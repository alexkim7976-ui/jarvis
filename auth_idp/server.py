"""
J.A.R.V.I.S — OIDC IDP HTTP 표면(FastAPI)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
인증 서버(AS) 엔드포인트:
  POST /oauth/token                       — client_credentials / authorization_code / refresh_token
  GET  /.well-known/jwks.json             — 공개키(JWKS)
  GET  /.well-known/openid-configuration  — OIDC 디스커버리
자원 서버(RS) 연동:
  ResourceGuard(idp).require(scope)       — Bearer 액세스 토큰 검증 FastAPI 의존성
jarvis_link 연동:
  bind_jarvis_link(idp, link)             — 일회성 승인코드를 Authorization Code 로 사용
"""
from __future__ import annotations
import base64, json
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .idp import IdentityProvider, OAuthError

_MAX_BODY = 8192


async def _parse_token_request(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > _MAX_BODY:
        raise OAuthError("invalid_request", "요청 본문 과대", 413)
    ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype == "application/json":
        try:
            d = json.loads(raw or b"{}")
            return {k: ("" if v is None else str(v)) for k, v in d.items()}
        except Exception:
            raise OAuthError("invalid_request", "JSON 파싱 실패", 400)
    # 기본: application/x-www-form-urlencoded (OAuth 표준)
    q = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
    return {k: v[0] for k, v in q.items()}


def _basic_client(request: Request):
    """HTTP Basic 클라이언트 인증(client_secret_basic) 추출."""
    h = request.headers.get("authorization", "")
    if h.lower().startswith("basic "):
        try:
            cid, _, csec = base64.b64decode(h[6:]).decode().partition(":")
            return cid, csec
        except Exception:
            return None
    return None


def _err_response(e: OAuthError) -> JSONResponse:
    return JSONResponse({"error": e.error, "error_description": e.description},
                        status_code=e.status, headers={"Cache-Control": "no-store"})


def install_error_handler(app: FastAPI):
    """RS 앱에서도 OAuthError → 표준 JSON 으로 변환하도록 등록."""
    @app.exception_handler(OAuthError)
    async def _h(request: Request, exc: OAuthError):   # noqa: ANN001
        return _err_response(exc)
    return app


def build_app(idp: IdentityProvider) -> FastAPI:
    app = FastAPI(title="JARVIS OIDC IDP", docs_url=None, redoc_url=None, openapi_url=None)
    install_error_handler(app)

    @app.post("/oauth/token")
    async def token(request: Request):
        f = await _parse_token_request(request)
        grant = f.get("grant_type", "")
        cid, csec = f.get("client_id"), f.get("client_secret")
        if not cid:                                   # 폼에 없으면 Basic 헤더 시도
            basic = _basic_client(request)
            if basic:
                cid, csec = basic
        if grant == "client_credentials":
            resp = idp.grant_client_credentials(cid, csec, f.get("scope"))
        elif grant == "authorization_code":
            resp = idp.grant_authorization_code(cid, csec, f.get("code"), f.get("scope"))
        elif grant == "refresh_token":
            resp = idp.grant_refresh_token(cid, csec, f.get("refresh_token"))
        else:
            raise OAuthError("unsupported_grant_type", f"미지원 grant: {grant}", 400)
        return JSONResponse(resp, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})

    @app.get("/.well-known/jwks.json")
    async def jwks():
        return JSONResponse(idp.jwks(), headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/.well-known/openid-configuration")
    async def discovery():
        return JSONResponse(idp.discovery())

    return app


class ResourceGuard:
    """자원 서버(RS) 의존성 팩토리 — Bearer 액세스 토큰을 JWKS 공개키로 검증.

    사용:
        guard = ResourceGuard(idp)
        @rs_app.get("/secure")
        async def secure(claims: dict = Depends(guard.require("rebalance"))):
            return {"sub": claims["sub"]}
    """
    def __init__(self, idp: IdentityProvider):
        self.idp = idp

    def require(self, scope=None):
        async def _dep(request: Request) -> dict:
            h = request.headers.get("authorization", "")
            if not h.lower().startswith("bearer "):
                raise OAuthError("invalid_token", "Bearer 액세스 토큰 필요", 401)
            return self.idp.verify_access(h[7:].strip(), required_scope=scope)
        return _dep


def bind_jarvis_link(idp: IdentityProvider, link) -> IdentityProvider:
    """jarvis_link 의 일회성 승인코드(issue_approval)를 OAuth Authorization Code 로 연결.

    link._consume_approval_code(code) -> (ok, context) 시그니처가
    IDP 의 authorization_code_verifier 규약과 일치한다.
    """
    idp.authorization_code_verifier = lambda code: link._consume_approval_code(code)
    return idp
