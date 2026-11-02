"""Bind pinned PR-Agent input and reject incomplete plans before inference."""

import importlib
import importlib.util
import json
import os
from pathlib import Path
import sys


def _write_report(path, report):
    path.write_text(json.dumps(report, sort_keys=True))


def configure_snapshot_filter(language_handler):
    # Pinned PR-Agent otherwise always skips checksums and generated filenames,
    # even with empty extension settings. The host verifies every changed blob
    # as text before inference; a filename cannot exempt it from review.
    language_handler._is_valid_file = lambda filename, _excluded: bool(filename)


def planned_coverage(reviewer, args, kwargs):
    """Require explicit coverage for the pinned single-call or chunked path."""
    public = [getattr(reviewer, name, None) for name in (
        "remaining_files_list", "partial_files_list",
    )]
    if any(type(value) is not list for value in public):
        raise ValueError("Pinned review plan coverage is incompatible")
    fields = (
        "_chunked_patches_diff_list", "_chunked_remaining_files_list",
        "_chunked_partial_files_list",
    )
    has_chunk_plan = any(hasattr(reviewer, name) for name in fields)
    prompt = args[1] if len(args) > 1 else kwargs.get("patches_diff")
    if has_chunk_plan or prompt is not None:
        chunks, omitted, partial = (
            getattr(reviewer, name, None) for name in fields
        )
        if (type(chunks) is not list or not chunks
                or any(type(chunk) is not str or not chunk for chunk in chunks)
                or type(omitted) is not list or type(partial) is not list):
            raise ValueError("Pinned chunk plan coverage is incompatible")
        if prompt is not None:
            if type(prompt) is not str or prompt not in chunks:
                raise ValueError("Prediction does not match the chunk plan")
            return omitted, partial
        return public[0] + omitted, public[1] + partial
    return public


def attested_reviewer(original, attest, report_path, failure_reason):
    """Wrap only the input boundary; the pinned reviewer owns its review."""
    class AttestedReviewer(original):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.input_report = {"schema": 1, "errors": []}
            try:
                self.input_report.update(attest(self.git_provider))
                empty = self.input_report.get("evidence", {}).get("empty", [])
                if type(empty) is not list:
                    raise ValueError("Invalid verified empty-file evidence")
                if empty:
                    self.vars["extra_instructions"] += (
                        "\nVerified zero-byte added-file count: "
                        + str(len(empty)) + "."
                    )
                _write_report(report_path, self.input_report)
            except Exception:
                self.input_report["input_error"] = (
                    "Repository input could not be verified before inference."
                )
                _write_report(report_path, self.input_report)
                raise

        async def _get_prediction(self, *args, **kwargs):
            try:
                omitted, partial = planned_coverage(self, args, kwargs)
            except ValueError:
                self.input_report["input_error"] = (
                    "The pinned review plan has no compatible coverage proof."
                )
                _write_report(report_path, self.input_report)
                raise RuntimeError("Incomplete review input plan") from None
            if omitted or partial:
                self.input_report["input_error"] = (
                    "The planned review omits or clips files; no inference "
                    "is permitted for this incomplete plan."
                )
                self.input_report["omitted"] = list(omitted)
                self.input_report["partial"] = list(partial)
                _write_report(report_path, self.input_report)
                raise RuntimeError("Incomplete review input plan")
            try:
                return await super()._get_prediction(*args, **kwargs)
            except Exception as error:
                # The pinned classifier emits only predefined safe reasons.
                self.input_report["errors"].append(failure_reason(error))
                _write_report(report_path, self.input_report)
                raise

    return AttestedReviewer


def main():
    # Isolated Python avoids policy/github.py shadowing the pinned PyGithub SDK.
    if not sys.flags.isolated:
        raise RuntimeError("The reviewer adapter requires isolated Python")
    path = Path(os.environ["PLUTO_REVIEW_INPUT_REPORT"])
    specification = importlib.util.spec_from_file_location(
        "pluto_review_inputs", Path(__file__).with_name("review_inputs.py")
    )
    inputs = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = inputs
    specification.loader.exec_module(inputs)
    configure_snapshot_filter(
        importlib.import_module("pr_agent.algo.language_handler")
    )
    reviewer = importlib.import_module("pr_agent.tools.pr_reviewer")
    specification = importlib.util.spec_from_file_location(
        "pluto_review_diagnostics",
        Path(__file__).with_name("review_diagnostics.py"),
    )
    diagnostics = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = diagnostics
    specification.loader.exec_module(diagnostics)
    recorder = diagnostics.Recorder(Path(
        os.environ["PLUTO_REVIEW_DIAGNOSTICS_REPORT"]
    ))
    observed = diagnostics.diagnostic_reviewer(reviewer.PRReviewer, recorder)
    completed = diagnostics.completed_reviewer(observed)
    single = diagnostics.single_attempt_reviewer(completed)
    wrapped = attested_reviewer(
        single,
        inputs.attest_provider,
        path,
        reviewer._review_failure_comment,
    )
    reviewer.PRReviewer = wrapped
    # Cover the agent's imported class alias as well as the tools module.
    agent = importlib.import_module("pr_agent.agent.pr_agent")
    agent.PRReviewer = wrapped
    for command in ("review", "review_pr", "auto_review", "answer"):
        agent.command2class[command] = wrapped
    cli = importlib.import_module("pr_agent.cli")
    return cli.run()


if __name__ == "__main__":
    raise SystemExit(main())
