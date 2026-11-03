"""부팅 인사 — 환율만(종목·하이닉스 제외) + 시스템 리소스 한 줄 (5개 언어 지원)."""
from __future__ import annotations

import os
from i18n import tr


def startup_fx_enabled() -> bool:
    return os.getenv("JARVIS_STARTUP_FX_BRIEF", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def startup_resource_voice_enabled() -> bool:
    return os.getenv("JARVIS_STARTUP_RESOURCE_VOICE", "1").strip().lower() in (
        "1", "true", "yes", "on",
    )


def build_startup_fx_briefing() -> str:
    """USD/KRW exchange rate only (localized spoken & HUD text)."""
    try:
        from stock_quant import MACRO_WATCH_TICKERS, _fetch_macro_indicator
    except ImportError:
        return ""
    item = _fetch_macro_indicator("USDKRW=X")
    if not item:
        return tr("fx_unavailable")
    rate = item.get("value")
    chg = item.get("change_pct_1m")
    meta = MACRO_WATCH_TICKERS.get("USDKRW=X", {})
    warn = meta.get("warning", 1350)
    danger = meta.get("danger", 1400)
    tone = tr("fx_tone_stable")
    if rate is not None:
        if rate >= danger:
            tone = tr("fx_tone_danger")
        elif rate >= warn:
            tone = tr("fx_tone_warn")
    chg_phrase = ""
    if chg is not None:
        chg_phrase = tr("fx_chg_tpl", chg=f"{chg:+.2f}%")
    if rate is None:
        return tr("fx_unavailable")
    return tr("fx_briefing_tpl", rate=f"{rate:,.0f}", chg=chg_phrase, tone=tone)


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
        items["1M"] = f"{chg:+.2f}%"
    if not items:
        return None
    title = tr("fx_title")
    banner = f"💱 USD/KRW {rate:,.0f} KRW" if rate is not None else f"💱 {title}"
    return {
        "type": "info",
        "title": title,
        "items": items,
        "alert_text": banner,
        "alert_message": msg,
    }


def build_startup_resource_line() -> str:
    try:
        from hud_resources import get_resource_sampler

        cpu, mem = get_resource_sampler().snapshot()
        return tr("resource_line_tpl", cpu=f"{cpu:.0f}", mem=f"{mem:.0f}")
    except Exception:
        try:
            from system_monitor import get_cpu_usage, get_memory_usage

            c = get_cpu_usage()
            m = get_memory_usage()
            if c.get("success") and m.get("success"):
                return tr(
                    "resource_line_tpl",
                    cpu=str(c.get("cpu_percent", "?")).rstrip("%"),
                    mem=str(m.get("percent", "?")).rstrip("%"),
                )
        except Exception:
            pass
        return ""
