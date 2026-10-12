"""
J.A.R.V.I.S Link — 멀티 디바이스 동기화 & 모바일 미러링 게이트웨이
═══════════════════════════════════════════════════════════════════════════
데스크톱 HUD(screen_overlay)의 실시간 데이터를 스마트폰/태블릿 등 외부 클라이언트로
스트리밍하고, 외출 중 위험 알림을 모바일로 푸시하며, 원격 리밸런싱 승인을 수신한다.

구성:
  · FastAPI + uvicorn 경량 비동기 서버를 **자체 백그라운드 스레드/이벤트 루프**에서 구동
    → 메인 퀀트 연산·STT/TTS 오디오 루프와 완전히 분리(asyncio 스레드 가드레일).
  · WebSocket  GET  /ws/hud                     : HUD/매트릭스/레이더 JSON 실시간 Pub/Sub 브로드캐스트
  · REST       POST /api/portfolio/rebalance/approve : 모바일 원격 리밸런싱 승인 수신 → 코어로 전달
  · REST       GET  /api/state                  : 최신 스냅샷 폴링(폴백)
  · 푸시 채널 : 텔레그램 봇 / 디스코드 웹훅 / 범용 웹훅 (환경변수로 자동 선택, 가장 가벼운 채널)

보안 (빅테크급 다계층 하드닝 — Google/Kakao/Instagram 스타일):
  · 토큰: SHA-256 다이제스트 상수시간 비교. REST 는 X-Link-Token 헤더 전용(쿼리 토큰 기본 차단
    → URL/액세스로그 유출 방지). 자동발급 토큰은 평문 로그 대신 0600 파일에만 저장.
  · 원격 승인: 일회성 승인 코드(TTL) + 무차별 대입 잠금(실패 임계 초과 시 엔드포인트 일시 잠금).
  · CSWSH 방어: 웹소켓 Origin 검증(악성 웹사이트 브라우저 연결 차단, 네이티브 앱은 허용).
  · 동시 연결 상한(DoS) + IP별 슬라이딩 윈도우 레이트리밋 + 인증 실패 누적 IP 자동차단(fail2ban).
  · 응답 보안 헤더(HSTS/CSP/X-Frame-Options/nosniff 등) + 본문 크기 상한(페이로드 폭탄 방어).
  · WS 인바운드 메시지 크기 상한 + 연결당 플러드 레이트 + 유휴/최대세션 타임아웃(좌비움 자동종료).
  · IP별 동시 WS 연결 상한 + 대기 승인코드 상한(메모리 고갈 DoS 차단).
  · Server/버전 헤더 은폐(풋프린팅 차단) + 전역 예외/404 마스킹(스택·경로·토큰 비노출).
  · 변경요청 application/json 강제(폼-CSRF/스머글링 방어) + 승인 입력 엄격검증(코드포맷/decision enum).
  · 공개 바인딩 안전 가드(명시 허용 없으면 0.0.0.0→127.0.0.1 강등) + 기동 보안 자가진단(security_report).
  · 실제 TLS(wss/https) 자동 적용(공개 바인딩 시 auto, 자체 서명 PEM 자동 생성) — 토큰/데이터 스니핑 차단.
  · WS 토큰을 헤더(X-Link-Token)/서브프로토콜 우선 인증(쿼리 토큰 URL·로그 유출 최소화).
  · 브로드캐스트 클라이언트별 송신 타임아웃(느린 클라 head-of-line blocking 방지).
  · 신뢰 프록시(TRUSTED_PROXIES)의 X-Forwarded-For 만 신뢰(IP 스푸핑 방지) + 보안상태 메모리 위생(GC).
  · 모든 거부/차단/승인 이벤트는 security.write_audit_log 해시 체인에 적재.
  · 외부망 노출 시 바인딩 호스트(JARVIS_LINK_HOST)를 유연하게 지정(기본 127.0.0.1, LAN=0.0.0.0).

의존성: pip install fastapi "uvicorn[standard]"  (미설치 시 available=False 로 안전 무력화)
"""
from __future__ import annotations

import os
import re
import hmac
import time
import json
import queue
import secrets
import threading
from collections import deque
from datetime import datetime
from typing import Callable, Optional

# ── 외부 통신(푸시)용 — net(재시도/적응형 타임아웃) 우선, 없으면 requests ──
try:
    import net as _http
except Exception:  # pragma: no cover
    try:
        import requests as _http
    except Exception:
        _http = None

# ── 감사 로그(해시 체인) 연동 — 없으면 무동작 ──
try:
    from security import write_audit_log as _audit
except Exception:  # pragma: no cover
    def _audit(event_type: str, details) -> None:  # type: ignore
        pass

# ── FastAPI / uvicorn (선택 의존성) ──
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
    from fastapi.responses import JSONResponse
    import uvicorn
    FASTAPI_AVAILABLE = True
except Exception:  # pragma: no cover
    FASTAPI_AVAILABLE = False


# ════════════════════════════════════════════════════════════════════════
#  설정 (환경변수 — 외부망/모바일 접근을 유연하게 코드화)
# ════════════════════════════════════════════════════════════════════════
LINK_HOST          = os.getenv("JARVIS_LINK_HOST", "127.0.0.1")   # LAN 노출 시 0.0.0.0
LINK_PORT          = int(os.getenv("JARVIS_LINK_PORT", "8770"))
LINK_TOKEN_ENV     = os.getenv("JARVIS_LINK_TOKEN", "").strip()
APPROVAL_TTL_SEC   = int(os.getenv("JARVIS_LINK_APPROVAL_TTL", "1800"))   # 일회성 승인 코드 유효(초)
# 원격 승인 무차별 대입(brute-force) 방어 — 짧은 승인 코드 추측 공격 차단
APPROVAL_MAX_FAILS   = int(os.getenv("JARVIS_LINK_APPROVE_MAX_FAILS", "5"))    # 윈도우 내 허용 실패 횟수
APPROVAL_FAIL_WINDOW = int(os.getenv("JARVIS_LINK_APPROVE_FAIL_WINDOW", "300"))# 실패 집계 윈도우(초)
APPROVAL_LOCK_SEC    = int(os.getenv("JARVIS_LINK_APPROVE_LOCK_SEC", "300"))   # 임계 초과 시 잠금(초)

