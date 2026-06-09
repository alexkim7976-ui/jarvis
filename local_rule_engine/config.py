"""local_rule_engine — 임계치·라우팅 설정."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class LocalRuleConfig:
    enabled: bool = True
    rsi_oversold: float = 30.0
    rsi_overbought: float = 70.0
    change_spike_pct: float = 5.0
    require_anomaly_for_gemini: bool = True
    local_period: str = "3mo"


def get_config() -> LocalRuleConfig:
    def _f(key: str, default: float) -> float:
        try:
            return float(os.getenv(key, str(default)))
        except ValueError:
            return default

    enabled = os.getenv("LOCAL_RULE_ENGINE_ENABLED", "1").strip().lower() not in (
        "0", "false", "off",
    )
    require = os.getenv("LOCAL_RULE_REQUIRE_ANOMALY_FOR_GEMINI", "1").strip().lower() not in (
        "0", "false", "off",
    )
    return LocalRuleConfig(
        enabled=enabled,
        rsi_oversold=_f("LOCAL_RULE_RSI_OVERSOLD", 30.0),
        rsi_overbought=_f("LOCAL_RULE_RSI_OVERBOUGHT", 70.0),
        change_spike_pct=_f("LOCAL_RULE_CHANGE_SPIKE_PCT", 5.0),
        require_anomaly_for_gemini=require,
        local_period=os.getenv("LOCAL_RULE_FETCH_PERIOD", "3mo"),
    )
