"""
J.A.R.V.I.S — FDS: 결정론적 규칙 엔진(Rules) + z-score 가중
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
해석 가능한 결정론적 규칙을 뼈대로, 통계적 이탈률(z-score)을 한 겹 얹는다.
각 규칙은 (event, collector, profile, config) → list[Signal] 을 반환.
가중치는 config.weights 에서 주입(테스트가 점수를 예측 가능).
"""
from __future__ import annotations
import time
from dataclasses import dataclass


@dataclass
class Signal:
    name: str
    weight: float
    detail: str = ""


def rule_threshold(ev, collector, profile, cfg) -> list[Signal]:
    """하드 임계 초과 금액 — 단독으로도 즉시 차단권으로 끌어올림."""
    if ev.amount and ev.amount >= cfg.amount_hard_cap:
        return [Signal("threshold", cfg.weights["threshold"],
                       f"amount {ev.amount:.0f} ≥ hard_cap {cfg.amount_hard_cap:.0f}")]
    return []


def rule_velocity(ev, collector, profile, cfg) -> list[Signal]:
    """속도: 윈도우 내 명령 폭주(매크로/거래/토큰 발급 급증)."""
    cnt = collector.count(ev.sub, cfg.velocity_window_sec, now=ev.ts)
    if cnt > cfg.velocity_max:
        return [Signal("velocity", cfg.weights["velocity"],
                       f"{cnt} cmds / {cfg.velocity_window_sec}s (>{cfg.velocity_max})")]
    return []


def rule_sequence(ev, collector, profile, cfg) -> list[Signal]:
    """시퀀스: 평소 안 하던 위험 명령 순서(예: 위험차단 해제 → 대량 매도)."""
    prev = collector.previous_command(ev.sub)
    if prev and (prev, ev.command) in cfg.dangerous_sequences:
        return [Signal("sequence", cfg.weights["sequence"],
                       f"dangerous transition {prev} → {ev.command}")]
    return []


def rule_new_device(ev, collector, profile, cfg) -> list[Signal]:
    """미사용 기기의 갑작스런 활성(이력 있는 주체에 한해)."""
    if ev.device_id and profile.seen(ev.sub) > 0 and not profile.is_known_device(ev.sub, ev.device_id):
        return [Signal("new_device", cfg.weights["new_device"],
                       f"unseen device {ev.device_id}")]
    return []


def rule_off_hours(ev, collector, profile, cfg) -> list[Signal]:
    """비정상 시간대 활동(설정된 off-hours 집합)."""
    hour = time.localtime(ev.ts).tm_hour
    if hour in cfg.off_hours:
        return [Signal("off_hours", cfg.weights["off_hours"], f"hour={hour}")]
    return []


def rule_amount_zscore(ev, collector, profile, cfg) -> list[Signal]:
    """통계적 이탈: 평소 금액 분포 대비 z-score 임계 초과."""
    if ev.amount and ev.amount > 0:
        z = profile.zscore(ev.sub, ev.amount)
        if z is not None and abs(z) >= cfg.z_threshold:
            return [Signal("amount_zscore", cfg.weights["amount_zscore"], f"z={z:.1f}")]
    return []


DEFAULT_RULES = [rule_threshold, rule_velocity, rule_sequence,
                 rule_new_device, rule_off_hours, rule_amount_zscore]
