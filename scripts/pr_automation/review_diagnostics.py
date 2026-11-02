"""Pinned-reviewer diagnostics without content or exception messages."""

import importlib
import inspect
import json
import os
from pathlib import Path
import sys
from types import FunctionType, MethodType

STAGES = {"request", "response", "planning", "parse"}
EVENTS = {"start", "ok", "error"}
FINISH = {"stop", "length", "content_filter", "tool_calls", "other"}
ERRORS = {
    "APIError", "EmptyTruncatedResponseError",
    "BadRequestError", "RateLimitError", "AuthenticationError",
    "APIConnectionError", "APITimeoutError", "ContextWindowExceededError",
    "InternalServerError", "ServiceUnavailableError",
    "ContentPolicyViolationError",
    "JSONDecodeError", "YAMLError", "ValidationError", "FallbackEligibleError",
    "RuntimeError", "ValueError", "TypeError", "KeyError",
    "TimeoutError", "other",
}


def safe_record(value):
    if not isinstance(value, dict):
        return None
    if (not isinstance(value.get("stage"), str)
            or not isinstance(value.get("event"), str)
            or value["stage"] not in STAGES or value["event"] not in EVENTS):
        return None
    result = {"stage": value["stage"], "event": value["event"]}
    if "exception" in value:
        kind = value["exception"]
        result["exception"] = (
            kind if isinstance(kind, str) and kind in ERRORS else "other"
        )
    status = value.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        result["http_status"] = status
    if "finish_reason" in value:
        reason = value["finish_reason"]
        result["finish_reason"] = (
            reason if isinstance(reason, str) and reason in FINISH else "other"
        )
    count = value.get("response_characters")
    if type(count) is int and 0 <= count <= 2**31 - 1:
        result["response_characters"] = count
    operation = value.get("operation")
    if isinstance(operation, str) and operation in {"prepare", "prediction"}:
        result["operation"] = operation
    return result


def safe_report(path):
    try:
        if path.stat().st_size > 65536:
            raise ValueError("Oversized diagnostics")
        report = json.loads(path.read_text())
        values = report["records"]
        if report.get("schema") != 1 or not isinstance(values, list):
            raise ValueError("Malformed diagnostics")
        records, counts = [], {}
        for value in values[:80]:
            item = safe_record(value)
            if item is not None and counts.get(item["stage"], 0) < 20:
                counts[item["stage"]] = counts.get(item["stage"], 0) + 1
                records.append(item)
        return {"schema": 1, "records": records}
    except (OSError, ValueError, TypeError, KeyError):
        return {"schema": 1, "records": [], "diagnostics": "unavailable"}


class Recorder:
    def __init__(self, path):
        self.path = path
        self.records = []
        self.counts = {}
        self.write()

    def write(self):
        try:
            self.path.write_text(json.dumps({
                "schema": 1, "records": self.records,
            }))
        except Exception:
            # Observability must not alter an SDK result or replace its error.
            pass

    def record(self, stage, event, **metadata):
        if self.counts.get(stage, 0) >= 20:
            return
        item = safe_record({"stage": stage, "event": event, **metadata})
        if item is not None:
            self.counts[stage] = self.counts.get(stage, 0) + 1
            self.records.append(item)
            self.write()

    def error(self, stage, error, **metadata):
        status = None
        try:
            status = getattr(error, "status_code", None)
            if status is None:
                status = getattr(getattr(error, "response", None),
                                 "status_code", None)
        except Exception:
            pass
        self.record(stage, "error", exception=type(error).__name__,
                    http_status=status, **metadata)