# ── 빅테크급 게이트웨이 하드닝 (Google/Kakao/Instagram 스타일) ──
# Round 1: CSWSH(교차 출처 웹소켓 하이재킹) 방어 + 동시 연결 상한(자원 고갈 DoS 방어)
LINK_ALLOWED_ORIGINS = [o.strip() for o in os.getenv("JARVIS_LINK_ALLOWED_ORIGINS", "").split(",") if o.strip()]
LINK_MAX_CLIENTS     = int(os.getenv("JARVIS_LINK_MAX_CLIENTS", "12"))
# Round 2: IP별 요청 레이트리밋 (슬라이딩 윈도우 스로틀)
RATE_LIMIT_MAX       = int(os.getenv("JARVIS_LINK_RATE_MAX", "60"))      # 윈도우당 최대 요청
RATE_LIMIT_WINDOW    = int(os.getenv("JARVIS_LINK_RATE_WINDOW", "60"))   # 윈도우(초)
# Round 3: 본문 크기 상한(페이로드 폭탄 DoS 방어) + HTTPS 강제 옵션
MAX_BODY_BYTES       = int(os.getenv("JARVIS_LINK_MAX_BODY", "16384"))   # 16KB
LINK_FORCE_HTTPS     = os.getenv("JARVIS_LINK_FORCE_HTTPS", "0") == "1"
# Round 4: REST 쿼리스트링 토큰 허용 여부(기본 차단 → URL/액세스로그 토큰 유출 방지)
ALLOW_QUERY_TOKEN_REST = os.getenv("JARVIS_LINK_ALLOW_QUERY_TOKEN", "0") == "1"
# Round 5: 인증 실패 기반 IP 자동 차단(fail2ban 스타일)
BAN_MAX_AUTH_FAILS   = int(os.getenv("JARVIS_LINK_BAN_MAX_FAILS", "10"))
BAN_WINDOW           = int(os.getenv("JARVIS_LINK_BAN_WINDOW", "300"))
BAN_SEC              = int(os.getenv("JARVIS_LINK_BAN_SEC", "900"))      # 차단 지속(초)

# ── 추가 하드닝 (Round 6~15) ──
WS_MAX_MSG_BYTES   = int(os.getenv("JARVIS_LINK_WS_MAX_MSG", "4096"))     # R6: 인바운드 메시지 크기 상한
WS_MSG_RATE_MAX    = int(os.getenv("JARVIS_LINK_WS_MSG_RATE", "30"))      # R6: 윈도우당 메시지 수
WS_MSG_RATE_WINDOW = int(os.getenv("JARVIS_LINK_WS_MSG_WINDOW", "10"))    # R6: 윈도우(초)
WS_IDLE_TIMEOUT    = int(os.getenv("JARVIS_LINK_WS_IDLE", "300"))         # R7: 유휴 연결 종료(초)
WS_MAX_SESSION     = int(os.getenv("JARVIS_LINK_WS_MAX_SESSION", "3600")) # R7: 최대 세션 수명(초)
WS_MAX_PER_IP      = int(os.getenv("JARVIS_LINK_WS_MAX_PER_IP", "4"))     # R12: IP당 동시 WS 연결
PENDING_MAX        = int(os.getenv("JARVIS_LINK_PENDING_MAX", "64"))      # R11: 대기 승인코드 상한
SERVER_HEADER      = os.getenv("JARVIS_LINK_SERVER_HEADER", "JARVIS")     # R8: Server 헤더 은폐값
ALLOW_PUBLIC_BIND  = os.getenv("JARVIS_LINK_ALLOW_PUBLIC", "0") == "1"    # R13: 공개 바인딩 명시 허용
# R10: 승인 코드 포맷(16진수 4~16자) / decision 허용값
_CODE_RE = re.compile(r"^[0-9A-Z]{4,16}$")
# 승인 코드 알파벳: Base36(0-9,A-Z). 8자 ≈ 41.4비트로 8 hex(32비트) 대비 엔트로피
# 상향하되 모바일 입력 길이(8자)는 동일 유지. 혼동 문자 포함 — 코드는 화면 표시 후 입력.
_CODE_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_DECISIONS = {"approve", "yes", "ok", "deny", "reject", "no", ""}

# ── 전송/연결 하드닝 (Round 16~20) ──
# R16: 실제 TLS — "auto"(공개 바인딩 시 자동 on) | "1"/"true"(강제 on) | "0"(off)
LINK_TLS_MODE      = os.getenv("JARVIS_LINK_TLS", "auto").strip().lower()
LINK_TLS_CERT      = os.getenv("JARVIS_LINK_TLS_CERT", "").strip()   # 직접 지정(PEM) 우선
LINK_TLS_KEY       = os.getenv("JARVIS_LINK_TLS_KEY", "").strip()
# R17: WS 토큰을 쿼리스트링으로 받는 것 허용 여부(기본 허용=하위호환; 헤더/서브프로토콜 우선)
ALLOW_QUERY_TOKEN_WS = os.getenv("JARVIS_LINK_ALLOW_QUERY_TOKEN_WS", "1") == "1"
# R18: 브로드캐스트 클라이언트별 송신 타임아웃(느린 클라이언트 head-of-line 차단)
BROADCAST_TIMEOUT  = float(os.getenv("JARVIS_LINK_BROADCAST_TIMEOUT", "3.0"))
# R20: 신뢰 프록시 IP — 이 출처의 X-Forwarded-For 만 신뢰(스푸핑 방지)
TRUSTED_PROXIES    = {p.strip() for p in os.getenv("JARVIS_LINK_TRUSTED_PROXIES", "").split(",") if p.strip()}

# 응답 보안 헤더 (Round 3) — 정적 상수
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Cache-Control": "no-store",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    "Cross-Origin-Resource-Policy": "same-origin",
}

# 푸시 채널 (가장 가벼운 채널을 환경변수로 자동 선택)
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "").strip()
DISCORD_WEBHOOK    = os.getenv("DISCORD_WEBHOOK_URL", "").strip()
GENERIC_WEBHOOK    = os.getenv("JARVIS_PUSH_WEBHOOK_URL", "").strip()


def _tg_creds() -> tuple:
    """텔레그램 토큰/CHAT_ID 를 호출 시점에 조회. 모듈 import 가 load_dotenv() 보다
    먼저 일어나 모듈 상수가 비어 있을 수 있으므로, 환경변수를 우선 읽고 상수로 폴백."""
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip() or TELEGRAM_BOT_TOKEN
    chat  = os.getenv("TELEGRAM_CHAT_ID", "").strip() or TELEGRAM_CHAT_ID
    return token, chat


def _log_default(level: str, msg: str) -> None:
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 📱 [JARVIS Link] {msg}")


