import unittest

from power_save_mode import (
    interpret_power_save_command,
    is_active,
    set_power_save,
    wants_power_save_off,
    wants_power_save_on,
)


class TestPowerSaveMode(unittest.TestCase):
    def tearDown(self):
        import os
        os.environ.pop("JARVIS_POWER_SAVE", None)

    def test_voice_intents(self):
        self.assertTrue(wants_power_save_on("절전 모드 켜줘"))
        self.assertTrue(wants_power_save_off("절전 모드 꺼"))
        self.assertIsNotNone(interpret_power_save_command("절전 모드 상태"))

    def test_env_toggle(self):
        import os
        os.environ["OLLAMA_NUM_PREDICT"] = "1536"
        msg = set_power_save(True)
        self.assertIn("켰", msg)
        self.assertTrue(is_active())
        self.assertEqual(os.getenv("OLLAMA_NUM_PREDICT"), "2048")
        off = set_power_save(False)
        self.assertIn("껐", off)
        self.assertFalse(is_active())
        self.assertEqual(os.getenv("OLLAMA_NUM_PREDICT"), "1536")


if __name__ == "__main__":
    unittest.main()
