"""Require a terminal completion before review parsing or publication."""

import asyncio
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from test_review_diagnostics import bind_fake_retry_method

from review_diagnostics import (
    Recorder, completed_reviewer, diagnostic_reviewer,
    main,
)

DEFAULTS = ["openai/qwq-plus"]
CONTENT = (
    "review:\n  risk_level: low\n"
    "  merge_recommendation: no_concerns_found\n"
    "  key_issues_to_review: []\n  security_concerns: No\n"
)


class FakeReviewer:
    def __init__(self):
        self.parsed = False
        self.reason = "stop"
        self.error = None
        self.ai_handler = SimpleNamespace(
            streaming_required_models=DEFAULTS, chat_completion=self.complete,
        )
        bind_fake_retry_method(self.ai_handler)

    async def complete(self):
        if self.error is not None:
            raise self.error
        return CONTENT, self.reason

    async def _get_prediction(self):
        content, _ = await self.ai_handler.chat_completion()
        self.parsed = True
        return {"review": content}


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.model = patch.dict(os.environ, {
            "PLUTO_REVIEW_MODEL": "openai/openai/gpt-6-astra",
        })
        self.model.start()
        self.addCleanup(self.model.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "diagnostics.json"
        self.recorder = Recorder(self.path)
        self.wrapped = completed_reviewer(
            diagnostic_reviewer(FakeReviewer, self.recorder)
        )

    def test_stop_preserves_review_and_streaming_defaults(self):
        reviewer = self.wrapped()
        self.assertEqual(asyncio.run(reviewer._get_prediction()),
                         {"review": CONTENT})
        self.assertTrue(reviewer.parsed)
        self.assertEqual(reviewer.ai_handler.streaming_required_models,
                         DEFAULTS + ["openai/openai/gpt-6-astra"])
        self.assertEqual(DEFAULTS, ["openai/qwq-plus"])
        self.assertIsNot(reviewer.ai_handler.streaming_required_models,
                         DEFAULTS)

    def test_non_stop_valid_looking_reviews_never_reach_parsing(self):
        for reason in (
            None, "", "length", "content_filter", "tool_calls", True,
        ):
            with self.subTest(reason=reason):
                reviewer = self.wrapped()
                reviewer.reason = reason
                with self.assertRaisesRegex(ValueError, "finish with stop"):
                    asyncio.run(reviewer._get_prediction())
                self.assertFalse(reviewer.parsed)

    def test_original_transport_exception_is_preserved(self):
        reviewer = self.wrapped()
        original = RuntimeError("synthetic transport failure")
        reviewer.error = original
        with self.assertRaises(RuntimeError) as caught:
            asyncio.run(reviewer._get_prediction())
        self.assertIs(caught.exception, original)
        self.assertFalse(reviewer.parsed)

    def test_diagnostic_io_remains_optional_for_completed_stream(self):
        with patch.object(Path, "write_text", side_effect=OSError("quota")):
            self.assertEqual(asyncio.run(self.wrapped()._get_prediction()),
                             {"review": CONTENT})

    def test_unknown_sdk_streaming_configuration_fails_before_request(self):
        for models in (None, "model", ("model",), [True]):
            class Incompatible(FakeReviewer):
                def __init__(self):
                    super().__init__()
                    self.ai_handler.streaming_required_models = models
            with self.subTest(models=models), self.assertRaisesRegex(
                RuntimeError, "Pinned streaming handler is incompatible"
            ):
                completed_reviewer(Incompatible)()

    def test_composition_keeps_observer_and_guard_separate(self):
        observed = diagnostic_reviewer(FakeReviewer, self.recorder)
        reviewer = observed()
        reviewer.reason = "length"
        self.assertEqual(asyncio.run(reviewer._get_prediction()),
                         {"review": CONTENT})
        guarded = completed_reviewer(observed)()
        guarded.reason = "length"
        with self.assertRaises(ValueError):
            asyncio.run(guarded._get_prediction())
        self.assertFalse(guarded.parsed)


    def test_main_binds_every_alias_to_guarded_diagnostic_composition(self):
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
        self.assertEqual(tools.PRReviewer.__mro__[0].__name__,
                         "SingleAttemptReviewer")
        self.assertEqual(tools.PRReviewer.__mro__[1].__name__,
                         "CompletedReviewer")
        self.assertEqual(tools.PRReviewer.__mro__[2].__name__,
                         "DiagnosticReviewer")
        for command in ("review", "review_pr", "auto_review", "answer"):
            self.assertIs(agent.command2class[command], tools.PRReviewer)
            reviewer = agent.command2class[command]()
            reviewer.reason = None
            with self.assertRaises(ValueError):
                asyncio.run(reviewer._get_prediction())
            self.assertFalse(reviewer.parsed)
