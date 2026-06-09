"""
J.A.R.V.I.S — Alert Pipeline 통합 테스트 (기존 74+ 스위트와 독립 실행)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행: .venv/bin/python test_alert_pipeline.py

검증:
  · EventBridge risk_engine / fds 어댑터 정규화 + Severity
  · AlertDispatcher Severity 라우팅(overlay/tts/audio_cue/push)
  · SecureChannel OIDC+DeviceGuard 게이트 + SSRF 차단
  · AlertPipelineRuntime poll_idle 계약
"""
from __future__ import annotations

import time

import numpy as np

import risk_engine as re
from alert_pipeline import (
    EventBridge, AlertDispatcher, Severity, SecureChannel, SecureChannelError,
    AlertPipelineRuntime, RiskFeedAdapter, FDSFeedAdapter,
)
from fds import FDSEngine, FDSConfig, BehaviorEvent


def _synth_close(seed=0, vol=0.03, n=140):
    rng = np.random.default_rng(seed)
    return 100 * np.cumprod(1 + rng.normal(0, vol, n))


def test_bridge_risk_engine_severity():
    bridge = EventBridge(mdd_warn_pct=10, mdd_crit_pct=20)
    close = _synth_close(seed=1, vol=0.04)
    assessment = re.assess("NVDA", close, {"risk_level": "위험"}, score=80, base_ratio=0.2,
                           n_sims=2000, seed=1)
    ev = bridge.ingest_risk_assessment(
        "NVDA", assessment, name="엔비디아",
        macro_level="위험",
    )
    assert ev.source == "risk_engine"
    assert ev.severity in (Severity.WARNING, Severity.CRITICAL)
    assert ev.meta.get("overlay_table")
    print(f"✓ risk_engine → bridge [{ev.severity.name}] MDD≈{ev.risk_score:.0f}%")


def test_bridge_fds_anomaly():
    bridge = EventBridge()
    eng = FDSEngine(FDSConfig(amount_hard_cap=100_000_000, off_hours=frozenset()))
    d = eng.evaluate(BehaviorEvent(sub="u1", command="withdraw", device_id="d1", amount=500_000_000))
    ev = bridge.ingest_fds_decision(d)
    assert ev.source == "fds"
    assert ev.severity == Severity.CRITICAL
    assert "block" in ev.kind
    print(f"✓ fds block → bridge CRITICAL score={ev.risk_score}")


def test_dispatcher_severity_routing():
    overlay_hits = []
    tts_hits = []
    cue_hits = []
    push_hits = []

    disp = AlertDispatcher(
        overlay_sink=lambda x: overlay_hits.append(x),
        tts_sink=lambda t: tts_hits.append(t),
        audio_cue_sink=lambda c: cue_hits.append(c),
        push_sink=lambda t, ctx: push_hits.append((t, ctx)),
    )
    bridge = EventBridge()
    bridge.subscribe(disp.on_event)

    bridge.ingest_fds_decision(
        FDSEngine(FDSConfig(off_hours=frozenset())).evaluate(
            BehaviorEvent(sub="u", command="macro_run", device_id="d", amount=0)
        )
    )  # allow → INFO (if allow, ingest returns allow - skip)

    close = _synth_close(seed=2, vol=0.05)
    assessment = re.assess("TSLA", close, {"risk_level": "위험"}, score=90, base_ratio=0.25,
                           n_sims=3000, seed=2)
    bridge.ingest_risk_assessment("TSLA", assessment, macro_level="위험")

    assert len(overlay_hits) >= 1
    assert len(tts_hits) >= 1 or len(cue_hits) >= 1
    print(f"✓ dispatcher routing overlay={len(overlay_hits)} tts={len(tts_hits)} cue={len(cue_hits)}")


def test_secure_channel_device_binding():
    def verifier(token, scope):
        if token != "good":
            raise ValueError("bad token")
        if scope != "alert.push":
            raise ValueError("bad scope")
        return {"sub": "owner", "scope": scope}

    ch = SecureChannel("https://hooks.example.com/alert", token_verifier=verifier,
                       required_scope="alert.push", require_device_binding=True)

    try:
        ch.send({"text": "x"}, sender_token="good")
        assert False, "cnf.jkt 없으면 거부되어야 함"
    except SecureChannelError as e:
        assert e.stage == "device"
        print("✓ SecureChannel — cnf.jkt 없는 토큰 거부")

    def bound_verifier(token, scope):
        return {"sub": "owner", "scope": scope, "cnf": {"jkt": "abc123"}}

    ch2 = SecureChannel("https://hooks.example.com/alert", token_verifier=bound_verifier,
                        allow_hosts=frozenset({"hooks.example.com"}))
    meta = ch2.send({"text": "test"}, sender_token="ok")
    assert meta.get("dry_run") is True
    print("✓ SecureChannel — 바인딩 토큰 + allowlist dry_run 통과")


def test_fds_feed_adapter_attach():
    bridge = EventBridge()
    feed = FDSFeedAdapter(bridge)
    eng = FDSEngine(FDSConfig(amount_hard_cap=50_000_000, off_hours=frozenset()))
    feed.attach(eng)
    eng.evaluate(BehaviorEvent(sub="x", command="withdraw", device_id="d", amount=100_000_000))
    events = bridge.get_events()
    assert any(e.source == "fds" for e in events)
    print("✓ FDSFeedAdapter.attach — evaluate 래핑 후 bridge 수신")


def test_runtime_poll_idle():
    spoken = []
    rt = AlertPipelineRuntime()
    bridge = rt.bridge
    close = _synth_close(seed=3, vol=0.045)
    assessment = re.assess("AAPL", close, {"risk_level": "위험"}, score=85, base_ratio=0.2,
                           n_sims=2500, seed=3)
    bridge.ingest_risk_assessment("AAPL", assessment, macro_level="위험")
    out = rt.poll_idle(can_speak=True, on_speak=lambda m: spoken.append(m))
    assert spoken or rt.stats()["tts_pending"] == 0
    print(f"✓ runtime poll_idle — spoken={len(spoken)}")


def main():
    test_bridge_risk_engine_severity()
    test_bridge_fds_anomaly()
    test_dispatcher_severity_routing()
    test_secure_channel_device_binding()
    test_fds_feed_adapter_attach()
    test_runtime_poll_idle()
    print("\n✅ Alert Pipeline 6/6 통과")


if __name__ == "__main__":
    main()
