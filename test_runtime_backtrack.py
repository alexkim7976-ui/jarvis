"""실행 경로: 맥 CPU, 백엔드, 오프라인 백트래킹."""
import os
import unittest

from runtime_backtrack import (
    BacktrackSkip,
    apply_runtime_policy,
    describe_error,
    guard_tool,
    lane_for_tool,
    main_uses_cpu,
    offline_reply,
    run_backtrack,
)


class RuntimeBacktrackTest(unittest.TestCase):
    def test_empty_error_is_readable(self):
        text = describe_error(RuntimeError())
        self.assertIn("RuntimeError", text)
        self.assertNotEqual(text.strip(), "RuntimeError:")

    def test_backtrack_skips_failed_step(self):
        def fail():
            raise RuntimeError()

        result = run_backtrack("stt", [("backend", fail), ("mac_cpu", lambda: "안녕")])
        self.assertTrue(result.ok)
        self.assertEqual(result.route, "mac_cpu")
        self.assertEqual(result.value, "안녕")
        self.assertIn("RuntimeError", result.attempts[0].detail)

    def test_unconfigured_backend_is_not_a_failure(self):
        result = run_backtrack(
            "quote",
            [("backend", lambda: (_ for _ in ()).throw(BacktrackSkip("백엔드 URL 없음"))),
             ("mac_cpu", lambda: {"success": True})],
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.route, "mac_cpu")

    def test_other_tools_use_backend_lane(self):
        self.assertEqual(lane_for_tool("analyze_stock_investment"), "other")
        self.assertEqual(lane_for_tool("web_search"), "other")
        self.assertEqual(lane_for_tool("get_system_status"), "main")

    def test_guard_returns_local_when_backend_missing(self):
        os.environ.pop("JARVIS_BACKEND_URL", None)
        out = guard_tool("analyze_stock_news", {}, lambda: {"success": True, "message": "로컬"})
        self.assertEqual(out["message"], "로컬")

    def test_guard_uses_offline_after_local_crash(self):
        def boom():
            raise OSError("disk")

        out = guard_tool("get_system_status", {}, boom)
        self.assertTrue(out.get("offline"))
        self.assertFalse(out.get("success"))

    def test_offline_clock(self):
        text = offline_reply("지금 몇 시야")
        self.assertIsNotNone(text)
        self.assertIn("지금은", text)

    def test_cpu_policy_overrides_metal(self):
        saved = {k: os.environ.get(k) for k in (
            "JARVIS_MAIN_DEVICE", "JARVIS_WHISPER_BACKEND", "JARVIS_STT_PROCESS", "OLLAMA_NUM_GPU",
        )}
        try:
            os.environ["JARVIS_MAIN_DEVICE"] = "cpu"
            os.environ["JARVIS_WHISPER_BACKEND"] = "mlx"
            os.environ["JARVIS_STT_PROCESS"] = "1"
            os.environ["OLLAMA_NUM_GPU"] = "-1"
            self.assertTrue(main_uses_cpu())
            apply_runtime_policy()
            self.assertEqual(os.environ["JARVIS_WHISPER_BACKEND"], "faster")
            self.assertEqual(os.environ["JARVIS_STT_PROCESS"], "0")
            self.assertEqual(os.environ["OLLAMA_NUM_GPU"], "0")
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
