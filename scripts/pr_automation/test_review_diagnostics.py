"""Safe diagnostics distinguish stages without changing reviewer decisions."""

import asyncio
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from review import run
from review_diagnostics import Recorder, diagnostic_reviewer, main, safe_report

SECRET = "credential-and-private-response"


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
        self.path.write_text(json.dumps({"schema": 1, "records": [{
            "stage": "request", "event": "error", "exception": SECRET,
            "http_status": 999, "response": SECRET, "prompt": SECRET,
        }]}))
        self.assertNotIn(SECRET, json.dumps(safe_report(self.path)))
        self.assertNotIn("http_status", self.report()[0])

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
        def failed(command, **kwargs):
            self.assertEqual(command[1], "-I")
            report = Path(kwargs["env"]["PLUTO_REVIEW_DIAGNOSTICS_REPORT"])
            report.write_text(json.dumps({"schema": 1, "records": [{
                "stage": "request", "event": "error", "exception": SECRET,
                "http_status": 400, "response": SECRET,
            }]}))
            return SimpleNamespace(returncode=1)
        stream = io.StringIO()
        with patch.dict(os.environ, {
            "PR_AGENT_SOURCE": str(self.path.parent)
        }), \
                patch("review.environment", return_value={}), \
                patch("review.subprocess.run", side_effect=failed), \
                contextlib.redirect_stdout(stream):
            with self.assertRaises(RuntimeError):
                run("https://github.com/example/repo/pull/1", output)
        self.assertNotIn(SECRET, stream.getvalue())
        self.assertEqual(json.loads(stream.getvalue())["records"][0]
                         ["http_status"], 400)

    def test_successful_process_preserves_result_without_printing(self):
        output = self.path.with_name("result.json")
        expected = {"review": {"value": SECRET}}
        def complete(_command, **_kwargs):
            output.write_text(json.dumps(expected))
            return SimpleNamespace(returncode=0)
        stream = io.StringIO()
        with patch.dict(os.environ, {"PR_AGENT_SOURCE": str(self.path.parent)}), \
                patch("review.environment", return_value={}), \
                patch("review.subprocess.run", side_effect=complete), \
                contextlib.redirect_stdout(stream):
            self.assertEqual(run("https://example.test/pr/1", output), expected)
        self.assertEqual(stream.getvalue(), "")
