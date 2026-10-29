import os
import tempfile
import unittest
from pathlib import Path

from performance_presets import (
    PRESET_BALANCED,
    PRESET_ECO,
    apply_preset,
    current_profile,
    interpret_performance_command,
    read_env_value,
    upsert_env_keys,
)


class TestPerformancePresets(unittest.TestCase):
    def test_apply_eco(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            env.write_text("# test\n", encoding="utf-8")
            msg = apply_preset(PRESET_ECO, env)
            self.assertIn("절전", msg)
            self.assertEqual(read_env_value("JARVIS_CAM_WIDTH", env), "640")
            self.assertEqual(current_profile(env), PRESET_ECO)

    def test_voice_command(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            env.write_text("JARVIS_PERF_PROFILE=balanced\n", encoding="utf-8")
            os.environ["JARVIS_DATA_DIR"] = td
            try:
                reply = interpret_performance_command("성능 균형")
                self.assertIsNotNone(reply)
                self.assertIn("균형", reply or "")
            finally:
                os.environ.pop("JARVIS_DATA_DIR", None)

    def test_upsert(self):
        with tempfile.TemporaryDirectory() as td:
            env = Path(td) / ".env"
            upsert_env_keys({"FOO": "bar", "JARVIS_PERF_PROFILE": "balanced"}, env)
            self.assertEqual(read_env_value("FOO", env), "bar")


if __name__ == "__main__":
    unittest.main()
