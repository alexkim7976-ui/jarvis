"""
speech_gate — 전역 음성 게이트(한 번에 하나만 말하기) 단위 테스트.
목소리 겹침(동시 발화) 방지 계약을 회귀 보호한다.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import speech_gate as g


def _reset():
    g.reset_speech_gate()


def test_basic_acquire_release():
    _reset()
    assert g.speech_active() is False
    assert g.begin_speech(wait=False) is True
    assert g.speech_active() is True
    # 한 번에 하나만 — 두 번째 비차단 획득은 실패
    assert g.begin_speech(wait=False) is False
    g.end_speech()
    assert g.speech_active() is False
    print("✓ 획득/반환 + 단일 점유")


def test_end_speech_no_underflow():
    _reset()
    # 점유하지 않은 상태에서 end_speech 반복 호출해도 음수/오류 없음
    g.end_speech()
    g.end_speech()
    assert g.speech_active() is False
    assert g.begin_speech(wait=False) is True
    g.end_speech()
    assert g.speech_active() is False
    print("✓ 미점유 end_speech 언더플로우 없음")


def test_serialization_no_overlap():
    """여러 스레드가 동시에 말하려 해도 임계구역엔 항상 1개만 — 겹침 없음."""
    _reset()
    active = {"n": 0}
    overlap = {"bad": False}
    order = []
    lock = threading.Lock()

    def worker(tag):
        assert g.begin_speech(timeout=5)
        with lock:
            active["n"] += 1
            if active["n"] > 1:
                overlap["bad"] = True
        time.sleep(0.03)
        with lock:
            active["n"] -= 1
        order.append(tag)
        g.end_speech()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert overlap["bad"] is False, "동시 발화(목소리 겹침) 발생!"
    assert len(order) == 5
    assert g.speech_active() is False
    print("✓ 다중 스레드 직렬화 — 동시 발화 없음")


def test_reset_recovers_stuck_gate():
    """워치독 시나리오 — 반환되지 않은 게이트를 강제 초기화해 이후 발화 가능."""
    _reset()
    assert g.begin_speech(wait=False) is True   # 점유 후 반환하지 않음(고착 모사)
    assert g.begin_speech(wait=False) is False
    g.reset_speech_gate()
    assert g.speech_active() is False
    assert g.begin_speech(wait=False) is True
    g.end_speech()
    print("✓ reset_speech_gate 고착 복구")


def test_wait_audio_idle_respects_playback():
    _reset()

    class _FakePlayer:
        def __init__(self):
            self.is_playing = True

    p = _FakePlayer()
    # 재생 중이면 타임아웃까지 대기 후 False
    t0 = time.time()
    assert g.wait_audio_idle(player=p, timeout=0.2) is False
    assert time.time() - t0 >= 0.2
    # 재생 종료되면 즉시 True
    p.is_playing = False
    assert g.wait_audio_idle(player=p, timeout=1.0) is True
    print("✓ wait_audio_idle 재생 상태 반영")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"\n{len(fns)}/{len(fns)} passed")
