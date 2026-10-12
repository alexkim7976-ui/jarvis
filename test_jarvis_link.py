"""
JARVIS Link 검증 — 웹소켓 HUD 스트림 / 원격 리밸런싱 승인 / 모바일 푸시 / 인증
실행: .venv/bin/python test_jarvis_link.py

웹소켓 클라이언트와 푸시 채널을 모킹(FastAPI TestClient + monkeypatch)하여
실제 네트워크 없이 Pub/Sub 브로드캐스트와 승인→리밸런싱 파이프라인을 검증한다.
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME
import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

import jarvis_link
from jarvis_link import JarvisLink

TOKEN = "test-secret-token"


def _client():
    link = JarvisLink(token=TOKEN)
    return link, TestClient(link._app)


def test_ws_hud_stream_broadcast():
    link, client = _client()
    with client:
        with client.websocket_connect(f"/ws/hud?token={TOKEN}") as ws:
            snap = ws.receive_json()                 # 연결 직후 스냅샷
            assert snap["type"] == "snapshot", snap
            link.publish_hud(
                hud={"state": "IDLE", "macro_level": "위험", "face_count": 1},
                matrix={"종합": 88, "기술적": 42, "고래": 20, "소셜": 19},
                radar=[{"label": "기술적 분석", "value": 42, "max": 50},
                       {"label": "고래 플로우", "value": 20, "max": 25},
                       {"label": "소셜 하이프", "value": 19, "max": 25}],
                state="IDLE", away=True)
            msg = ws.receive_json()                   # 브로드캐스트 수신
            assert msg["type"] == "hud", msg
            assert msg["hud"]["macro_level"] == "위험"
            assert len(msg["radar"]) == 3 and msg["away"] is True
    print("✓ WebSocket HUD 스트림: 스냅샷 + 실시간 매트릭스/레이더 브로드캐스트 수신")


def test_ws_rejects_bad_token():
    link, client = _client()
    rejected = False
    with client:
        try:
            with client.websocket_connect("/ws/hud?token=WRONG") as ws:
                ws.receive_json()
        except Exception:
            rejected = True
    assert rejected, "잘못된 토큰인데 연결이 수락됨"
    assert link.stats["auth_fail"] >= 1
    print("✓ WebSocket 인증: 잘못된 토큰 연결 거부 (auth_fail 카운트)")


def test_rest_auth_required():
    link, client = _client()
    with client:
        r = client.post("/api/portfolio/rebalance/approve", json={"approval_code": "AB1234"})
        assert r.status_code == 401, r.status_code
        r2 = client.get("/api/state")
        assert r2.status_code == 401
    print("✓ REST 인증: 토큰 없는 승인/상태 요청 401 차단")


def test_remote_rebalance_approval_flow():
    link, client = _client()
    # 코어가 발급하는 일회성 승인 코드 (외출 위험 푸시에 동봉됨)
    code = link.issue_approval({"haircut_pct": 10, "reason": "매크로 위험 격상", "risk_level": "위험"})
    with client:
        # 잘못된 코드 → 409
        bad = client.post("/api/portfolio/rebalance/approve",
                          headers={"X-Link-Token": TOKEN}, json={"approval_code": "ZZ9999"})
        assert bad.status_code == 409, bad.status_code
        # 정상 승인 → 200
        ok = client.post("/api/portfolio/rebalance/approve",
                         headers={"X-Link-Token": TOKEN},
                         json={"approval_code": code, "decision": "approve"})
        assert ok.status_code == 200 and ok.json()["status"] == "approved", ok.text
        # 동일 코드 재사용(리플레이) → 409
        replay = client.post("/api/portfolio/rebalance/approve",
                             headers={"X-Link-Token": TOKEN}, json={"approval_code": code})
        assert replay.status_code == 409, "일회성 코드가 재사용됨"

    # 코어 소비: 승인 이벤트 → 포트폴리오 자율 리밸런싱 실행
    events = link.get_approval_events()
    assert len(events) == 1 and events[0]["action"] == "rebalance", events
    ctx = events[0]["context"]
    assert ctx["haircut_pct"] == 10 and ctx["risk_level"] == "위험"
    print(f"✓ 원격 승인 파이프라인: 발급→승인(200)→리플레이차단(409)→코어 큐 적재 (haircut={ctx['haircut_pct']}%)")


def test_approval_bruteforce_lockout():
    """짧은 승인 코드 무차별 대입 방어 — 임계 실패 초과 시 429 잠금."""
    link, client = _client()
    with client:
        h = {"X-Link-Token": TOKEN}
        # APPROVAL_MAX_FAILS(기본 5) 회 연속 오답 → 마지막에 잠금(429)
        statuses = []
        for i in range(jarvis_link.APPROVAL_MAX_FAILS):
            r = client.post("/api/portfolio/rebalance/approve", headers=h,
                            json={"approval_code": f"DEAD{i:04d}"})
            statuses.append(r.status_code)
        # 처음 4회는 409(코드 오류), 임계 도달 시 429(잠금)
        assert statuses[:-1] == [409] * (jarvis_link.APPROVAL_MAX_FAILS - 1), statuses
        assert statuses[-1] == 429, statuses
        # 잠금 상태에서는 '유효한' 코드라도 거부되어야 함(파괴적 액션 차단)
        valid = link.issue_approval({"haircut_pct": 10})
        blocked = client.post("/api/portfolio/rebalance/approve", headers=h,
                              json={"approval_code": valid})
        assert blocked.status_code == 429, blocked.status_code
        assert "Retry-After" in blocked.headers
    assert link.stats["approvals"] == 0, "무차별 대입 중 승인이 통과됨"
    print(f"✓ 무차별 대입 방어: {jarvis_link.APPROVAL_MAX_FAILS}회 오답→429 잠금, 잠금 중 유효코드도 차단")


def test_apply_rebalance_executes():
    import portfolio_manager as pm
    # 네트워크 차단 — 현재가/환율 모킹
    pm._fetch_current_price = lambda t: 100.0
    pm.get_usdkrw_rate = lambda: 1300.0

    tmp = Path(tempfile.mkdtemp()) / "portfolio.json"
    tmp.write_text(json.dumps({
        "available_cash_krw": 1_000_000,
        "holdings": {
            "AAPL": {"shares": 10, "avg_price_usd": 175.0},
            "005930.KS": {"shares": 50, "avg_price_krw": 72000},
        },
    }), encoding="utf-8")

    mgr = pm.PortfolioManager(path=tmp)
    res = mgr.apply_rebalance(10, reason="원격 승인 리밸런싱")
    assert res["success"], res
    assert res["raised_cash_krw"] > 0
    # AAPL 10→9, 삼성 50→45 (10% 감축)
    h = mgr.data["holdings"]
    assert abs(h["AAPL"]["shares"] - 9) < 1e-6, h
    assert abs(h["005930.KS"]["shares"] - 45) < 1e-6, h
    assert mgr.data["available_cash_krw"] > 1_000_000  # 매도 대금 현금화
    print(f"✓ 자율 리밸런싱 실행: 2종목 10% 매도 → {res['raised_cash_krw']:,.0f}원 현금 확보, 수량 정확히 감소")


def test_push_alert_channels_mocked():
    link, _ = _client()
    # 텔레그램 채널 활성 + 전송 모킹 (실제 네트워크 없음)
    jarvis_link.TELEGRAM_BOT_TOKEN = "BOT"; jarvis_link.TELEGRAM_CHAT_ID = "CHAT"
    sent = {}
    link._post_telegram = lambda text: sent.update(text=text) or True
    res = link.push_alert(f"{USER_DISPLAY_NAME}, 외부 활동 중 공급망 리스크가 감지되었습니다.",
                          context={"rebalance": True, "haircut_pct": 10},
                          issue_code=True, blocking=True)
    assert "telegram" in res["channels"], res
    assert res["approval_code"] and res["approval_code"] in sent["text"]
    assert link.stats["pushes"] >= 1
    # 푸시에 동봉된 코드로 즉시 원격 승인 가능해야 함
    ok, ctx = link._consume_approval_code(res["approval_code"])
    assert ok and ctx["haircut_pct"] == 10
    print(f"✓ 모바일 푸시(텔레그램 모킹): 승인 코드 동봉 + 코드 유효성 확인 (code={res['approval_code']})")


def test_away_state_and_snapshot():
    link, client = _client()
    assert link.is_away() is False
    link.set_away(True)
    assert link.is_away() is True
    with client:
        r = client.get("/api/state", headers={"X-Link-Token": TOKEN})
        assert r.status_code == 200 and r.json()["away"] is True
    print("✓ 외출 상태 머신: set_away → 스냅샷/REST 반영")


def test_r1_cswsh_origin_and_connection_cap():
    """Round 1: 교차 출처 웹소켓 하이재킹(CSWSH) 차단 + 동시 연결 상한(DoS)."""
    link, client = _client()
    with client:
        # (a) 악성 웹사이트 Origin → 유효 토큰이어도 거부
        blocked = False
        try:
            with client.websocket_connect(f"/ws/hud?token={TOKEN}",
                                          headers={"origin": "http://evil.example.com"}) as ws:
                ws.receive_json()
        except Exception:
            blocked = True
        assert blocked, "악성 Origin 웹소켓이 연결됨(CSWSH)"
        assert link.stats["cswsh_blocked"] >= 1
        # (b) 로컬호스트 Origin(정상 대시보드) 은 통과
        with client.websocket_connect(f"/ws/hud?token={TOKEN}",
                                      headers={"origin": "http://localhost:3000"}) as ws:
            assert ws.receive_json()["type"] == "snapshot"
    # (c) 동시 연결 상한 — 한도까지 채운 뒤 추가 연결 거부
    link2, client2 = _client()
    for _ in range(jarvis_link.LINK_MAX_CLIENTS):
        link2._clients.add(object())
    capped = False
    with client2:
        try:
            with client2.websocket_connect(f"/ws/hud?token={TOKEN}") as ws:
                ws.receive_json()
        except Exception:
            capped = True
    assert capped, "동시 연결 상한 초과인데 연결 수락됨"
    print(f"✓ Round1 CSWSH 차단 + 동시연결 상한({jarvis_link.LINK_MAX_CLIENTS}) DoS 방어")


def test_r2_rate_limit():
    """Round 2: IP별 슬라이딩 윈도우 레이트리밋."""
    orig = jarvis_link.RATE_LIMIT_MAX
    jarvis_link.RATE_LIMIT_MAX = 3
    try:
        link, client = _client()
        with client:
            codes = [client.get("/").status_code for _ in range(3)]
            assert codes == [200, 200, 200], codes
            r = client.get("/")
            assert r.status_code == 429 and "Retry-After" in r.headers, r.status_code
        assert link.stats["rate_limited"] >= 1
    finally:
        jarvis_link.RATE_LIMIT_MAX = orig
    print("✓ Round2 레이트리밋: 한도 초과 요청 429 + Retry-After")


def test_r3_security_headers_and_body_limit():
    """Round 3: 보안 응답 헤더 + 본문 크기 상한(페이로드 폭탄 방어)."""
    link, client = _client()
    with client:
        h = client.get("/").headers
        assert h.get("X-Content-Type-Options") == "nosniff"
        assert h.get("X-Frame-Options") == "DENY"
        assert "default-src 'none'" in h.get("Content-Security-Policy", "")
        assert h.get("Referrer-Policy") == "no-referrer"
    orig = jarvis_link.MAX_BODY_BYTES
    jarvis_link.MAX_BODY_BYTES = 50
    try:
        link2, client2 = _client()
        with client2:
            big = client2.post("/api/portfolio/rebalance/approve",
                               headers={"X-Link-Token": TOKEN},
                               json={"approval_code": "A" * 500})
            assert big.status_code == 413, big.status_code
    finally:
        jarvis_link.MAX_BODY_BYTES = orig
    print("✓ Round3 보안헤더(CSP/XFO/nosniff…) + 본문 16KB 상한(413)")


def test_r4_query_token_rejected_and_hashed():
    """Round 4: REST 쿼리 토큰 차단(URL 유출 방지) + 토큰 해시 저장."""
    import hashlib
    link, client = _client()
    # 토큰은 평문이 아닌 SHA-256 다이제스트로 검증
    assert link._token_hash == hashlib.sha256(TOKEN.encode()).hexdigest()
    with client:
        # 쿼리스트링 토큰은 REST 에서 거부(헤더만 허용)
        q = client.get(f"/api/state?token={TOKEN}")
        assert q.status_code == 401, "쿼리 토큰이 REST 에서 수락됨(URL 유출 위험)"
        # 헤더 토큰은 정상
        ok = client.get("/api/state", headers={"X-Link-Token": TOKEN})
        assert ok.status_code == 200
    print("✓ Round4 REST 쿼리토큰 차단 + 토큰 SHA-256 해시 검증")


def test_r5_ip_autoban():
    """Round 5: 인증 실패 누적 시 IP 자동 차단(fail2ban) — 유효 토큰도 차단."""
    orig = jarvis_link.BAN_MAX_AUTH_FAILS
    jarvis_link.BAN_MAX_AUTH_FAILS = 3
    try:
        link, client = _client()
        with client:
            for _ in range(3):
                bad = client.get("/api/state", headers={"X-Link-Token": "WRONG"})
                assert bad.status_code == 401
            # 차단 후에는 '유효한' 토큰이어도 403
            blocked = client.get("/api/state", headers={"X-Link-Token": TOKEN})
            assert blocked.status_code == 403, blocked.status_code
        assert link.stats["bans"] >= 1
    finally:
        jarvis_link.BAN_MAX_AUTH_FAILS = orig
    print("✓ Round5 IP 자동차단(fail2ban): 인증실패 누적 → 유효토큰도 403")


def test_r6_ws_message_size_and_flood():
    """Round 6: 인바운드 메시지 크기 상한 + 연결당 플러드 레이트 제한."""
    # (a) 메시지 크기 초과 → 서버가 연결 종료
    o_sz = jarvis_link.WS_MAX_MSG_BYTES
    jarvis_link.WS_MAX_MSG_BYTES = 16
    try:
        _, client = _client()
        closed = False
        try:
            with client, client.websocket_connect(f"/ws/hud?token={TOKEN}") as ws:
                ws.receive_json()
                ws.send_text("X" * 200)
                ws.receive_json()
        except Exception:
            closed = True
        assert closed, "초대형 메시지인데 연결 유지됨"
    finally:
        jarvis_link.WS_MAX_MSG_BYTES = o_sz
    # (b) 플러드 레이트 초과 → 종료
    o_rate = jarvis_link.WS_MSG_RATE_MAX
    jarvis_link.WS_MSG_RATE_MAX = 2
    try:
        _, client = _client()
        flooded = False
        try:
            with client, client.websocket_connect(f"/ws/hud?token={TOKEN}") as ws:
                ws.receive_json()
                for _ in range(8):
                    ws.send_text('{"type":"noop"}')
                ws.receive_json()   # 한도 초과로 서버가 닫음 → 예외
        except Exception:
            flooded = True
        assert flooded, "메시지 플러드인데 연결 유지됨"
    finally:
        jarvis_link.WS_MSG_RATE_MAX = o_rate
    print("✓ Round6 WS 메시지 크기 상한 + 연결당 플러드 레이트 차단")


def test_r7_ws_idle_and_session_timeout():
    """Round 7: 유휴/최대 세션 타임아웃 — 좌비움 세션 자동 종료."""
    o_idle = jarvis_link.WS_IDLE_TIMEOUT
    jarvis_link.WS_IDLE_TIMEOUT = 1
    try:
        _, client = _client()
        timed_out = False
        try:
            with client, client.websocket_connect(f"/ws/hud?token={TOKEN}") as ws:
                ws.receive_json()
                ws.receive_json()   # 메시지 안 보냄 → 1초 후 서버가 유휴 종료
        except Exception:
            timed_out = True
        assert timed_out, "유휴 세션이 종료되지 않음"
    finally:
        jarvis_link.WS_IDLE_TIMEOUT = o_idle
    # 최대 세션 수명 = 0 → 첫 메시지에서 즉시 만료 종료
    o_sess = jarvis_link.WS_MAX_SESSION
    jarvis_link.WS_MAX_SESSION = 0
    try:
        _, client = _client()
        ended = False
        try:
            with client, client.websocket_connect(f"/ws/hud?token={TOKEN}") as ws:
                ws.receive_json()
                ws.send_text('{"type":"ping"}')
                ws.receive_json()
        except Exception:
            ended = True
        assert ended, "최대 세션 수명 초과인데 유지됨"
    finally:
        jarvis_link.WS_MAX_SESSION = o_sess
    print("✓ Round7 WS 유휴 타임아웃 + 최대 세션 수명 자동 종료")


def test_r8_server_header_masked():
    """Round 8: Server/프레임워크 버전 헤더 은폐(풋프린팅 차단)."""
    _, client = _client()
    with client:
        h = client.get("/").headers
        assert h.get("server") == "JARVIS", h.get("server")
        assert "uvicorn" not in h.get("server", "").lower()
    print("✓ Round8 Server 헤더 은폐(uvicorn 버전 비노출)")


def test_r9_internal_error_masked():
    """Round 9: 내부 예외/미존재 경로를 정제 응답으로 마스킹(정보노출 차단)."""
    link = JarvisLink(token=TOKEN)
    client = TestClient(link._app, raise_server_exceptions=False)
    def _boom():
        raise RuntimeError("secret stacktrace /Users/secret/path token=abcd")
    link.get_snapshot = _boom   # 핸들러 내부에서 예외 유발
    with client:
        r = client.get("/api/state", headers={"X-Link-Token": TOKEN})
        assert r.status_code == 500, r.status_code
        assert r.json() == {"error": "internal_error"}
        assert "secret" not in r.text and "Traceback" not in r.text
        nf = client.get("/nope/secret")
        assert nf.status_code == 404 and nf.json() == {"error": "not_available"}
    print("✓ Round9 내부오류/404 마스킹(스택·경로·토큰 비노출)")


def test_r10_strict_input_validation():
    """Round 10: 승인 입력 엄격 검증 — decision enum / 본문 스키마."""
    link, client = _client()
    code = link.issue_approval({"haircut_pct": 10})
    with client:
        h = {"X-Link-Token": TOKEN}
        bad_dec = client.post("/api/portfolio/rebalance/approve", headers=h,
                              json={"approval_code": code, "decision": "DROP TABLE"})
        assert bad_dec.status_code == 400, bad_dec.status_code
        not_dict = client.post("/api/portfolio/rebalance/approve", headers=h, json=[1, 2, 3])
        assert not_dict.status_code == 400, not_dict.status_code
        too_many = client.post("/api/portfolio/rebalance/approve", headers=h,
                               json={f"k{i}": i for i in range(20)})
        assert too_many.status_code == 400, too_many.status_code
    print("✓ Round10 승인 입력 엄격검증(decision enum/비정형 본문/과대 키 → 400)")


def test_r11_pending_code_cap():
    """Round 11: 대기 승인코드 상한 — 무제한 발급 메모리 고갈 차단."""
    o = jarvis_link.PENDING_MAX
    jarvis_link.PENDING_MAX = 3
    try:
        link, _ = _client()
        codes = [link.issue_approval({"n": i}) for i in range(8)]
        assert len(link._pending) <= 3, len(link._pending)
        # 가장 오래된 코드는 폐기되어 더 이상 유효하지 않아야 함
        ok_old, _ = link._consume_approval_code(codes[0])
        assert ok_old is False
        # 최신 코드는 유효
        ok_new, _ = link._consume_approval_code(codes[-1])
        assert ok_new is True
    finally:
        jarvis_link.PENDING_MAX = o
    print("✓ Round11 대기 승인코드 상한(LRU 폐기)로 메모리 고갈 DoS 차단")


def test_r12_per_ip_ws_cap():
    """Round 12: IP별 동시 WebSocket 연결 수 제한."""
    o = jarvis_link.WS_MAX_PER_IP
    jarvis_link.WS_MAX_PER_IP = 2
    try:
        _, client = _client()
        with client:
            with client.websocket_connect(f"/ws/hud?token={TOKEN}") as w1:
                w1.receive_json()
                with client.websocket_connect(f"/ws/hud?token={TOKEN}") as w2:
                    w2.receive_json()
                    rejected = False
                    try:
                        with client.websocket_connect(f"/ws/hud?token={TOKEN}") as w3:
                            w3.receive_json()
                    except Exception:
                        rejected = True
                    assert rejected, "IP당 동시연결 상한 초과인데 수락됨"
    finally:
        jarvis_link.WS_MAX_PER_IP = o
    print("✓ Round12 IP별 동시 WS 연결 수 제한")


def test_r13_public_bind_guard():
    """Round 13: 공개 바인딩 안전 가드 — 명시 허용 없으면 로컬로 강등."""
    link, _ = _client()
    assert link._resolve_bind_host("0.0.0.0") == "127.0.0.1"
    assert link._resolve_bind_host("127.0.0.1") == "127.0.0.1"
    o = jarvis_link.ALLOW_PUBLIC_BIND
    jarvis_link.ALLOW_PUBLIC_BIND = True
    try:
        assert link._resolve_bind_host("0.0.0.0") == "0.0.0.0"
    finally:
        jarvis_link.ALLOW_PUBLIC_BIND = o
    print("✓ Round13 공개 바인딩 가드(미허용 0.0.0.0→127.0.0.1 강등)")


def test_r14_content_type_enforced():
    """Round 14: 변경 요청 application/json 강제(폼-CSRF/스머글링 방어)."""
    _, client = _client()
    with client:
        r = client.post("/api/portfolio/rebalance/approve",
                        headers={"X-Link-Token": TOKEN}, data="approval_code=AAAA1111")
        assert r.status_code == 415, r.status_code
    print("✓ Round14 POST Content-Type 강제(application/json 외 415)")


def test_r15_security_report():
    """Round 15: 기동 보안 자가진단 리포트."""
    link, _ = _client()
    rep = link.security_report()
    for k in ("bind", "public", "rate_limit", "ban_policy", "approval_lock",
              "pending_max", "max_body", "ws_idle", "ws_max_session"):
        assert k in rep, k
    assert rep["public"] is False and rep["token_source"] == "configured"
    print(f"✓ Round15 보안 자가진단: bind={rep['bind']} rate={rep['rate_limit']} ban={rep['ban_policy']}")


def test_r16_tls_resolution():
    """Round 16: TLS 모드 결정 — off/강제/auto(공개시) 동작."""
    link = JarvisLink(token=TOKEN)
    o_mode = jarvis_link.LINK_TLS_MODE
    try:
        jarvis_link.LINK_TLS_MODE = "0"
        link.host = "0.0.0.0"
        assert link._resolve_tls() == (None, None)   # 비활성
        jarvis_link.LINK_TLS_MODE = "auto"
        link.host = "127.0.0.1"
        assert link._resolve_tls() == (None, None)   # 로컬은 auto에서 평문
        jarvis_link.LINK_TLS_MODE = "auto"
        link.host = "0.0.0.0"
        cert, key = link._resolve_tls()              # 공개 바인딩 → 자동 PEM 생성 시도
        if cert:   # cryptography 설치 환경
            import os as _os
            assert _os.path.exists(cert) and _os.path.exists(key)
    finally:
        jarvis_link.LINK_TLS_MODE = o_mode
    print("✓ Round16 TLS 모드 결정(off/auto·공개 자동 PEM)")


def test_r17_ws_token_via_subprotocol():
    """Round 17: WS 토큰을 헤더/서브프로토콜로 인증(쿼리 노출 회피)."""
    _, client = _client()
    with client:
        # 서브프로토콜로 토큰 전달 → 쿼리스트링 없이 인증 성공
        with client.websocket_connect("/ws/hud",
                                      subprotocols=["jarvis-link", TOKEN]) as ws:
            snap = ws.receive_json()
            assert snap.get("type") == "snapshot"
        # 잘못된 서브프로토콜 토큰 → 거부
        rejected = False
        try:
            with client.websocket_connect("/ws/hud", subprotocols=["jarvis-link", "WRONG"]) as ws:
                ws.receive_json()
        except Exception:
            rejected = True
        assert rejected
    print("✓ Round17 WS 서브프로토콜/헤더 토큰 인증(쿼리 미노출)")


def test_r18_broadcast_isolates_slow_client():
    """Round 18: 느린/실패 클라이언트가 전체 브로드캐스트를 막지 않음."""
    import asyncio
    link = JarvisLink(token=TOKEN)

    class _OKWS:
        def __init__(self): self.got = 0
        async def send_json(self, p): self.got += 1

    class _DeadWS:
        async def send_json(self, p):
            raise RuntimeError("broken pipe")

    good, dead = _OKWS(), _DeadWS()
    link._clients = {good, dead}
    asyncio.run(link._broadcast({"type": "hud", "x": 1}))
    assert good.got == 1, "정상 클라이언트가 전달받지 못함"
    assert dead not in link._clients, "실패 클라이언트가 정리되지 않음"
    print("✓ Round18 브로드캐스트 격리(실패/느린 클라 폐기, 정상 클라 보호)")


def test_r19_net_state_memory_hygiene():
    """Round 19: 인증실패/밴 상태 메모리 위생(만료·빈 엔트리 정리)."""
    link = JarvisLink(token=TOKEN)
    now = __import__("time").time()
    # 만료된 밴 2049개 주입 → 임계 초과 시 정리 트리거
    for i in range(2050):
        link._banned[f"10.0.{i//256}.{i%256}"] = now - 1   # 이미 만료
    link._register_auth_fail("1.2.3.4", "test")
    assert len(link._banned) < 2050, "만료 밴이 정리되지 않음"
    print("✓ Round19 보안상태 메모리 위생(만료 밴/빈 큐 GC)")


def test_r20_trusted_proxy_xff():
    """Round 20: 신뢰 프록시의 XFF만 신뢰, 비신뢰 출처 XFF는 무시."""
    link = JarvisLink(token=TOKEN)

    class _Req:
        def __init__(self, peer, xff):
            self.client = type("C", (), {"host": peer, "port": 1})()
            self.headers = {"x-forwarded-for": xff}

    o = jarvis_link.TRUSTED_PROXIES
    try:
        jarvis_link.TRUSTED_PROXIES = {"10.0.0.1"}
        # 신뢰 프록시 → XFF 선두 홉 사용
        assert link._client_ip(_Req("10.0.0.1", "203.0.113.9, 10.0.0.1")) == "203.0.113.9"
        # 비신뢰 출처 → XFF 무시(스푸핑 방지), 피어 IP 사용
        assert link._client_ip(_Req("8.8.8.8", "1.1.1.1")) == "8.8.8.8"
    finally:
        jarvis_link.TRUSTED_PROXIES = o
    print("✓ Round20 신뢰 프록시 XFF 검증(스푸핑 방지)")


if __name__ == "__main__":
    assert jarvis_link.FASTAPI_AVAILABLE, "FastAPI 미설치"
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
    print(f"\n✅ ALL {len(tests)} TESTS PASSED")
