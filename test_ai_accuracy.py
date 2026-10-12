"""AI 정확도·라우팅 테스트."""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME
import os
import unittest


class TestAiAccuracy(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._saved)

    def test_validate_numbers_detects_hallucination(self):
        from ai_accuracy import validate_narrative_numbers
        ctx = {"consensus_price": 72000, "rsi": 55.2}
        ok, bad = validate_narrative_numbers(
            f"{USER_DISPLAY_NAME}, 합의가 72000원이며 RSI 55입니다.", ctx,
        )
        self.assertTrue(ok)
        ok2, bad2 = validate_narrative_numbers(
            f"{USER_DISPLAY_NAME}, 합의가 999999원입니다.", ctx,
        )
        self.assertFalse(ok2)
        self.assertTrue(bad2)

    def test_ollama_accuracy_options_low_temp(self):
        os.environ["OLLAMA_TEMPERATURE"] = "0.1"
        from ai_accuracy import ollama_accuracy_options
        opts = ollama_accuracy_options()
        self.assertEqual(opts["temperature"], 0.1)
        self.assertIn("num_ctx", opts)

    def test_hybrid_routes_news_to_analyzer(self):
        os.environ["HYBRID_ROUTING_ENABLED"] = "1"
        from hybrid_router import try_local_tool_execution
        out = try_local_tool_execution("analyze_stock_news", {})
        self.assertIsInstance(out, dict)
        self.assertFalse(out.get("success"))

    def test_hybrid_multi_source_not_hijacked(self):
        os.environ["HYBRID_ROUTING_ENABLED"] = "1"
        from hybrid_router import try_local_tool_execution
        out = try_local_tool_execution(
            "analyze_stock_multi_source", {"query": "삼성전자"},
        )
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
