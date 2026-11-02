"""Safe diagnostics distinguish stages without changing reviewer decisions."""

import asyncio
import contextlib
import functools
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import MethodType, SimpleNamespace
import unittest
from unittest.mock import patch

from review import run
from review_diagnostics import (
    Recorder, diagnostic_reviewer, main, safe_report, single_attempt_reviewer,
)

SECRET = "credential-and-private-response"


class MemoryReport:
    def __init__(self, value):
        self.text = json.dumps(value)

    def stat(self):
        return SimpleNamespace(st_size=len(self.text.encode()))

    def read_text(self):
        return self.text


def bind_fake_retry_method(handler):
    """Model the locked two-layer decorator shape using only the stdlib."""
    async def _chat_completion_with_retry(self, *args, **kwargs):
        return await self.transport(*args, **kwargs)
    body = _chat_completion_with_retry
    body.__module__ = "pr_agent.algo.ai_handlers.litellm_ai_handler"
    body.__qualname__ = "LiteLLMAIHandler._chat_completion_with_retry"
    body.__code__ = body.__code__.replace(co_filename=(
        "/fixture/pr_agent/algo/ai_handlers/litellm_ai_handler.py"
    ))
    @functools.wraps(body)
    def wrapped_f(self, *args, **kwargs):
        async def retry():
            try:
                return await body(self, *args, **kwargs)
            except Exception as error:
                if getattr(error, "has_headers", False) or isinstance(
                    error, TimeoutError
                ):
                    raise
                return await body(self, *args, **kwargs)
        return retry()
    wrapped_f.__code__ = wrapped_f.__code__.replace(
        co_filename="/fixture/tenacity/__init__.py"
    )
    retry_type = type("AsyncRetrying", (), {"__module__": "tenacity._asyncio"})
    wrapped_f.retry = retry_type()
    @functools.wraps(wrapped_f)
    async def async_wrapped(self, *args, **kwargs):
        return await wrapped_f(self, *args, **kwargs)
    async_wrapped.__code__ = async_wrapped.__code__.replace(
        co_filename="/fixture/tenacity/_asyncio.py"
    )
    handler._chat_completion_with_retry = MethodType(async_wrapped, handler)
    return handler


class RetryReviewer:
    def __init__(self):
        self.calls = []
        self.failure = None
        handler = SimpleNamespace(deployment_id="unchanged")
        async def transport(**kwargs):
            self.calls.append(kwargs)
            if self.failure is not None:
                raise self.failure
            return "unchanged result", "stop"
        handler.transport = transport
        bind_fake_retry_method(handler)
        async def chat(**kwargs):
            return await handler._chat_completion_with_retry(**kwargs)
        handler.chat_completion = chat
        self.ai_handler = handler

    async def predict(self):
        return await self.ai_handler.chat_completion(
            model="openai/openai/gpt-6-astra", reasoning_effort="high",
            max_completion_tokens=16384,
        )


class SingleAttemptTests(unittest.TestCase):
    def test_one_body_invocation_for_502_headers_timeout_and_success(self):
        for kind in ("502_no_headers", "502_headers", "timeout", "success"):
            reviewer = single_attempt_reviewer(RetryReviewer)()
            if kind.startswith("502"):
                failure = BadRequestError("synthetic transport failure")
                failure.status_code = 502
                failure.has_headers = kind == "502_headers"
            elif kind == "timeout":
                failure = TimeoutError("synthetic timeout")
            else:
                failure = None
            reviewer.failure = failure
            with self.subTest(kind=kind):
                if failure is None:
                    self.assertEqual(asyncio.run(reviewer.predict()),
                                     ("unchanged result", "stop"))
                else:
                    with self.assertRaises(type(failure)) as caught:
                        asyncio.run(reviewer.predict())
                    self.assertIs(caught.exception, failure)
                self.assertEqual(len(reviewer.calls), 1)
                self.assertEqual(reviewer.calls[0], {
                    "model": "openai/openai/gpt-6-astra",
                    "reasoning_effort": "high", "max_completion_tokens": 16384,
                })
                self.assertEqual(reviewer.ai_handler.deployment_id, "unchanged")

    def test_full_binding_leaves_wrappers_intact_and_one_unwrap_retries(self):
        baseline = RetryReviewer()
        method = baseline.ai_handler._chat_completion_with_retry
        baseline.ai_handler._chat_completion_with_retry = MethodType(
            method.__wrapped__, baseline.ai_handler
        )
        baseline.failure = RuntimeError("synthetic failure")
        with self.assertRaises(RuntimeError):
            asyncio.run(baseline.predict())
        self.assertEqual(len(baseline.calls), 2)
        original = RetryReviewer()
        outer = original.ai_handler._chat_completion_with_retry.__func__
        middle = outer.__wrapped__
        body = middle.__wrapped__
        class Existing:
            def __init__(self):
                self.ai_handler = original.ai_handler
        wrapped = single_attempt_reviewer(Existing)()
        self.assertIs(wrapped.ai_handler._chat_completion_with_retry.__func__,
                      body)
        self.assertIs(outer.__wrapped__, middle)
        self.assertIs(middle.__wrapped__, body)

    def test_incompatible_retry_shapes_fail_before_any_transport(self):
        for kind in ("missing", "one_layer", "extra_layer", "wrong_body"):
            original = RetryReviewer()
            handler = original.ai_handler
            outer = handler._chat_completion_with_retry.__func__
            if kind == "missing":
                del handler._chat_completion_with_retry
            elif kind == "one_layer":
                outer.__wrapped__ = outer.__wrapped__.__wrapped__
            elif kind == "extra_layer":
                outer.__wrapped__.__wrapped__.__wrapped__ = lambda: None
            else:
                body = outer.__wrapped__.__wrapped__
                body.__code__ = body.__code__.replace(co_filename="/wrong.py")
            class Existing:
                def __init__(self):
                    self.ai_handler = handler
            with self.subTest(kind=kind), self.assertRaisesRegex(
                RuntimeError, "Pinned single-attempt handler is incompatible"
            ):
                single_attempt_reviewer(Existing)()
            self.assertEqual(original.calls, [])


