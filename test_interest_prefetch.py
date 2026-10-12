"""관심 미리받기와 백그라운드 켜기/끄기. 네트워크와 카메라는 쓰지 않는다."""
from __future__ import annotations

import os
import tempfile
import unittest

import numpy as np


class PrefetchControlTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._prev = os.environ.get("JARVIS_PROJECT_ROOT")
        os.environ["JARVIS_PROJECT_ROOT"] = self._tmp.name
        for key in ("JARVIS_INTERESTS", "NEWS_WATCHLIST", "WATCH_LIST"):
            os.environ.pop(key, None)

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("JARVIS_PROJECT_ROOT", None)
        else:
            os.environ["JARVIS_PROJECT_ROOT"] = self._prev
        self._tmp.cleanup()

    def test_cache_hit_for_matching_interest(self):
        import interest_prefetch as ip

        self.assertIn("추가", ip.add_interest("엔비디아"))
        ip.remember("엔비디아 주가", "web", {"success": True, "answer": "캐시", "query": "엔비디아"})
        hit = ip.cached_search("엔비디아 최근 소식", "web")
        self.assertIsNotNone(hit)
        self.assertTrue(hit["cached"])
        self.assertEqual(hit["answer"], "캐시")
        self.assertIsNone(ip.cached_search("오늘 날씨", "web"))

    def test_commands_add_topic_and_toggle(self):
        import background_control as bc

        state = {"on": True}

        def start():
            state["on"] = True

        def stop():
            state["on"] = False

        bc.bind_task("news", start, stop, lambda: state["on"])
        added = bc.interpret_control_command("관심 추가 삼성전자")
        self.assertIn("추가", added)
        self.assertIn("삼성전자", bc.interpret_control_command("관심 목록"))

        off = bc.interpret_control_command("뉴스 백그라운드 꺼")
        self.assertIn("껐", off)
        self.assertFalse(bc.is_enabled("news"))
        self.assertFalse(state["on"])

        on = bc.interpret_control_command("뉴스 백그라운드 켜")
        self.assertIn("켰", on)
        self.assertTrue(bc.is_enabled("news"))
        self.assertIsNone(bc.interpret_control_command("오늘 뉴스 알려줘"))

    def test_web_search_returns_fresh_cache_without_network(self):
        import interest_prefetch as ip
        import web_search

        ip.add_interest("삼성전자")
        ip.remember("삼성전자", "web", {"success": True, "answer": "미리받음", "results": []})

        def boom(*_a, **_k):
            raise AssertionError("캐시가 있으면 인터넷을 다시 치면 안 됩니다")

        web_search.duckduckgo_search = boom
        out = web_search.web_search("삼성전자 실적")
        self.assertTrue(out.get("cached"))
        self.assertEqual(out["answer"], "미리받음")

    def test_panel_draws(self):
        import background_control as bc

        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        drawn = bc.draw_background_panel(frame, True)
        self.assertEqual(drawn.shape, frame.shape)


if __name__ == "__main__":
    unittest.main()
