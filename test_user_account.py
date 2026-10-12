"""로그인 계정은 소스 밖에만 저장된다."""
import os
import unittest
from pathlib import Path

from user_account import apply_profile, authenticate, register_account


class UserAccountTest(unittest.TestCase):
    def setUp(self):
        self.root = Path("data") / "_account_test"
        self.root.mkdir(parents=True, exist_ok=True)
        os.environ["JARVIS_PROJECT_ROOT"] = str(self.root)
        for key in (
            "JARVIS_USER_NAME", "CAMPUS_GMAIL_ACCOUNT", "CAMPUS_COURSE_SENDER",
            "CAMPUS_CALL_TO", "JARVIS_DEVICE_LOCATION",
        ):
            os.environ.pop(key, None)

    def tearDown(self):
        path = self.root / "data" / "accounts.json"
        if path.exists():
            path.unlink()
        os.environ.pop("JARVIS_PROJECT_ROOT", None)

    def test_register_and_login_applies_profile(self):
        err = register_account("ada", "pass", {
            "display_name": "에이다",
            "email": "ada@example.com",
            "campus_email": "ada@school.example",
            "phone": "+10000000000",
            "city": "서울",
        })
        self.assertIsNone(err)
        self.assertIsNone(authenticate("ada", "wrong"))
        profile = authenticate("ada", "pass")
        self.assertIsNotNone(profile)
        apply_profile(profile)
        self.assertEqual(os.environ["JARVIS_USER_NAME"], "에이다")
        self.assertEqual(os.environ["CAMPUS_GMAIL_ACCOUNT"], "ada@school.example")
        self.assertEqual(os.environ["JARVIS_DEVICE_LOCATION"], "서울")
        stored = (self.root / "data" / "accounts.json").read_text(encoding="utf-8")
        self.assertNotIn('"pass"', stored)
        self.assertIn("password_hash", stored)


if __name__ == "__main__":
    unittest.main()
