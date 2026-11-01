import os
import unittest
from unittest.mock import patch
from review import environment


class ReviewSettingsTests(unittest.TestCase):
    def test_gateway_and_policy_cannot_be_overridden_by_repository(self):
        with patch.dict(
            os.environ,
            {
                "GPUROUTER_API_KEY": "synthetic",
                "REVIEW_TOKEN": "synthetic",
                "BOT_LOGIN": "jackbot-advrsty",
                "CONFIG__USE_REPO_SETTINGS_FILE": "true",
                "CONFIG__FALLBACK_MODELS": '["weak-model"]',
            },
        ):
            env = environment()
        self.assertEqual(env["OPENAI__API_BASE"], "https://gpurouter.io/v1")
        self.assertEqual(env["CONFIG__MODEL"], "openai/openai/gpt-6-astra")
        self.assertEqual(env["CONFIG__USE_REPO_SETTINGS_FILE"], "false")
        self.assertEqual(env["CONFIG__FALLBACK_MODELS"], "[]")
        self.assertEqual(env["CONFIG__REASONING_EFFORT"], "high")
        self.assertEqual(env["CONFIG__PROPAGATE_TOOL_ERRORS"], "true")
        self.assertEqual(env["PR_REVIEWER__PUBLISH_ERROR_DETAILS"], "true")


if __name__ == "__main__":
    unittest.main()
