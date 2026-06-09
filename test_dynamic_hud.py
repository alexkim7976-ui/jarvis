"""
동적 HUD(아이언맨 스타일) 검증 — 제스처 레이더 팝업 / 매크로 테마 스위칭 / 위험 보더
실행: .venv/bin/python test_dynamic_hud.py

· 제스처(OPEN_PALM 홀드) 시뮬레이션 → 테이블 접힘 + 레이더 팝업 애니메이션
· 매크로 등급(안정/경계/위험) 강제 스위칭 → 테마 색/보더 반응
· 렌더 FPS 측정(백엔드 분리·부드러움 확인)
· 단계별 프레임 PNG 시각 덤프(hud_dump/) + 로그 리포트
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import numpy as np
import cv2

from screen_overlay import ScreenOverlay, THEME_BGR, HUD_STYLE, _hex_to_bgr

DUMP = Path("hud_dump"); DUMP.mkdir(exist_ok=True)
W, H = 1280, 720


def _frame():
    f = np.zeros((H, W, 3), dtype=np.uint8)
    f[:] = (18, 14, 10)   # 어두운 배경
    return f


def _matrix_table():
    return {
        "type": "table",
        "title": "INVESTMENT MATRIX | 엔비디아 | 91/100 강력매수",
        "headers": ["레이어", "점수/상태", "세부"],
        "rows": [
            ["QUANT TECH", "42/50", "매수 9개/매도 2개"],
            ["WHALE FLOW", "20/25", "엘리트 매수"],
            ["SOCIAL HYPE", "19/25", "건전 촉매"],
            ["COMPOSITE", "91/100", "강력매수"],
            ["FINAL ALLOC", "2.0%", "매크로: 위험 ⚠OVERRIDE"],
        ],
        "theme": "위험",
    }


def _central_brightness(frame):
    cy, cx = H // 2, W // 2
    roi = frame[cy - 120:cy + 120, cx - 120:cx + 120]
    return float(roi.mean())


def test_style_guide_modularized():
    assert THEME_BGR["안정"] == _hex_to_bgr("#00F0FF") == (255, 240, 0)
    assert THEME_BGR["경계"] == _hex_to_bgr("#FFAA00") == (0, 170, 255)
    assert THEME_BGR["위험"] == _hex_to_bgr("#FF0055") == (85, 0, 255)
    assert "anim" in HUD_STYLE and "radar" in HUD_STYLE
    print("✓ 전역 스타일 가이드 모듈화 + 헥사코드 일치 (안정/경계/위험)")


def test_radar_extracted_from_matrix():
    ov = ScreenOverlay()
    ov.set_data(_matrix_table(), duration=30)
    assert ov._radar_data and len(ov._radar_data) == 3
    labels = {a["label"] for a in ov._radar_data}
    assert labels == {"기술적 분석", "고래 플로우", "소셜 하이프"}, labels
    print("✓ 매트릭스 표 → 레이더 3축 자동 추출:", [f"{a['label']} {a['value']:.0f}/{a['max']:.0f}" for a in ov._radar_data])


def test_gesture_radar_popup_animation():
    ov = ScreenOverlay(); ov.set_theme("위험")
    ov.set_data(_matrix_table(), duration=30)

    base = ov.render(_frame())
    base_bright = _central_brightness(base)
    cv2.imwrite(str(DUMP / "01_table_only.png"), base)

    # 제스처: 손바닥 펼치기 홀드 → 레이더 토글 ON
    evt = ov.handle_gesture("OPEN_PALM", held=True)
    assert evt and evt[0] == "RADAR_TOGGLE" and evt[1] is True, evt

    # 프레임 기반 애니메이션 진행 (≈0.6초)
    mid_saved = False
    for i in range(40):
        fr = ov.render(_frame())
        if not mid_saved and ov._radar_anim >= 0.4:
            cv2.imwrite(str(DUMP / "02_collapsing_popup.png"), fr); mid_saved = True
        time.sleep(0.016)
    full = ov.render(_frame())
    cv2.imwrite(str(DUMP / "03_radar_full.png"), full)

    assert ov._radar_anim > 0.9, ov._radar_anim
    assert ov._table_collapse > 0.9, ov._table_collapse
    full_bright = _central_brightness(full)
    assert full_bright > base_bright + 3, (base_bright, full_bright)
    # 위험 테마 → 레이더에 크림슨(R) 존재
    cy, cx = H // 2, W // 2
    assert int(full[cy - 120:cy + 120, cx - 120:cx + 120, 2].max()) > 120
    print(f"✓ 제스처 레이더 팝업: 중앙밝기 {base_bright:.1f}→{full_bright:.1f}, "
          f"radar_anim={ov._radar_anim:.2f}, collapse={ov._table_collapse:.2f}")

    # 다시 토글 → 접힘 복귀
    ov.handle_gesture("OPEN_PALM", held=True)
    for _ in range(40):
        ov.render(_frame()); time.sleep(0.012)
    assert ov._radar_anim < 0.1, ov._radar_anim
    print("✓ 재토글 시 레이더 접힘 + 테이블 복원 (radar_anim→0)")


def test_macro_theme_switch_and_danger_border():
    results = {}
    for lv in ("안정", "경계", "위험"):
        ov = ScreenOverlay(); ov.set_theme(lv)
        # 위험 보더는 알파 페이드라 펄스 위상에 따라 픽셀이 변하므로 여러 프레임 중 최대 적색 채택
        rmax = 0
        for _ in range(20):
            fr = ov.render(_frame())
            rmax = max(rmax, int(fr[0:6, :, 2].max()))
            time.sleep(0.01)
        cv2.imwrite(str(DUMP / f"theme_{HUD_STYLE['theme'][lv]['name']}.png"), fr)
        results[lv] = rmax
    # 위험만 강한 적색 경고 보더
    assert results["위험"] > 150, results
    assert results["안정"] < 80, results
    print(f"✓ 매크로 테마/보더: 안정 R={results['안정']} · 경계 R={results['경계']} · 위험 R={results['위험']} (페이드 펄스)")


def test_render_fps_smooth():
    ov = ScreenOverlay(); ov.set_theme("위험")
    ov.set_data(_matrix_table(), duration=60)
    ov.show_radar()
    N = 200
    t0 = time.time()
    for _ in range(N):
        ov.render(_frame())
    dt = time.time() - t0
    fps = N / dt
    assert fps >= 60, f"렌더 FPS 미달: {fps:.0f}"
    print(f"✓ 렌더 성능 {fps:.0f} FPS (레이더+보더 동시 / 목표 60+ 충족)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n✅ ALL {len(tests)} TESTS PASSED")
    print(f"📁 시각 덤프 저장: {DUMP.resolve()}")
    for p in sorted(DUMP.glob('*.png')):
        print("   -", p.name)