# ════════════════════════════════════════════════════════════════════════
#  핵심 게이트웨이
# ════════════════════════════════════════════════════════════════════════
class JarvisLink:
    """모바일 미러링 게이트웨이. 스레드 안전 Pub/Sub + 원격 승인 + 푸시."""

    def __init__(self, host: str = LINK_HOST, port: int = LINK_PORT,
                 token: str = "", logger: Optional[Callable[[str, str], None]] = None):
        self.host = host
        self.port = port
        self._log = logger or _log_default
        self.available = FASTAPI_AVAILABLE

        # 공유 시크릿 — 미설정 시 자동 발급(외부 노출 전 반드시 확인하도록 로깅)
        self.token = token or LINK_TOKEN_ENV or secrets.token_urlsafe(24)
        self._token_autogen = not (token or LINK_TOKEN_ENV)
        # Round 4: 토큰을 평문 비교 대신 SHA-256 다이제스트로 검증(메모리 평문 노출 최소화)
        import hashlib as _hl
        self._token_hash = _hl.sha256(self.token.encode()).hexdigest()

        # 비동기 서버/루프 — 메인 스레드와 분리
        self._app = None
        self._server = None
        self._thread: Optional[threading.Thread] = None
        self._loop = None
        self._ready = threading.Event()
        self._tls = False                    # 실제 TLS 적용 여부(R16, start()에서 결정)
        self._clients: set = set()           # 활성 WebSocket 연결 (서버 루프에서만 변형)

        # 공유 상태 (스레드 안전)
        self._state_lock = threading.Lock()
        self._snapshot: dict = {"type": "snapshot", "hud": {}, "matrix": {}, "radar": [], "away": False, "ts": 0}
        self._away = False

        # 원격 승인: 발급된 일회성 코드 + 코어가 소비할 승인 이벤트 큐
        self._approval_lock = threading.Lock()
        self._pending: dict[str, dict] = {}          # code -> {"context", "ts"}
        self._approval_q: "queue.Queue[dict]" = queue.Queue()
        # ZFold / 모바일 → Mac: 알림 TTS·음성 명령 수신 큐
        self._mobile_inbound_q: "queue.Queue[dict]" = queue.Queue()
        # 무차별 대입 방어: 실패 타임스탬프 슬라이딩 윈도우 + 잠금 만료 시각
        self._approval_fails: deque = deque(maxlen=256)
        self._approval_locked_until: float = 0.0

        # ── 네트워크 보안 가드(레이트리밋/IP밴) 공유 상태 ──
        self._net_lock = threading.Lock()
        self._rl_hits: dict[str, deque] = {}     # ip -> 최근 요청 타임스탬프 (레이트리밋)
        self._authfails: dict[str, deque] = {}   # ip -> 최근 인증 실패 타임스탬프 (밴 추적)
        self._banned: dict[str, float] = {}      # ip -> 차단 만료 시각
        self._ws_ip_counts: dict[str, int] = {}  # ip -> 동시 WS 연결 수 (R12)

        # 통계
        self.stats = {"broadcasts": 0, "pushes": 0, "approvals": 0, "auth_fail": 0,
                      "clients_peak": 0, "rate_limited": 0, "bans": 0, "cswsh_blocked": 0}

        if self.available:
            self._build_app()

    # ─────────────────────────────────────────────────────────────
    #  FastAPI 앱 구성
    # ─────────────────────────────────────────────────────────────
    def _build_app(self):
        from starlette.exceptions import HTTPException as _StarletteHTTPException
        app = FastAPI(title="J.A.R.V.I.S Link", docs_url=None, redoc_url=None, openapi_url=None)

        @app.on_event("startup")
        async def _on_start():
            import asyncio
            self._loop = asyncio.get_event_loop()
            self._ready.set()

        # ── Round 9: 전역 예외/오류 핸들러 — 내부 스택/경로/버전 노출 차단(정보 누출 방지) ──
        @app.exception_handler(Exception)
        async def _unhandled(request: "Request", exc: Exception):
            _audit("LINK_INTERNAL_ERROR", {"path": str(request.url.path),
                                           "err": f"{type(exc).__name__}: {str(exc)[:160]}"})
            return JSONResponse({"error": "internal_error"}, status_code=500)

        @app.exception_handler(_StarletteHTTPException)
        async def _http_exc(request: "Request", exc):
            # 기본 detail(예: 'Not Found','Method Not Allowed')만 정제 반환
            return JSONResponse({"error": "not_available"}, status_code=exc.status_code)

        # ── 전역 HTTP 보안 미들웨어: IP밴 → 본문크기 → 레이트리밋 → 보안헤더 ──
        @app.middleware("http")
        async def _security_guard(request: "Request", call_next):
            ip = self._client_ip(request)
            is_https = request.url.scheme == "https"
            # 1) IP 자동 차단(fail2ban)
            if self._is_banned(ip):
                _audit("LINK_BANNED_REJECT", {"ip": ip, "path": request.url.path})
                r = JSONResponse({"error": "forbidden"}, status_code=403)
                self._apply_security_headers(r, is_https); return r
            # 2) 본문 크기 상한(페이로드 폭탄 방어)
            cl = request.headers.get("content-length", "")
            if cl.isdigit() and int(cl) > MAX_BODY_BYTES:
                _audit("LINK_BODY_TOO_LARGE", {"ip": ip, "len": int(cl)})
                r = JSONResponse({"error": "payload_too_large"}, status_code=413)
                self._apply_security_headers(r, is_https); return r
            # 2.5) Round 14: 변경 요청(POST 등)은 application/json 강제
            #      (브라우저 폼 기반 CSRF·요청 스머글링 차단; 커스텀 헤더 인증과 함께 다중 방어)
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype != "application/json":
                    _audit("LINK_BAD_CONTENT_TYPE", {"ip": ip, "ctype": ctype[:40]})
                    r = JSONResponse({"error": "unsupported_media_type"}, status_code=415)
                    self._apply_security_headers(r, is_https); return r
            # 3) HTTPS 강제(옵션) — 로컬호스트는 예외
            if LINK_FORCE_HTTPS and not is_https and ip not in ("127.0.0.1", "::1", "testclient"):
                r = JSONResponse({"error": "https_required"}, status_code=400)
                self._apply_security_headers(r, True); return r
            # 4) IP별 레이트리밋(슬라이딩 윈도우)
            if not self._rate_ok(ip):
                self.stats["rate_limited"] += 1
                _audit("LINK_RATE_LIMIT", {"ip": ip, "path": request.url.path})
                r = JSONResponse({"error": "rate_limited", "retry_after": RATE_LIMIT_WINDOW},
                                 status_code=429, headers={"Retry-After": str(RATE_LIMIT_WINDOW)})
                self._apply_security_headers(r, is_https); return r
            resp = await call_next(request)
            self._apply_security_headers(resp, is_https)
            return resp

        @app.get("/")
        async def _root():
            return {"service": "jarvis-link", "status": "online",
                    "clients": len(self._clients), "away": self._away}

        @app.get("/api/state")
        async def _state(request: "Request"):
            if not self._check_auth_request(request):
                self._register_auth_fail(self._client_ip(request), "state")
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return self.get_snapshot()

        @app.websocket("/ws/hud")
        async def _ws_hud(ws: "WebSocket"):
            ip = self._client_ip(ws)
            # 1) IP 자동 차단
            if self._is_banned(ip):
                _audit("LINK_WS_BANNED", {"ip": ip})
                await ws.close(code=4403); return
            # 2) 레이트리밋(연결 시도 폭주 차단)
            if not self._rate_ok(ip):
                self.stats["rate_limited"] += 1
                await ws.close(code=4429); return
            # 3) CSWSH(교차 출처 웹소켓 하이재킹) 방어 — 악성 웹사이트의 브라우저 연결 차단
            origin = ws.headers.get("origin", "")
            if not self._origin_allowed(origin):
                self.stats["cswsh_blocked"] += 1
                _audit("LINK_WS_ORIGIN_BLOCK", {"ip": ip, "origin": origin[:80]})
                await ws.close(code=4403); return
            # 4) 동시 연결 상한(자원 고갈 DoS 방어) — 전역 + IP별(R12)
            if len(self._clients) >= LINK_MAX_CLIENTS:
                _audit("LINK_WS_MAX_CLIENTS", {"ip": ip, "max": LINK_MAX_CLIENTS})
                await ws.close(code=4429); return
            with self._net_lock:
                ip_n = self._ws_ip_counts.get(ip, 0)
            if ip_n >= WS_MAX_PER_IP:
                _audit("LINK_WS_MAX_PER_IP", {"ip": ip, "max": WS_MAX_PER_IP})
                await ws.close(code=4429); return
            # 5) 토큰 인증 — 헤더(X-Link-Token) → 서브프로토콜 → (옵션)쿼리스트링 순(R17)
            #    쿼리 토큰은 URL/로그 유출 경로이므로 헤더/서브프로토콜 인증을 우선 권장.
            token, subproto = self._extract_ws_token(ws)
            if not self._verify_token(token):
                self._register_auth_fail(ip, "ws_token")
                _audit("LINK_WS_AUTH_FAIL", {"peer": _peer(ws)})
                await ws.close(code=4401)
                return
            await ws.accept(subprotocol=subproto)
            import asyncio
            self._clients.add(ws)
            with self._net_lock:
                self._ws_ip_counts[ip] = self._ws_ip_counts.get(ip, 0) + 1
            self.stats["clients_peak"] = max(self.stats["clients_peak"], len(self._clients))
            self._log("INFO", f"📲 모바일 클라이언트 연결 (총 {len(self._clients)})")
            _audit("LINK_WS_CONNECT", {"peer": _peer(ws), "clients": len(self._clients)})
            try:
                # 연결 직후 최신 스냅샷 1회 푸시
                await ws.send_json(self.get_snapshot())
                start_ts = time.time()
                msg_times: deque = deque(maxlen=WS_MSG_RATE_MAX * 2 + 8)
                while True:
                    # R7: 유휴 타임아웃 — 일정 시간 무메시지면 좌비움 세션으로 간주하고 종료
                    try:
                        msg = await asyncio.wait_for(ws.receive_text(), timeout=WS_IDLE_TIMEOUT)
                    except asyncio.TimeoutError:
                        _audit("LINK_WS_IDLE_CLOSE", {"ip": ip})
                        await ws.close(code=4408); break
                    now = time.time()
                    # R7: 최대 세션 수명 초과 시 강제 재인증 유도(종료)
                    if now - start_ts > WS_MAX_SESSION:
                        _audit("LINK_WS_SESSION_MAX", {"ip": ip})
                        await ws.close(code=4408); break
                    # R6: 인바운드 메시지 크기 상한
                    if len(msg) > WS_MAX_MSG_BYTES:
                        _audit("LINK_WS_MSG_TOO_BIG", {"ip": ip, "len": len(msg)})
                        await ws.close(code=1009); break
                    # R6: 연결당 메시지 플러드 레이트 제한
                    msg_times.append(now)
                    recent = [t for t in msg_times if now - t <= WS_MSG_RATE_WINDOW]
                    if len(recent) > WS_MSG_RATE_MAX:
                        _audit("LINK_WS_FLOOD", {"ip": ip, "msgs": len(recent)})
                        await ws.close(code=4429); break
                    await self._handle_client_msg(ws, msg)
            except WebSocketDisconnect:
                pass
            except Exception:
                pass
            finally:
                self._clients.discard(ws)
                with self._net_lock:
                    if self._ws_ip_counts.get(ip, 0) <= 1:
                        self._ws_ip_counts.pop(ip, None)
                    else:
                        self._ws_ip_counts[ip] -= 1
                self._log("INFO", f"📴 클라이언트 해제 (총 {len(self._clients)})")

        @app.post("/api/portfolio/rebalance/approve")
        async def _approve(request: "Request"):
            if not self._check_auth_request(request):
                self._register_auth_fail(self._client_ip(request), "approve_token")
                _audit("LINK_APPROVE_AUTH_FAIL", {"peer": _peer(request)})
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                body = await request.json()
            except Exception:
                body = {}
            # ── Round 10: 엄격 입력 검증 (스키마/포맷/길이) ──
            if not isinstance(body, dict) or len(body) > 8:
                _audit("LINK_APPROVE_BADREQ", {"peer": _peer(request), "reason": "malformed_body"})
                return JSONResponse({"error": "bad_request"}, status_code=400)
            code = str(body.get("approval_code", "")).strip().upper()[:32]
            decision = str(body.get("decision", "approve")).strip().lower()[:16]
            if decision not in _DECISIONS:
                _audit("LINK_APPROVE_BADREQ", {"peer": _peer(request), "reason": "bad_decision"})
                return JSONResponse({"error": "bad_request"}, status_code=400)
            # ── 무차별 대입 잠금 확인 (파괴적 액션 보호) ──
            if self._approval_is_locked():
                self.stats["auth_fail"] += 1
                _audit("LINK_APPROVE_LOCKED", {"peer": _peer(request),
                                               "retry_after": APPROVAL_LOCK_SEC})
                return JSONResponse({"error": "too_many_attempts", "retry_after": APPROVAL_LOCK_SEC},
                                    status_code=429, headers={"Retry-After": str(APPROVAL_LOCK_SEC)})
            # 포맷 불일치 코드는 조회 없이 즉시 실패 처리(포맷/타이밍 오라클 차단)
            if _CODE_RE.match(code):
                ok, ctx = self._consume_approval_code(code)
            else:
                ok, ctx = False, None
            if not ok:
                locked_now = self._record_approval_failure()
                # 코드 추측도 자격증명 공격 → IP 밴 추적에도 반영(다계층 방어)
                self._register_auth_fail(self._client_ip(request), "approve_code")
                _audit("LINK_APPROVE_REJECT", {"reason": "invalid_or_expired_code",
                                               "code": _mask_code(code),
                                               "peer": _peer(request),
                                               "locked": locked_now})
                if locked_now:
                    self._log("WARN", "🚫 원격 승인 무차별 대입 감지 — 승인 엔드포인트 일시 잠금")
                    return JSONResponse({"error": "too_many_attempts", "retry_after": APPROVAL_LOCK_SEC},
                                        status_code=429, headers={"Retry-After": str(APPROVAL_LOCK_SEC)})
                return JSONResponse({"error": "invalid_or_expired_code"}, status_code=409)
            # 정상 승인 → 누적 실패 카운터 리셋(정상 사용자 페널티 방지)
            self._reset_approval_failures()
            if decision in ("deny", "reject", "no"):
                _audit("LINK_APPROVE_DENIED", {"code": _mask_code(code)})
                return {"status": "denied", "code": _mask_code(code)}
            evt = {"action": "rebalance", "context": ctx, "code": _mask_code(code),
                   "source": "mobile", "ts": time.time()}
            self._approval_q.put(evt)
            self.stats["approvals"] += 1
            _audit("LINK_APPROVE_OK", {"code": _mask_code(code),
                                       "haircut_pct": (ctx or {}).get("haircut_pct")})
            self._log("INTR", f"✅ 모바일 원격 리밸런싱 승인 수신 (code={_mask_code(code)})")
            return {"status": "approved", "queued": True, "code": _mask_code(code)}

        @app.post("/api/mobile/notify")
        async def _mobile_notify(request: "Request"):
            """ZFoldMacBridge — 폴드/안드로이드 알림 → JARVIS TTS 큐."""
            if not self._check_auth_request(request):
                self._register_auth_fail(self._client_ip(request), "mobile_notify")
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            try:
                body = await request.json()
            except Exception:
                body = {}
            if not isinstance(body, dict):
                return JSONResponse({"error": "bad_request"}, status_code=400)
            evt = self._enqueue_mobile_inbound(body, source="rest")
            return {"status": "queued", "id": evt.get("id")}

        self._app = app

    def _enqueue_mobile_inbound(self, data: dict, source: str = "ws") -> dict:
        """모바일 알림/음성 명령을 Mac 처리 큐에 적재."""
        msg_type = str(data.get("type") or "notification").lower()
        text = (data.get("text") or data.get("body") or data.get("message") or "").strip()
        title = (data.get("title") or data.get("app") or "모바일").strip()
        evt = {
            "id": f"mob_{int(time.time() * 1000)}",
            "type": msg_type,
            "title": title,
            "text": text,
            "reply_to": data.get("reply_to") or data.get("notification_id"),
            "source": source,
            "ts": time.time(),
            "raw": {k: v for k, v in data.items() if k != "token"},
        }
        if msg_type in ("voice_command", "reply", "command") and text:
            evt["process_as_user"] = True
        elif msg_type == "notification" and (text or title):
            evt["read_aloud"] = True
            evt["tts_text"] = f"{title}. {text}".strip(". ")
        self._mobile_inbound_q.put(evt)
        self.publish("mobile_inbound", evt)
        _audit("LINK_MOBILE_INBOUND", {"type": msg_type, "title": title[:40], "source": source})
        self._log("INFO", f"📱 모바일 수신 [{msg_type}] {title[:30]}")
        return evt

    def get_mobile_inbound_events(self) -> list:
        out = []
        while True:
            try:
                out.append(self._mobile_inbound_q.get_nowait())
            except queue.Empty:
                break
        return out

    async def _handle_client_msg(self, ws, msg: str):
        try:
            data = json.loads(msg)
        except Exception:
            return
        # 단순 핑/퐁 (지연 측정·연결 유지)
        if data.get("type") == "ping":
            try:
                await ws.send_json({"type": "pong", "ts": time.time()})
            except Exception:
                pass
            return
        # ZFoldMacBridge — WebSocket 알림/음성 명령
        if data.get("type") in ("notification", "voice_command", "reply", "command", "mobile_notify"):
            self._enqueue_mobile_inbound(data, source="ws")
            try:
                await ws.send_json({"type": "ack", "id": data.get("id"), "ts": time.time()})
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────
    #  인증
    # ─────────────────────────────────────────────────────────────
    def _verify_token(self, token: str) -> bool:
        if not token:
            return False
        import hashlib
        cand = hashlib.sha256(str(token).encode()).hexdigest()
        return hmac.compare_digest(cand, self._token_hash)

    def _check_auth_request(self, request) -> bool:
        """REST 인증 — 토큰은 헤더(X-Link-Token) 우선. 쿼리스트링 토큰은 기본 차단
        (URL·프록시·액세스로그에 시크릿이 남는 유출 경로를 봉쇄; 빅테크 API 관행)."""
        token = ""
        try:
            token = request.headers.get("x-link-token", "")
            if not token and ALLOW_QUERY_TOKEN_REST:
                token = request.query_params.get("token", "")
        except Exception:
            token = ""
        return self._verify_token(token)

    def _extract_ws_token(self, ws):
        """WS 인증 토큰 추출(R17). 우선순위: X-Link-Token 헤더 → Sec-WebSocket-Protocol
        서브프로토콜 → (ALLOW_QUERY_TOKEN_WS 일 때만) 쿼리스트링.
        Returns (token, accept_subprotocol). 서브프로토콜로 받은 경우 accept 시 echo."""
        # 1) 표준 헤더
        token = ws.headers.get("x-link-token", "")
        if token:
            return token, None
        # 2) 서브프로토콜: 관례상 ["jarvis-link", "<TOKEN>"] 또는 ["<TOKEN>"]
        raw = ws.headers.get("sec-websocket-protocol", "")
        cands = [p.strip() for p in raw.split(",") if p.strip()]
        for p in cands:
            if p != "jarvis-link":
                return p, p   # 선택된 서브프로토콜을 accept 시 echo 해야 함
        # 3) 쿼리스트링(레거시/순수 브라우저 폴백)
        if ALLOW_QUERY_TOKEN_WS:
            qt = ws.query_params.get("token", "")
            if qt:
                _audit("LINK_WS_QUERY_TOKEN", {"ip": self._client_ip(ws)})  # 권장 경로 아님
                return qt, None
        return "", None

    # ─────────────────────────────────────────────────────────────
    #  네트워크 보안 가드 — 레이트리밋 / IP 자동차단 / CSWSH / 헤더
    # ─────────────────────────────────────────────────────────────
    def _client_ip(self, obj) -> str:
        """피어 IP 추출. 단, 신뢰 프록시(TRUSTED_PROXIES) 뒤일 때만 X-Forwarded-For
        선두 홉을 사용(R20). 신뢰하지 않는 출처의 XFF 는 스푸핑 위험이므로 무시."""
        try:
            c = obj.client
            peer = c.host if c else "?"
        except Exception:
            return "?"
        if TRUSTED_PROXIES and peer in TRUSTED_PROXIES:
            try:
                xff = obj.headers.get("x-forwarded-for", "")
                if xff:
                    return xff.split(",")[0].strip() or peer
            except Exception:
                pass
        return peer

    def _rate_ok(self, ip: str) -> bool:
        """슬라이딩 윈도우 레이트리밋 (Round 2). 윈도우 내 RATE_LIMIT_MAX 초과 시 False."""
        now = time.time()
        with self._net_lock:
            dq = self._rl_hits.setdefault(ip, deque(maxlen=RATE_LIMIT_MAX * 4 + 8))
            while dq and now - dq[0] > RATE_LIMIT_WINDOW:
                dq.popleft()
            if len(dq) >= RATE_LIMIT_MAX:
                return False
            dq.append(now)
            # 메모리 위생: 비어있는 ip 엔트리 정리(과도 증가 방지)
            if len(self._rl_hits) > 4096:
                for k in [k for k, v in self._rl_hits.items() if not v]:
                    self._rl_hits.pop(k, None)
            return True

    def _is_banned(self, ip: str) -> bool:
        with self._net_lock:
            until = self._banned.get(ip, 0)
            if until and time.time() < until:
                return True
            if until:
                self._banned.pop(ip, None)   # 만료 정리
            return False

    def _register_auth_fail(self, ip: str, reason: str = "") -> bool:
        """인증 실패 1건 기록(Round 5). 윈도우 내 임계 초과 시 IP 차단 후 True."""
        self.stats["auth_fail"] += 1
        now = time.time()
        with self._net_lock:
            dq = self._authfails.setdefault(ip, deque(maxlen=BAN_MAX_AUTH_FAILS * 4 + 8))
            while dq and now - dq[0] > BAN_WINDOW:
                dq.popleft()
            dq.append(now)
            if len(dq) >= BAN_MAX_AUTH_FAILS:
                self._banned[ip] = now + BAN_SEC
                dq.clear()
                banned = True
            else:
                banned = False
            # R19: 메모리 위생 — 만료 밴/빈 실패큐/유휴 per-IP 카운트 정리(자원 고갈 방지)
            if len(self._authfails) > 2048 or len(self._banned) > 2048:
                for k in [k for k, v in self._authfails.items() if not v]:
                    self._authfails.pop(k, None)
                for k in [k for k, exp in self._banned.items() if exp <= now]:
                    self._banned.pop(k, None)
                for k in [k for k, c in self._ws_ip_counts.items() if c <= 0]:
                    self._ws_ip_counts.pop(k, None)
        if banned:
            self.stats["bans"] += 1
            _audit("LINK_IP_BANNED", {"ip": ip, "reason": reason, "ban_sec": BAN_SEC})
            self._log("WARN", f"🚫 인증 실패 누적 — IP 자동 차단: {ip} ({BAN_SEC}s)")
        return banned

    @staticmethod
    def _origin_allowed(origin: str) -> bool:
        """CSWSH 방어(Round 1). 네이티브 앱(Origin 없음)은 허용, 브라우저 교차 출처는 차단.
        허용 출처 목록이 설정되면 그 목록만 통과, 아니면 로컬호스트 출처만 허용."""
        if not origin:
            return True   # 네이티브 모바일 앱 등 — 브라우저가 아님
        o = origin.lower().rstrip("/")
        if LINK_ALLOWED_ORIGINS:
            return origin in LINK_ALLOWED_ORIGINS or o in [x.lower().rstrip("/") for x in LINK_ALLOWED_ORIGINS]
        return (o.startswith("http://localhost") or o.startswith("https://localhost")
                or o.startswith("http://127.0.0.1") or o.startswith("https://127.0.0.1"))

    def _resolve_tls(self):
        """TLS 인증서/키 경로 결정(R16). Returns (certfile, keyfile) 또는 (None, None).
        모드: '1'/'true'→강제, '0'→비활성, 'auto'→공개 바인딩일 때만 자동 활성."""
        public = self.host not in ("127.0.0.1", "localhost", "::1")
        if LINK_TLS_MODE in ("0", "false", "off", "no"):
            return None, None
        want = LINK_TLS_MODE in ("1", "true", "on", "yes") or (LINK_TLS_MODE == "auto" and public)
        if not want:
            return None, None
        # 1) 운영자가 직접 지정한 인증서 우선
        if LINK_TLS_CERT and LINK_TLS_KEY and os.path.exists(LINK_TLS_CERT) and os.path.exists(LINK_TLS_KEY):
            return LINK_TLS_CERT, LINK_TLS_KEY
        # 2) 자체 서명 PEM 자동 생성(tls_certs)
        try:
            import tls_certs
            info = tls_certs.ensure_link_pem()
            if info:
                return info["certfile"], info["keyfile"]
        except Exception as e:
            self._log("WARN", f"TLS 인증서 준비 실패: {e}")
        if LINK_TLS_MODE in ("1", "true", "on", "yes"):
            self._log("WARN", "⚠ TLS 강제 모드인데 인증서 준비 실패 — 평문으로 기동(cryptography 설치 확인)")
        elif public:
            self._log("WARN", "⚠ 공개 바인딩인데 TLS 인증서 준비 실패 — 평문 노출 위험")
        return None, None

    def _resolve_bind_host(self, host: str) -> str:
        """공개 바인딩 안전 가드(R13). 명시 허용(JARVIS_LINK_ALLOW_PUBLIC=1) 없이
        0.0.0.0 등 외부 노출 주소면 로컬호스트로 강등하여 사고성 노출을 방지."""
        local = host in ("127.0.0.1", "localhost", "::1")
        if local or ALLOW_PUBLIC_BIND:
            if not local and ALLOW_PUBLIC_BIND and self._token_autogen and not LINK_FORCE_HTTPS:
                self._log("WARN", "⚠ 공개 바인딩 + 자동발급 토큰 + 평문 — TLS/고정토큰 사용을 강력 권장")
            return host
        self._log("WARN", f"공개 바인딩 차단: {host} → 127.0.0.1 강등 "
                          f"(외부 노출하려면 JARVIS_LINK_ALLOW_PUBLIC=1 명시)")
        _audit("LINK_PUBLIC_BIND_BLOCKED", {"requested": host})
        return "127.0.0.1"

    def security_report(self) -> dict:
        """유효 보안 태세 스냅샷(R15) — 약한 설정 가시화."""
        public = self.host not in ("127.0.0.1", "localhost", "::1")
        return {
            "bind": self.host, "public": public, "force_https": LINK_FORCE_HTTPS,
            "tls_mode": LINK_TLS_MODE, "tls_active": self._tls,
            "ws_query_token": ALLOW_QUERY_TOKEN_WS,
            "trusted_proxies": sorted(TRUSTED_PROXIES) or ["(none)"],
            "broadcast_timeout": BROADCAST_TIMEOUT,
            "token_source": "autogen" if self._token_autogen else "configured",
            "query_token_rest": ALLOW_QUERY_TOKEN_REST,
            "max_clients": LINK_MAX_CLIENTS, "ws_max_per_ip": WS_MAX_PER_IP,
            "rate_limit": f"{RATE_LIMIT_MAX}/{RATE_LIMIT_WINDOW}s",
            "ban_policy": f"{BAN_MAX_AUTH_FAILS}fails/{BAN_WINDOW}s→{BAN_SEC}s",
            "approval_lock": f"{APPROVAL_MAX_FAILS}fails→{APPROVAL_LOCK_SEC}s",
            "pending_max": PENDING_MAX, "max_body": MAX_BODY_BYTES,
            "ws_idle": WS_IDLE_TIMEOUT, "ws_max_session": WS_MAX_SESSION,
            "allowed_origins": LINK_ALLOWED_ORIGINS or ["localhost(default)"],
        }

    def _emit_security_report(self):
        rep = self.security_report()
        _audit("LINK_SECURITY_POSTURE", rep)
        warns = []
        if rep["public"] and not rep["tls_active"]:
            warns.append("공개바인딩+평문(TLS미적용)")
        if rep["public"] and rep["token_source"] == "autogen":
            warns.append("공개바인딩+자동토큰")
        if rep["query_token_rest"]:
            warns.append("REST쿼리토큰허용")
        tag = f" ⚠ 점검필요({', '.join(warns)})" if warns else " ✓ 양호"
        self._log("LOCK", f"보안 태세: bind={rep['bind']} rate={rep['rate_limit']} "
                          f"ban={rep['ban_policy']}{tag}")

    @staticmethod
    def _apply_security_headers(resp, is_https: bool):
        for k, v in _SECURITY_HEADERS.items():
            resp.headers[k] = v
        # Round 8: 서버/프레임워크 버전 은폐(풋프린팅 차단)
        resp.headers["Server"] = SERVER_HEADER
        if is_https or LINK_FORCE_HTTPS:
            resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"

    # ─────────────────────────────────────────────────────────────
    #  서버 라이프사이클 (자체 스레드 + 이벤트 루프)
    # ─────────────────────────────────────────────────────────────
    def start(self) -> bool:
        if not self.available:
            self._log("WARN", "FastAPI/uvicorn 미설치 — JARVIS Link 비활성 "
                              "(pip install fastapi 'uvicorn[standard]')")
            return False
        if self._thread and self._thread.is_alive():
            return True
        # Round 13: 공개 바인딩 안전 가드 — 명시 허용 없이 0.0.0.0/외부 IP 노출 금지(실수 방지)
        self.host = self._resolve_bind_host(self.host)
        # Round 16: 실제 TLS 적용 — 평문 전송 구간 암호화(토큰/데이터 스니핑 차단)
        certfile, keyfile = self._resolve_tls()
        self._tls = bool(certfile and keyfile)
        cfg_kw = dict(host=self.host, port=self.port,
                      log_level="warning", loop="asyncio",
                      ws_ping_interval=20, ws_ping_timeout=20,
                      server_header=False, date_header=True,
                      timeout_keep_alive=15)
        if self._tls:
            cfg_kw.update(ssl_certfile=certfile, ssl_keyfile=keyfile)
        config = uvicorn.Config(self._app, **cfg_kw)
        # Round 15: 기동 시 유효 보안 태세 자가진단 + 감사
        self._emit_security_report()
        self._server = uvicorn.Server(config)
        # uvicorn 의 시그널 핸들러 설치 비활성화(메인 스레드가 아니므로)
        self._server.install_signal_handlers = lambda: None
        self._thread = threading.Thread(target=self._server.run, daemon=True, name="jarvis-link")
        self._thread.start()
        ready = self._ready.wait(timeout=8.0)
        if ready:
            scope = "외부망(LAN/모바일)" if self.host == "0.0.0.0" else "로컬"
            scheme = "wss" if self._tls else "ws"
            tls_tag = " 🔒TLS" if self._tls else " (평문)"
            self._log("OK", f"🔗 JARVIS Link 가동 — {scheme}://{self.host}:{self.port}/ws/hud ({scope}){tls_tag}")
            if self._token_autogen:
                # 평문 토큰을 로그에 남기지 않는다(로그 유출 방어). 0600 파일에만 기록하고 로그는 마스킹.
                path = self._persist_token_securely()
                self._log("LOCK", f"접속 토큰(자동발급) {_mask_code(self.token)} — 전체 값은 {path} (0600) 참조 / "
                                  f".env JARVIS_LINK_TOKEN 으로 고정 권장")
            self._push_channel_report()
        else:
            self._log("WARN", "JARVIS Link 기동 확인 실패(타임아웃)")
        return ready

    def _persist_token_securely(self) -> str:
        """자동발급 토큰을 소유자 전용(0600) 파일에 저장. 로그 평문 노출 대체."""
        path = os.path.abspath(os.getenv("JARVIS_LINK_TOKEN_FILE", ".jarvis_link_token"))
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(self.token + "\n")
            try:
                os.chmod(path, 0o600)
            except Exception:
                pass
        except Exception as e:
            self._log("WARN", f"토큰 파일 저장 실패: {e}")
        return path

    def stop(self):
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)

    # ─────────────────────────────────────────────────────────────
    #  Pub/Sub — 어느 스레드에서도 호출 가능(서버 루프로 안전 위임)
    # ─────────────────────────────────────────────────────────────
    def _broadcast_threadsafe(self, payload: dict):
        if not self.available or not self._ready.is_set() or self._loop is None:
            return
        if not self._clients:
            return
        import asyncio
        try:
            asyncio.run_coroutine_threadsafe(self._broadcast(payload), self._loop)
        except Exception:
            pass

    async def _broadcast(self, payload: dict):
        import asyncio
        clients = list(self._clients)
        if not clients:
            self.stats["broadcasts"] += 1
            return

        async def _send(ws):
            # R18: 클라이언트별 송신 타임아웃 — 느린 클라 1개가 전체를 막는
            #      head-of-line blocking 방지. 실패/지연 소켓은 폐기 대상으로 반환.
            try:
                await asyncio.wait_for(ws.send_json(payload), timeout=BROADCAST_TIMEOUT)
                return None
            except Exception:
                return ws

        results = await asyncio.gather(*[_send(ws) for ws in clients], return_exceptions=True)
        for r in results:
            if r is not None and not isinstance(r, BaseException):
                self._clients.discard(r)
                _audit("LINK_WS_SLOW_DROP", {"reason": "send_timeout_or_error"})
        self.stats["broadcasts"] += 1

    def publish(self, channel: str, data: dict):
        """범용 발행 — {type, ...data, ts} 형태로 브로드캐스트."""
        payload = {"type": channel, **(data or {}), "ts": time.time()}
        self._broadcast_threadsafe(payload)

    def publish_hud(self, hud: dict | None = None, matrix: dict | None = None,
                    radar: list | None = None, state: str = "", away: Optional[bool] = None):
        """HUD 실시간 데이터(hud_values·매트릭스 점수·레이더 좌표)를 스냅샷 갱신 + 브로드캐스트."""
        with self._state_lock:
            snap = self._snapshot
            if hud is not None: snap["hud"] = hud
            if matrix is not None: snap["matrix"] = matrix
            if radar is not None: snap["radar"] = radar
            if state: snap["state"] = state
            if away is not None: self._away = bool(away); snap["away"] = self._away
            snap["ts"] = time.time()
            payload = dict(snap); payload["type"] = "hud"
        self._broadcast_threadsafe(payload)

    def get_snapshot(self) -> dict:
        with self._state_lock:
            return dict(self._snapshot)

    # ─────────────────────────────────────────────────────────────
    #  외출 상태
    # ─────────────────────────────────────────────────────────────
    def set_away(self, away: bool):
        with self._state_lock:
            self._away = bool(away)
            self._snapshot["away"] = self._away
        self.publish("presence", {"away": self._away})

    def is_away(self) -> bool:
        with self._state_lock:
            return self._away

    # ─────────────────────────────────────────────────────────────
    #  원격 승인 — 일회성 코드 발급/소비 + 코어 소비 큐
    # ─────────────────────────────────────────────────────────────
    def issue_approval(self, context: dict | None = None) -> str:
        """리밸런싱 승인용 일회성 코드를 발급(TTL). 푸시 메시지에 동봉."""
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))  # Base36 8자(≈41.4비트) — 잠금정책과 함께 추측공격 차단
        with self._approval_lock:
            self._prune_expired()
            # Round 11: 대기 코드 상한 — 초과 시 가장 오래된 코드 폐기(메모리 고갈 DoS 차단)
            while len(self._pending) >= PENDING_MAX:
                oldest = min(self._pending, key=lambda c: self._pending[c]["ts"])
                self._pending.pop(oldest, None)
                _audit("LINK_PENDING_EVICT", {"reason": "cap", "max": PENDING_MAX})
            self._pending[code] = {"context": dict(context or {}), "ts": time.time()}
        return code

    def _approval_is_locked(self) -> bool:
        with self._approval_lock:
            return time.time() < self._approval_locked_until

    def _record_approval_failure(self) -> bool:
        """실패 1건 기록. 윈도우 내 임계 초과 시 잠금 설정 후 True 반환."""
        now = time.time()
        with self._approval_lock:
            self._approval_fails.append(now)
            recent = [t for t in self._approval_fails if now - t <= APPROVAL_FAIL_WINDOW]
            if len(recent) >= APPROVAL_MAX_FAILS:
                self._approval_locked_until = now + APPROVAL_LOCK_SEC
                self._approval_fails.clear()
                return True
        return False

    def _reset_approval_failures(self):
        with self._approval_lock:
            self._approval_fails.clear()
            self._approval_locked_until = 0.0

    def _consume_approval_code(self, code: str):
        """코드 검증 + 1회성 소비. Returns (ok, context)."""
        if not code:
            return False, None
        with self._approval_lock:
            self._prune_expired()
            rec = self._pending.pop(code, None)
        if not rec:
            return False, None
        if time.time() - rec["ts"] > APPROVAL_TTL_SEC:
            return False, None
        return True, rec.get("context")

    def _prune_expired(self):
        now = time.time()
        for c in [c for c, r in self._pending.items() if now - r["ts"] > APPROVAL_TTL_SEC]:
            self._pending.pop(c, None)

    def get_approval_events(self) -> list:
        """코어(jarvis_v2)가 매 루프 폴링 — 승인된 리밸런싱 이벤트를 모두 꺼낸다."""
        out = []
        while True:
            try:
                out.append(self._approval_q.get_nowait())
            except queue.Empty:
                break
        return out

    # ─────────────────────────────────────────────────────────────
    #  모바일 푸시 (텔레그램 / 디스코드 / 범용 웹훅) — 비블로킹
    # ─────────────────────────────────────────────────────────────
    def push_alert(self, text: str, context: dict | None = None,
                   issue_code: bool = True, blocking: bool = False) -> dict:
        """외출 중 위험 알림을 모바일로 푸시. issue_code=True면 원격 승인 코드를 발급·동봉.

        Returns: {"channels":[...], "approval_code": "...", "message": "..."}
        """
        approval_code = self.issue_approval(context) if issue_code else ""
        message = text
        if approval_code:
            message = (f"{text}\n\n[원격 승인 코드] {approval_code}\n"
                       f"모바일 앱에서 이 코드로 리밸런싱을 승인/거부할 수 있습니다.")
        result = {"channels": [], "approval_code": approval_code, "message": message}

        def _send():
            sent = []
            try:
                _tg_token, _tg_chat = _tg_creds()
                if _tg_token and _tg_chat and _http is not None:
                    if self._post_telegram(message):
                        sent.append("telegram")
                if DISCORD_WEBHOOK and _http is not None:
                    if self._post_json(DISCORD_WEBHOOK, {"content": message}):
                        sent.append("discord")
                if GENERIC_WEBHOOK and _http is not None:
                    if self._post_json(GENERIC_WEBHOOK,
                                       {"text": message, "context": context or {},
                                        "approval_code": approval_code}):
                        sent.append("webhook")
            except Exception as e:
                self._log("WARN", f"푸시 실패: {e}")
            result["channels"] = sent
            self.stats["pushes"] += len(sent)
            # 모바일 미연결/채널 미설정이어도 WS 로는 항상 알림 발행(앱이 떠 있으면 수신)
            self.publish("alert", {"message": text, "approval_code": approval_code,
                                   "context": context or {}})
            _audit("LINK_PUSH", {"channels": sent, "approval_code": _mask_code(approval_code),
                                 "preview": text[:60]})
            if sent:
                self._log("INTR", f"📤 모바일 푸시 전송: {', '.join(sent)}")
            else:
                self._log("INFO", "푸시 채널 미설정 — WebSocket 으로만 알림 발행")

        if blocking:
            _send()
        else:
            threading.Thread(target=_send, daemon=True).start()
        return result

    def _post_telegram(self, text: str) -> bool:
        # 토큰/CHAT_ID 는 호출 시점에 읽는다 — import 순서상 .env 가 모듈 로드 뒤에
        # load_dotenv() 되더라도(=모듈 상수가 비어 있어도) 항상 최신 값으로 전송.
        token, chat = _tg_creds()
        if not token or not chat or _http is None:
            return False
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        try:
            r = _http.post(url, json={"chat_id": chat, "text": text},
                           timeout=(5, 10))
            return getattr(r, "status_code", 0) == 200
        except Exception:
            return False

    def _post_json(self, url: str, payload: dict) -> bool:
        try:
            r = _http.post(url, json=payload, timeout=(5, 10))
            return getattr(r, "status_code", 0) in (200, 201, 202, 204)
        except Exception:
            return False

    def _push_channel_report(self):
        ch = []
        _tg_token, _tg_chat = _tg_creds()
        if _tg_token and _tg_chat: ch.append("Telegram")
        if DISCORD_WEBHOOK: ch.append("Discord")
        if GENERIC_WEBHOOK: ch.append("Webhook")
        if ch:
            self._log("OK", f"푸시 채널 활성: {', '.join(ch)}")
        else:
            self._log("INFO", "푸시 채널 미설정 — 위험 알림은 WebSocket 으로만 발행 "
                             "(TELEGRAM_BOT_TOKEN/CHAT_ID, DISCORD_WEBHOOK_URL 설정 권장)")


