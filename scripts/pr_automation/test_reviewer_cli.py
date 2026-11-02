"""Exercise the trusted input guard without an SDK or paid inference."""

import asyncio
import json
import os
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from reviewer_cli import attested_reviewer, configure_snapshot_filter, main


class FakeReviewer:
    calls = 0

    def __init__(self):
        self.git_provider = object()
        self.vars = {"extra_instructions": "trusted policy"}

    async def _get_prediction(self, *_args, **_kwargs):
        self.calls += 1
        return "clean result"


class InputPlanTests(unittest.TestCase):
    def test_launcher_wraps_all_pinned_dispatch_references(self):
        tools = SimpleNamespace(
            PRReviewer=FakeReviewer, _review_failure_comment=lambda _: "safe"
        )
        agent = SimpleNamespace(
            PRReviewer=FakeReviewer,
            command2class={name: FakeReviewer for name in (
                "review", "review_pr", "auto_review", "answer"
            )},
        )
        modules = {
            "pr_agent.tools.pr_reviewer": tools,
            "pr_agent.agent.pr_agent": agent,
            "pr_agent.algo.language_handler": SimpleNamespace(),
            "pr_agent.cli": SimpleNamespace(run=lambda: 0),
        }
        flags = SimpleNamespace(**{
            name: value for name in dir(sys.flags)
            if isinstance(value := getattr(sys.flags, name), (int, bool))
        })
        flags.isolated = True
        with tempfile.TemporaryDirectory() as tmp, patch.dict(
            os.environ, {"PLUTO_REVIEW_INPUT_REPORT": f"{tmp}/input.json"}
        ), patch("reviewer_cli.sys.flags", flags), patch(
            "reviewer_cli.importlib.import_module",
            side_effect=modules.__getitem__,
        ):
            self.assertEqual(main(), 0)
        self.assertIsNot(tools.PRReviewer, FakeReviewer)
        self.assertIs(agent.PRReviewer, tools.PRReviewer)
        for target in agent.command2class.values():
            self.assertIs(target, tools.PRReviewer)

    def test_filename_cannot_exempt_changed_text_from_review(self):
        handler = SimpleNamespace(_is_valid_file=lambda *_args: False)
        configure_snapshot_filter(handler)
        for filename in (
            "go.sum", "package-lock.json", "bundle.min.js", "x.go"
        ):
            with self.subTest(filename=filename):
                self.assertTrue(handler._is_valid_file(filename, {"sum"}))
        self.assertFalse(handler._is_valid_file("", set()))

    def test_incomplete_plans_never_start_inference(self):
        for field in (
            "_chunked_remaining_files_list",
            "_chunked_partial_files_list",
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory(
            ) as tmp:
                path = Path(tmp) / "input.json"
                wrapped = attested_reviewer(
                    FakeReviewer,
                    lambda _: {"evidence": {"complete": True}},
                    path,
                    lambda _: "predefined safe reason",
                )
                reviewer = wrapped()
                setattr(reviewer, field, ["unassessed.go"])
                with self.assertRaisesRegex(RuntimeError, "Incomplete review"):
                    asyncio.run(reviewer._get_prediction("model"))
                self.assertEqual(reviewer.calls, 0)
                self.assertIn("input_error", json.loads(path.read_text()))

    def test_verified_empty_file_metadata_reaches_the_prompt(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            wrapped = attested_reviewer(
                FakeReviewer,
                lambda _: {
                    "evidence": {"complete": True},
                    "metadata_notes": ["Added zero-byte file: __init__.py"],
                },
                path,
                lambda _: "predefined safe reason",
            )
            reviewer = wrapped()
            self.assertIn("zero-byte", reviewer.vars["extra_instructions"])
            self.assertTrue(
                json.loads(path.read_text())["evidence"]["complete"]
            )
            self.assertEqual(
                asyncio.run(reviewer._get_prediction("model")), "clean result"
            )
            self.assertEqual(reviewer.calls, 1)

    def test_unverified_input_never_starts_inference(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"

            def reject(_provider):
                raise ValueError("synthetic input failure")

            wrapped = attested_reviewer(
                FakeReviewer, reject, path, lambda _: "predefined safe reason"
            )
            with self.assertRaises(ValueError):
                wrapped()
            self.assertEqual(FakeReviewer.calls, 0)
            self.assertIn("input_error", json.loads(path.read_text()))

    def test_provider_failure_records_only_the_sanitized_reason(self):
        class FailedReviewer(FakeReviewer):
            async def _get_prediction(self, *_args, **_kwargs):
                raise RuntimeError("provider details synthetic-secret")

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            wrapped = attested_reviewer(
                FailedReviewer,
                lambda _: {"evidence": {"complete": True}},
                path,
                lambda _: "The model provider timed out",
            )
            with self.assertRaises(RuntimeError):
                asyncio.run(wrapped()._get_prediction("model"))
            self.assertNotIn("synthetic-secret", path.read_text())
            self.assertEqual(
                json.loads(path.read_text())["errors"],
                ["The model provider timed out"],
            )
