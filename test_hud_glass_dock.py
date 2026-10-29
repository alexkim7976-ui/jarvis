"""Liquid glass HUD dock smoke test."""
import numpy as np

from hud_glass_dock import draw_glass_power_chip, draw_glass_quick_dock


def test_draw_dock_no_crash():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    frame[:] = (28, 32, 40)
    draw_glass_quick_dock(
        frame,
        chat_open=True,
        calendar_open=False,
        settings_open=True,
        location_active=True,
    )
    draw_glass_power_chip(frame, False)
    assert frame.shape == (480, 640, 3)
