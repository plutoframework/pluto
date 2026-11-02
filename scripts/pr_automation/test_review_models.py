"""Protected operator selection and model-bound cached evidence."""

import asyncio
import copy
import json
import os
import unittest
from unittest.mock import patch

from controller import previous_result, review_scope
from policy import REVIEW_CHECK
from review import environment
from review_diagnostics import REVIEW_MODELS, completed_reviewer, selected_model
from test_controller import FakeAPI
from test_policy import CLEAN
from test_review_completion import CONTENT, FakeReviewer


class ModelConfigurationTests(unittest.TestCase):
    def test_default_and_both_explicit_routes_preserve_policy(self):
        for choice in ("", *REVIEW_MODELS):
            with self.subTest(choice=choice), patch.dict(os.environ, {
                "PLUTO_REVIEW_MODEL": choice,
                "GPUROUTER_API_KEY": "synthetic",
                "REVIEW_TOKEN": "synthetic",
                "BOT_LOGIN": "bot",
                "CONFIG__MODEL": "untrusted/model",
            }):
                chosen = choice or REVIEW_MODELS[1]
                env = environment()
                self.assertEqual(selected_model(), chosen)
                self.assertEqual(env["CONFIG__MODEL"], chosen)
                for key in ("ADDITIONAL_REASONING_EFFORT_MODELS",
                            "NO_TEMPERATURE_MODELS"):
                    self.assertEqual(json.loads(env["CONFIG__" + key]),
                                     [chosen])
                for key, value in {
                    "REASONING_EFFORT": "high", "MAX_MODEL_TOKENS": "96000",
                    "MAX_OUTPUT_TOKENS": "16384", "FALLBACK_MODELS": "[]",
                    "NUM_RETRIES": "0",
                    "RETRY_SAME_MODEL_ON_TIMEOUT": "false",
                }.items():
                    self.assertEqual(env["CONFIG__" + key], value)
                self.assertEqual(env["PR_REVIEWER__MAX_NUMBER_OF_CALLS"], "5")
                instance = completed_reviewer(FakeReviewer)()
                self.assertIn(chosen,
                              instance.ai_handler.streaming_required_models)
                self.assertEqual(asyncio.run(instance._get_prediction()),
                                 {"review": CONTENT})

    def test_unknown_selection_rejects_before_transport(self):
        for choice in ("unknown", " openai/anthropic/claude-opus-5.5",
                       "openai/anthropic/claude-opus-5.5 ", "null"):
            with self.subTest(choice=choice), patch.dict(os.environ, {
                "PLUTO_REVIEW_MODEL": choice,
            }):
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    selected_model()
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    environment()
                with self.assertRaisesRegex(ValueError, "Unsupported"):
                    completed_reviewer(FakeReviewer)()

    def test_model_change_invalidates_signed_cached_review(self):
        api = FakeAPI()
        with patch.dict(os.environ, {
            "POLICY_SHA": "policy", "BOT_LOGIN": "bot",
            "PLUTO_REVIEW_MODEL": REVIEW_MODELS[0],
        }):
            evidence = {**review_scope(api.pr), "result": copy.deepcopy(CLEAN)}
            item = {"id": 1, "name": REVIEW_CHECK, "app": {"id": 123},
                    "conclusion": "success",
                    "output": {"text": json.dumps(evidence)}}
            with patch.object(api, "pages", return_value=[item]):
                self.assertEqual(previous_result(api, "/repos/test", api.pr,
                                                 123), CLEAN)
                with patch.dict(os.environ, {
                    "PLUTO_REVIEW_MODEL": REVIEW_MODELS[1],
                }):
                    self.assertIsNone(previous_result(api, "/repos/test",
                                                      api.pr, 123))
                del evidence["model"]
                item["output"]["text"] = json.dumps(evidence)
                self.assertIsNone(previous_result(api, "/repos/test", api.pr,
                                                  123))

    def test_only_trusted_repository_variable_is_forwarded(self):
        from rollout import entrypoint
        source = entrypoint("pluto", "a" * 40)
        self.assertIn("PLUTO_REVIEW_MODEL: ${{ vars.PLUTO_REVIEW_MODEL }}",
                      source)
        self.assertNotIn("inputs.review_model", source)


if __name__ == "__main__":
    unittest.main()