class BadRequestError(RuntimeError):
    status_code = 400


class FakeReviewer:
    def __init__(self):
        self.ai_handler = SimpleNamespace(chat_completion=self.complete)

    async def complete(self, **_kwargs):
        return SECRET, "length"

    async def _get_prediction(self, *_args, **_kwargs):
        return await self.ai_handler.chat_completion(system=SECRET, user=SECRET)

    async def _prepare_prediction(self, *args, **kwargs):
        return await self._get_prediction(*args, **kwargs)

    @classmethod
    def _load_valid_review_yaml(cls, prediction, *, source="model response"):
        if prediction == "invalid":
            raise ValueError(SECRET)
        return {"review": prediction, "class": cls.__name__}


class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "diagnostics.json"
        self.recorder = Recorder(self.path)
        self.wrapped = diagnostic_reviewer(FakeReviewer, self.recorder)

    def report(self):
        return safe_report(self.path)["records"]

    def test_length_response_counts_characters_without_recording_content(self):
        reviewer = self.wrapped()
        self.assertEqual(asyncio.run(reviewer._prepare_prediction("model")),
                         (SECRET, "length"))
        response = next(r for r in self.report() if r["stage"] == "response")
        self.assertEqual(response["finish_reason"], "length")
        self.assertEqual(response["response_characters"], len(SECRET))
        self.assertNotIn(SECRET, self.path.read_text())

    def test_http_error_metadata_survives_without_raw_message(self):
        async def failing(**_kwargs):
            raise BadRequestError(SECRET)
        class Failed(FakeReviewer):
            complete = staticmethod(failing)
        wrapped = diagnostic_reviewer(Failed, self.recorder)
        with self.assertRaises(BadRequestError):
            asyncio.run(wrapped()._prepare_prediction("model"))
        request = next(r for r in self.report() if r.get("http_status"))
        self.assertEqual(request["stage"], "request")
        self.assertEqual(request["exception"], "BadRequestError")
        self.assertEqual(request["http_status"], 400)
        self.assertNotIn(SECRET, self.path.read_text())

    def test_truncation_and_generic_api_types_remain_distinct(self):
        for name in ("EmptyTruncatedResponseError", "APIError"):
            kind = type(name, (RuntimeError,), {})
            async def failed(**_kwargs):
                raise kind(SECRET)
            class Failed(FakeReviewer):
                complete = staticmethod(failed)
            wrapped = diagnostic_reviewer(Failed, self.recorder)
            with self.assertRaises(kind):
                asyncio.run(wrapped()._get_prediction("model"))
            errors = [r for r in self.report() if r["stage"] == "request"
                      and r["event"] == "error"]
            self.assertEqual(errors[-1]["exception"], name)
            self.assertNotIn(SECRET, self.path.read_text())

    def test_planning_before_request_and_classmethod_parse_are_distinct(self):
        class Planning(FakeReviewer):
            async def _get_prediction(self, *_args, **_kwargs):
                raise RuntimeError(SECRET)
        wrapped = diagnostic_reviewer(Planning, self.recorder)
        with self.assertRaises(RuntimeError):
            asyncio.run(wrapped()._prepare_prediction("model"))
        self.assertFalse(any(r["stage"] == "request" for r in self.report()))
        with self.assertRaises(ValueError):
            wrapped._load_valid_review_yaml("invalid", source=SECRET)
        self.assertTrue(any(r["stage"] == "parse" for r in self.report()))
        self.assertEqual(wrapped._load_valid_review_yaml("ok")["class"],
                         "DiagnosticReviewer")
        self.assertNotIn(SECRET, self.path.read_text())

    def test_report_sanitizer_bounds_and_revalidates_every_field(self):
        for stage in ("request", "response", "planning", "parse"):
            for _ in range(100):
                self.recorder.record(stage, "error", exception=SECRET,
                                     http_status=True, response=SECRET,
                                     finish_reason=SECRET)
        records = self.report()
        self.assertEqual(len(records), 80)
        self.assertNotIn(SECRET, self.path.read_text())
        hostile = MemoryReport({"schema": 1, "records": [{
            "stage": "request", "event": "error", "exception": SECRET,
            "http_status": 999, "response": SECRET, "prompt": SECRET,
        }]})
        filtered = safe_report(hostile)
        self.assertNotIn(SECRET, json.dumps(filtered))
        self.assertNotIn("http_status", filtered["records"][0])

    def test_launcher_wraps_every_review_dispatch_alias(self):
        tools = SimpleNamespace(PRReviewer=FakeReviewer)
        agent = SimpleNamespace(PRReviewer=FakeReviewer, command2class={})
        modules = {
            "pr_agent.tools.pr_reviewer": tools,
            "pr_agent.agent.pr_agent": agent,
            "pr_agent.cli": SimpleNamespace(run=lambda: 0),
        }
        with (
            patch.dict(os.environ, {
                "PLUTO_REVIEW_DIAGNOSTICS_REPORT": str(self.path)
            }),
            patch("review_diagnostics.sys.flags", SimpleNamespace(isolated=1)),
            patch("review_diagnostics.importlib.import_module",
                  side_effect=modules.__getitem__),
        ):
            self.assertEqual(main(), 0)
        self.assertIs(agent.PRReviewer, tools.PRReviewer)
        for name in ("review", "review_pr", "auto_review", "answer"):
            self.assertIs(agent.command2class[name], tools.PRReviewer)

    def test_failed_process_prints_only_safe_metadata_and_stays_failed(self):
        output = self.path.with_name("result.json")
        hostile = MemoryReport({"schema": 1, "records": [{
            "stage": "request", "event": "error", "exception": SECRET,
            "http_status": 400, "response": SECRET,
        }]})
        def failed(command, **kwargs):
            self.assertEqual(command[1], "-I")
            self.assertIn("PLUTO_REVIEW_DIAGNOSTICS_REPORT", kwargs["env"])
            return SimpleNamespace(returncode=1)
        stream = io.StringIO()
        with (
            patch.dict(os.environ, {"PR_AGENT_SOURCE": str(self.path.parent)}),
            patch("review.environment", return_value={}),
            patch("review.subprocess.run", side_effect=failed),
            patch(
                "review.safe_report",
                side_effect=lambda _: safe_report(hostile),
            ),
            contextlib.redirect_stdout(stream),
        ):
            with self.assertRaises(RuntimeError):
                run("https://github.com/example/repo/pull/1", output)
        self.assertNotIn(SECRET, stream.getvalue())
        self.assertEqual(json.loads(stream.getvalue())["records"][0]
                         ["http_status"], 400)

    def test_successful_process_preserves_result_without_printing(self):
        output = self.path.with_name("result.json")
        expected = {"review": {"value": SECRET}}
        stream = io.StringIO()
        with (
            patch.dict(os.environ, {"PR_AGENT_SOURCE": str(self.path.parent)}),
            patch("review.environment", return_value={}),
            patch(
                "review.subprocess.run",
                return_value=SimpleNamespace(returncode=0),
            ),
            patch.object(Path, "is_file", return_value=True),
            patch.object(Path, "read_text", side_effect=lambda path: json.dumps(
                {"evidence": {"complete": True}}
                if path.name.endswith(".input.json") else expected
            ), autospec=True),
            contextlib.redirect_stdout(stream),
        ):
            self.assertEqual(run("https://example.test/pr/1", output), {
                **expected, "input_evidence": {"complete": True},
            })
        self.assertEqual(stream.getvalue(), "")
        self.assertFalse(output.exists())

    def test_write_failure_never_masks_sdk_results_or_original_exceptions(self):
        async def failed(**_kwargs):
            raise original
        class Failed(FakeReviewer):
            complete = staticmethod(failed)
        with patch.object(Path, "write_text", side_effect=OSError("quota")):
            recorder = Recorder(self.path)
            wrapped = diagnostic_reviewer(FakeReviewer, recorder)
            self.assertEqual(
                asyncio.run(wrapped()._prepare_prediction("model")),
                (SECRET, "length"),
            )
            self.assertEqual(wrapped._load_valid_review_yaml("ok")["review"],
                             "ok")
            with self.assertRaises(ValueError):
                wrapped._load_valid_review_yaml("invalid")
            original = BadRequestError(SECRET)
            wrapped = diagnostic_reviewer(Failed, recorder)
            with self.assertRaises(BadRequestError) as caught:
                asyncio.run(wrapped()._prepare_prediction("model"))
            self.assertIs(caught.exception, original)
