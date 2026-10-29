"""부팅 인사 — 환율만(종목·하이닉스 제외) + 시스템 리소스 한 줄."""
from __future__ import annotations

import os


def startup_fx_enabled() -> bool:
    return os.getenv("JARVIS_STARTUP_FX_BRIEF", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def startup_resource_voice_enabled() -> bool:
    return os.getenv("JARVIS_STARTUP_RESOURCE_VOICE", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def build_startup_fx_briefing() -> str:
    """USD/KRW exchange rate only (spoken & HUD text)."""
    try:
        from stock_quant import MACRO_WATCH_TICKERS, _fetch_macro_indicator
    except ImportError:
        return ""
    item = _fetch_macro_indicator("USDKRW=X")
    if not item:
        return "I was unable to retrieve the USD to KRW exchange rate at the moment. Please check again shortly."
    rate = item.get("value")
    chg = item.get("change_pct_1m")
    meta = MACRO_WATCH_TICKERS.get("USDKRW=X", {})
    warn = meta.get("warning", 1350)
    danger = meta.get("danger", 1400)
    tone = "It remains relatively stable."
    if rate is not None:
        if rate >= danger:
            tone = "It has crossed 1,400 won, indicating an elevated risk zone."
        elif rate >= warn:
            tone = "The US dollar is currently in a strong range."
    chg_phrase = ""
    if chg is not None:
        chg_phrase = f" That is {chg:+.2f}% over the past month."
    if rate is None:
        return "I could not verify the USD to KRW exchange rate data."
    return f"The USD to KRW exchange rate is {rate:,.0f} won.{chg_phrase} {tone}"


def build_startup_fx_overlay() -> dict | None:
    """HUD panel for USD/KRW exchange rate."""
    try:
        from stock_quant import _fetch_macro_indicator
    except ImportError:
        return None
    item = _fetch_macro_indicator("USDKRW=X")
    if not item:
        return None
    rate = item.get("value")
    chg = item.get("change_pct_1m")
    msg = build_startup_fx_briefing()
    items: dict[str, str] = {}
    if rate is not None:
        items["USD/KRW"] = f"{rate:,.0f} KRW"
    if chg is not None:
        items["1M Change"] = f"{chg:+.2f}%"
    if not items:
        return None
    banner = f"💱 USD/KRW {rate:,.0f} KRW" if rate is not None else "💱 USD/KRW Exchange Rate"
    return {
        "type": "info",
        "title": "USD/KRW Exchange Rate",
        "items": items,
        "alert_text": banner,
        "alert_message": msg,
    }


def build_startup_resource_line() -> str:
    try:
        from hud_resources import get_resource_sampler

        cpu, mem = get_resource_sampler().snapshot()
        return f"Mac CPU usage is at {cpu:.0f} percent, and memory usage is at {mem:.0f} percent."
    except Exception:
        try:
            from system_monitor import get_cpu_usage, get_memory_usage

            c = get_cpu_usage()
            m = get_memory_usage()
            if c.get("success") and m.get("success"):
                return (
                    f"Mac CPU is at {c.get('cpu_percent', '?')} and "
                    f"memory is at {m.get('percent', '?')}."
                )
        except Exception:
            pass
        return ""
