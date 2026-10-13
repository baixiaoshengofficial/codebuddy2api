import json
import os
import tempfile
import unittest

from src.codebuddy_token_manager import CodeBuddyTokenManager


class CredentialSelectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        for index in range(3):
            path = os.path.join(self.directory.name, f"credential_{index}.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"bearer_token": f"token-{index}", "user_id": f"user-{index}"}, handle)
        self.manager = CodeBuddyTokenManager(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def test_stays_on_current_credential_until_credit_failure(self):
        self.assertEqual([self.manager.get_next_credential()["_credential_index"] for _ in range(4)], [0] * 4)
        self.manager.mark_credential_exhausted(0)
        self.assertEqual(self.manager.get_next_credential()["_credential_index"], 1)
        self.assertEqual(CodeBuddyTokenManager(self.directory.name).get_next_credential()["_credential_index"], 1)

    def test_exhausted_credentials_are_not_reused_when_all_fail(self):
        for index in range(3):
            self.manager.mark_credential_exhausted(index)
        self.assertIsNone(self.manager.get_next_credential())
        self.assertTrue(self.manager.all_available_credentials_exhausted())
        self.assertIsNone(CodeBuddyTokenManager(self.directory.name).get_next_credential())

    def test_manual_selection_remains_locked(self):
        self.manager.set_mode("manual", 0)
        self.manager.mark_credential_exhausted(0)
        self.assertEqual(self.manager.get_next_credential()["_credential_index"], 0)


if __name__ == "__main__":
    unittest.main()
