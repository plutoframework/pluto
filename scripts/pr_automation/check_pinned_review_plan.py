"""Exercise the locked SDK planner and host coverage gate without inference."""

import asyncio
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def main():
    import sys

    if not sys.flags.isolated:
        raise RuntimeError("The pinned planner probe requires isolated Python")
    def load(name):
        path = Path(__file__).with_name(name + ".py")
        specification = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        return module

    review_diagnostics = load("review_diagnostics")
    reviewer_cli = load("reviewer_cli")
    from pr_agent.config_loader import get_settings
    from pr_agent.tools import pr_reviewer as sdk

    settings = get_settings()
    model = review_diagnostics.selected_model()
    changes = {
        "config.model": model,
        "config.max_model_tokens": 96000,
        "config.max_output_tokens": 16384,
        "config.reasoning_effort": "high",
        "config.publish_output_progress": False,
        "pr_reviewer.max_number_of_calls": 5,
        "pr_reviewer.enable_large_pr_chunking": False,
    }
    previous = {key: settings.get(key) for key in changes}
    for key, value in changes.items():
        settings.set(key, value)

    calls = []

    class Fixture(sdk.PRReviewer):
        def __init__(self):
            self.vars = {"extra_instructions": ""}
            self.token_handler = object()
            self.git_provider = SimpleNamespace(pr=object())

            async def completion(**kwargs):
                calls.append(True)
                return "synthetic", "stop"

            self.ai_handler = SimpleNamespace(chat_completion=completion)

        def _review_attempt_budget(self, selected):
            def fit(variables, name, text, **kwargs):
                return SimpleNamespace(
                    optional_text=text,
                    system_prompt="synthetic",
                    user_prompt="synthetic",
                )
            return SimpleNamespace(fit_prompt_variable=fit)

        def _load_valid_review_yaml(self, *args, **kwargs):
            return {}

        def _validate_review_schema(self, *args):
            return True

        def _merge_cached_review_chunks(self):
            return True

        def _chunk_progress_reporter(self, *args, **kwargs):
            return None

    async def exercise(directory):
        wrapped = reviewer_cli.attested_reviewer(
            Fixture,
            lambda provider: {"metadata_notes": []},
            Path(directory) / "input.json",
            lambda error: "synthetic failure",
        )
        results = []

        async def check(name, action, expected):
            calls.clear()
            rejected = False
            try:
                await action()
            except RuntimeError:
                rejected = True
            if len(calls) != expected or rejected != (expected == 0):
                raise AssertionError("Pinned planner case failed: " + name)
            results.append(name)

        async def prepared(remaining, chunked=False):
            reviewer = wrapped()
            settings.set("pr_reviewer.enable_large_pr_chunking", chunked)
            coverage = SimpleNamespace(
                chunks=["first synthetic diff", "second synthetic diff"],
                remaining_files_list=[],
                partial_files_list=[],
            )
            with (
                patch.object(sdk, "get_pr_diff", return_value=(
                    "synthetic diff", remaining,
                )),
                patch.object(sdk, "get_pr_multi_diffs", return_value=coverage),
                patch.object(sdk, "fit_related_tickets_to_prompt_budget",
                             return_value=(reviewer.vars, object())),
            ):
                await reviewer._prepare_prediction(model)

        await check("complete_single", lambda: prepared([]), 1)
        await check("public_omission", lambda: prepared(["omitted"]), 0)
        await check("complete_chunked", lambda: prepared(["packed"], True), 2)

        async def direct(kind):
            reviewer = wrapped()
            reviewer.remaining_files_list = []
            reviewer.partial_files_list = []
            reviewer.patches_diff = "synthetic diff"
            prompt = None
            if kind == "partial":
                reviewer.partial_files_list = ["partial"]
            elif kind == "absent_public":
                del reviewer.remaining_files_list
            elif kind == "malformed_public":
                reviewer.remaining_files_list = None
            elif kind == "absent_chunk_field":
                reviewer._chunked_patches_diff_list = ["synthetic diff"]
                prompt = "synthetic diff"
            elif kind == "malformed_chunk_field":
                reviewer._chunked_patches_diff_list = ["synthetic diff"]
                reviewer._chunked_remaining_files_list = None
                reviewer._chunked_partial_files_list = []
                prompt = "synthetic diff"
            elif kind in ("chunk_partial", "chunk_omission"):
                reviewer._chunked_patches_diff_list = ["synthetic diff"]
                reviewer._chunked_remaining_files_list = (
                    ["omitted"] if kind == "chunk_omission" else []
                )
                reviewer._chunked_partial_files_list = (
                    ["partial"] if kind == "chunk_partial" else []
                )
                prompt = "synthetic diff"
            elif kind == "invalid_chunks":
                reviewer._chunked_patches_diff_list = [None]
                reviewer._chunked_remaining_files_list = []
                reviewer._chunked_partial_files_list = []
                prompt = "synthetic diff"
            elif kind == "unsupported_prompt":
                prompt = "unknown synthetic diff"
            await reviewer._get_prediction(model, prompt)

        for name in (
            "partial", "absent_public", "malformed_public",
            "absent_chunk_field", "malformed_chunk_field", "chunk_partial",
            "chunk_omission", "invalid_chunks",
            "unsupported_prompt",
        ):
            await check(name, lambda name=name: direct(name), 0)
        return results

    def forbidden(*args, **kwargs):
        raise AssertionError("Network is forbidden in the pinned planner probe")

    try:
        with (
            tempfile.TemporaryDirectory(
                prefix="pluto-review-plan-"
            ) as directory,
            patch.object(sdk, "get_logger", return_value=MagicMock()),
            patch("socket.socket.connect", forbidden),
            patch("socket.create_connection", forbidden),
        ):
            cases = asyncio.run(exercise(directory))
    finally:
        for key, value in previous.items():
            settings.set(key, value)
    print(json.dumps({
        "schema": 1,
        "passed": cases,
        "live_model_calls": 0,
        "network_calls": 0,
    }))


if __name__ == "__main__":
    main()
