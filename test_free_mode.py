"""무료 모드 단위 테스트."""
from __future__ import annotations

import os
import unittest


class TestFreeMode(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_free_only_defaults(self):
        os.environ["JARVIS_FREE_ONLY"] = "1"
        os.environ.pop("JARVIS_NO_GEMINI", None)
        os.environ.pop("LLM_PROVIDER", None)
        from free_mode import apply_free_mode_defaults, free_only_enabled
        self.assertTrue(free_only_enabled())
        apply_free_mode_defaults()
        self.assertEqual(os.environ.get("JARVIS_NO_GEMINI"), "1")
        self.assertEqual(os.environ.get("LLM_PROVIDER"), "ollama")

    def test_stt_forced_local(self):
        os.environ["JARVIS_FREE_ONLY"] = "1"
        os.environ["JARVIS_STT_BACKEND"] = "api"
        from stt_backend import resolve_stt_mode
        self.assertEqual(resolve_stt_mode("sk-test"), "local")

    def test_web_search_free(self):
        os.environ["JARVIS_FREE_ONLY"] = "1"
        os.environ.pop("TAVILY_API_KEY", None)
        from web_search import web_search
        # 네트워크 없어도 Tavily 키 없이 DuckDuckGo 경로로 진입하는지 확인
        out = web_search("Python programming", max_results=2)
        self.assertIn("success", out)
        if out.get("success"):
            self.assertEqual(out.get("source"), "duckduckgo")

    def test_no_gemini_in_free_mode(self):
        os.environ["JARVIS_FREE_ONLY"] = "1"
        from hybrid_router import no_gemini_mode
        self.assertTrue(no_gemini_mode())


class TestOllamaVision(unittest.TestCase):
    def test_vision_model_env(self):
        os.environ["OLLAMA_VISION_MODEL"] = "llama3.2-vision"
        import importlib
        import ollama_vision
        importlib.reload(ollama_vision)
        self.assertEqual(ollama_vision.OLLAMA_VISION_MODEL, "llama3.2-vision")

    def test_stock_model_helper(self):
        os.environ["OLLAMA_STOCK_MODEL"] = "qwen2.5:7b-instruct"
        import importlib
        import ollama_agent
        importlib.reload(ollama_agent)
        self.assertEqual(ollama_agent.get_stock_model(), "qwen2.5:7b-instruct")


if __name__ == "__main__":
    unittest.main()