def diagnostic_reviewer(original, recorder):
    """Preserve SDK returns and exceptions while observing narrow boundaries."""
    class DiagnosticReviewer(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            completion = self.ai_handler.chat_completion
            async def chat(*args, **kwargs):
                recorder.record("request", "start")
                try:
                    result = await completion(*args, **kwargs)
                except Exception as error:
                    recorder.error("request", error)
                    raise
                response, reason = result
                recorder.record(
                    "response", "ok", finish_reason=reason,
                    response_characters=len(response)
                    if isinstance(response, str) else 0,
                )
                return result
            self.ai_handler.chat_completion = chat

        async def _get_prediction(self, *args, **kwargs):
            try:
                return await super()._get_prediction(*args, **kwargs)
            except Exception as error:
                recorder.error("planning", error, operation="prediction")
                raise

        async def _prepare_prediction(self, *args, **kwargs):
            recorder.record("planning", "start", operation="prepare")
            try:
                value = await super()._prepare_prediction(*args, **kwargs)
            except Exception as error:
                recorder.error("planning", error, operation="prepare")
                raise
            recorder.record("planning", "ok", operation="prepare")
            return value

        @classmethod
        def _load_valid_review_yaml(cls, *args, **kwargs):
            try:
                value = super()._load_valid_review_yaml(*args, **kwargs)
            except Exception as error:
                recorder.error("parse", error)
                raise
            recorder.record("parse", "ok")
            return value
    return DiagnosticReviewer



STREAMING_REVIEW_MODEL = "openai/openai/gpt-6-astra"


def completed_reviewer(original):
    """Use the pinned streaming transport and reject unfinished completions."""
    class CompletedReviewer(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            handler = self.ai_handler
            models = getattr(handler, "streaming_required_models", None)
            if (type(models) is not list
                    or any(type(m) is not str for m in models)):
                raise RuntimeError("Pinned streaming handler is incompatible")
            selected = models.copy()
            if STREAMING_REVIEW_MODEL not in selected:
                selected.append(STREAMING_REVIEW_MODEL)
            handler.streaming_required_models = selected
            completion = handler.chat_completion
            async def completed(*args, **kwargs):
                result = await completion(*args, **kwargs)
                _, finish_reason = result
                if type(finish_reason) is not str or finish_reason != "stop":
                    raise ValueError(
                        "Model completion did not finish with stop"
                    )
                return result
            handler.chat_completion = completed
    return CompletedReviewer


def single_attempt_reviewer(original):
    """Bind the exact pinned SDK body without its two retry-decorator layers."""
    class SingleAttemptReviewer(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            handler = self.ai_handler
            method = getattr(handler, "_chat_completion_with_retry", None)
            message = "Pinned single-attempt handler is incompatible"
            if (not isinstance(method, MethodType)
                    or method.__self__ is not handler):
                raise RuntimeError(message)
            outer = method.__func__
            middle = getattr(outer, "__wrapped__", None)
            body = getattr(middle, "__wrapped__", None)
            expected = (
                (outer, "async_wrapped", "/tenacity/_asyncio.py", True),
                (middle, "wrapped_f", "/tenacity/__init__.py", False),
                (body, "_chat_completion_with_retry",
                 "/pr_agent/algo/ai_handlers/litellm_ai_handler.py", True),
            )
            for function, name, suffix, asynchronous in expected:
                if (
                    not isinstance(function, FunctionType)
                    or function.__module__
                    != "pr_agent.algo.ai_handlers.litellm_ai_handler"
                    or function.__qualname__
                    != "LiteLLMAIHandler._chat_completion_with_retry"
                    or function.__code__.co_name != name
                    or not function.__code__.co_filename.replace("\\", "/")
                    .endswith(suffix)
                    or inspect.iscoroutinefunction(function) != asynchronous
                ):
                    raise RuntimeError(message)
            retry = getattr(outer, "retry", None)
            if (
                type(retry).__module__ != "tenacity._asyncio"
                or type(retry).__name__ != "AsyncRetrying"
                or getattr(middle, "retry", None) is not retry
                or hasattr(body, "__wrapped__")
                or inspect.unwrap(method) is not body
            ):
                raise RuntimeError(message)
            handler._chat_completion_with_retry = MethodType(body, handler)
    return SingleAttemptReviewer

def main():
    if not sys.flags.isolated:
        raise RuntimeError("The diagnostic launcher requires isolated Python")
    recorder = Recorder(Path(os.environ["PLUTO_REVIEW_DIAGNOSTICS_REPORT"]))
    tools = importlib.import_module("pr_agent.tools.pr_reviewer")
    wrapped = single_attempt_reviewer(completed_reviewer(
        diagnostic_reviewer(tools.PRReviewer, recorder)
    ))
    tools.PRReviewer = wrapped
    agent = importlib.import_module("pr_agent.agent.pr_agent")
    agent.PRReviewer = wrapped
    for command in ("review", "review_pr", "auto_review", "answer"):
        agent.command2class[command] = wrapped
    return importlib.import_module("pr_agent.cli").run()


if __name__ == "__main__":
    raise SystemExit(main())