def _peer(obj) -> str:
    try:
        c = obj.client
        return f"{c.host}:{c.port}" if c else "?"
    except Exception:
        return "?"


def _mask_code(code: str) -> str:
    if not code:
        return ""
    return code[:2] + "*" * max(0, len(code) - 2)


# ── 프로세스 전역 싱글톤 ──
_link_instance: Optional[JarvisLink] = None


def get_link() -> Optional[JarvisLink]:
    return _link_instance


def init_link(**kwargs) -> JarvisLink:
    global _link_instance
    if _link_instance is None:
        _link_instance = JarvisLink(**kwargs)
    return _link_instance


if __name__ == "__main__":
    # 단독 데모 — 서버를 띄우고 주기적으로 더미 HUD 데이터를 브로드캐스트
    link = init_link()
    if not link.start():
        raise SystemExit("FastAPI 미설치")
    print(f"토큰: {link.token}")
    print(f"WS:   ws://{link.host}:{link.port}/ws/hud?token={link.token}")
    try:
        i = 0
        while True:
            i += 1
            link.publish_hud(
                hud={"state": "IDLE", "tick": i},
                matrix={"종합": 70 + i % 20, "기술적": 40, "고래": 18, "소셜": 15},
                radar=[{"label": "기술적 분석", "value": 42, "max": 50},
                       {"label": "고래 플로우", "value": 20, "max": 25},
                       {"label": "소셜 하이프", "value": 19, "max": 25}],
                state="IDLE")
            time.sleep(2)
    except KeyboardInterrupt:
        link.stop()
