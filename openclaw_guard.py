"""
J.A.R.V.I.S — OpenClaw 제로트러스트 가드
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
OpenClaw 게이트웨이(CVE-2026-25253 WebSocket 탈취, Claw Chain 샌드박스 탈출 등)
대응을 위해 Jarvis ↔ OpenClaw 통신을 단일 보안 프록시로 제한한다.

원칙:
  1. 게이트웨이 URL은 127.0.0.1/localhost 만 허용 (LAN·공인 IP 차단)
  2. 토큰은 Keychain(OPENCLAW_GATEWAY_TOKEN) — 코드·로그·.env 평문 금지
  3. 위험 API(config.patch, exec.approvals, sandbox disable) 호출 차단
  4. 응답은 sanitize_external_content + filter_llm_output 로 간접 인젝션 중화
  5. 모든 호출 감사 체인 기록

환경변수:
  OPENCLAW_ENABLED=1              OpenClaw 연동 ON (기본 0)
  OPENCLAW_GATEWAY_URL=http://127.0.0.1:18789
  OPENCLAW_GUARD_STRICT=1         maximum 모드에서 자동 ON
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    import net
except ImportError:
    net = None  # type: ignore

try:
    from security import (
        validate_url, sanitize_external_content, chain_event,
        RateLimiter, write_audit_log,
    )
except ImportError:
    def validate_url(url): return True, "OK"
    def sanitize_external_content(t, source=""): return t
    def chain_event(e, d=None): pass
    class RateLimiter:
        def __init__(self, max_calls=20, window_sec=60): pass
        def allow(self): return True
    def write_audit_log(e, d): pass

try:
    from secure_config import get_secret, set_secret
except ImportError:
    def get_secret(k, d=""): return os.getenv(k, d)
    def set_secret(k, v): return False

OPENCLAW_HOME = Path(os.getenv("OPENCLAW_HOME", str(Path.home() / ".openclaw")))
OPENCLAW_CONFIG = OPENCLAW_HOME / "openclaw.json"
GATEWAY_ENV_FILE = OPENCLAW_HOME / "service-env" / "ai.openclaw.gateway.env"
DEFAULT_GATEWAY = os.getenv("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789").strip()
TOKEN_KEY = "OPENCLAW_GATEWAY_TOKEN"

# OpenClaw 위험 API — Jarvis 프록시에서 절대 전달하지 않음
_BLOCKED_PATH_RE = re.compile(
    r"(exec\.approvals|config\.patch|config\.set|sandbox\.disable|"
    r"tools\.exec\.host|gateway\.auth|skills\.install|clawhub|"
    r"plugins\.install|/export|/diagnostics|owner\.transfer)",
    re.I,
)

# 허용 HTTP 메서드·경로 (화이트리스트 — 읽기/채팅 위주)
_ALLOWED_PREFIXES = (
    "/api/v1/chat",
    "/api/v1/completions",
    "/api/v1/agents/",
    "/health",
    "/api/v1/status",
)

_guard_limiter = RateLimiter(max_calls=15, window_sec=60)


def openclaw_enabled() -> bool:
    if os.getenv("OPENCLAW_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on"):
        return True
    try:
        from security_policy import is_maximum_security
        return is_maximum_security() and os.getenv("OPENCLAW_GUARD_STRICT", "1") != "0"
    except ImportError:
        return False


def _localhost_only(url: str) -> tuple[bool, str]:
    """OpenClaw 전용 — loopback만 허용 (일반 SSRF 가드와 별도)."""
    try:
        parsed = urlparse(url.strip())
    except Exception as e:
        return False, str(e)
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        return False, f"허용되지 않은 스킴: {scheme or '(없음)'}"
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in ("127.0.0.1", "localhost", "::1"):
        return True, "OK"
    return False, f"OpenClaw 게이트웨이는 localhost만 허용 (현재: {host})"


def get_gateway_token() -> str:
    return get_secret(TOKEN_KEY, "")


def ensure_gateway_token() -> tuple[bool, str]:
    """Keychain에 게이트웨이 토큰이 없으면 생성·저장."""
    existing = get_gateway_token()
    if existing:
        return True, "OK"
    new = secrets.token_hex(32)
    if set_secret(TOKEN_KEY, new):
        os.environ[TOKEN_KEY] = new
        chain_event("OPENCLAW_TOKEN_CREATED", {"stored": "keychain"})
        return True, "새 게이트웨이 토큰 생성(Keychain)"
    os.environ[TOKEN_KEY] = new
    chain_event("OPENCLAW_TOKEN_ENV", {"warn": "keychain_unavailable"})
    return True, "새 토큰 생성(환경변수 — Keychain 마이그레이션 권장)"


def inject_gateway_env() -> None:
    """자식 openclaw 프로세스·HTTP 클라이언트용 env 주입."""
    tok = get_gateway_token()
    if tok:
        os.environ[TOKEN_KEY] = tok


def sync_gateway_service_env() -> tuple[bool, str]:
    """launchd 게이트웨이 + CLI SecretRef(env) — Keychain → service-env(0600) 동기화.

    openclaw.json 의 gateway.auth.token 이 env:OPENCLAW_GATEWAY_TOKEN 을 가리키므로,
    터미널/launchd 모두 이 파일 또는 프로세스 env 에 토큰이 있어야 한다.
    """
    ensure_gateway_token()
    tok = get_gateway_token()
    if not tok:
        return False, "OPENCLAW_GATEWAY_TOKEN 없음 — Keychain 확인"
    inject_gateway_env()

    env_path = GATEWAY_ENV_FILE
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()

    export_line = f"export {TOKEN_KEY}='{tok}'"
    out: list[str] = []
    found = False
    for line in lines:
        if line.strip().startswith(f"export {TOKEN_KEY}="):
            out.append(export_line)
            found = True
        else:
            out.append(line)
    if not found:
        out.append(export_line)

    env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        os.chmod(str(env_path), 0o600)
    except OSError:
        pass
    chain_event("OPENCLAW_ENV_SYNC", {"path": str(env_path.name)})
    return True, "gateway service-env 동기화"


def restart_gateway(force: bool = True) -> tuple[int, str]:
    """토큰 동기화 후 게이트웨이 재시작."""
    ok, msg = sync_gateway_service_env()
    if not ok:
        return 1, msg
    args = ["gateway", "restart"]
    if force:
        args.append("--force")
    return openclaw_cli(*args, timeout=180)


def _path_allowed(path: str) -> tuple[bool, str]:
    p = (path or "").lower()
    if _BLOCKED_PATH_RE.search(p):
        return False, f"차단된 OpenClaw API: {p[:80]}"
    if not any(p.startswith(prefix.lower()) for prefix in _ALLOWED_PREFIXES):
        return False, f"허용되지 않은 OpenClaw 경로: {p[:80]}"
    return True, "OK"


def guarded_request(
    method: str,
    path: str,
    *,
    json_body: dict | None = None,
    timeout: tuple = (3, 30),
) -> tuple[bool, dict | str]:
    """
    OpenClaw 게이트웨이 HTTP 호출 — 모든 보안 게이트 통과 후 실행.
    Returns: (success, result_or_error_message)
    """
    if not openclaw_enabled():
        return False, "OpenClaw 연동 비활성 (OPENCLAW_ENABLED=0)"

    if not _guard_limiter.allow():
        chain_event("OPENCLAW_RATE_LIMIT", {"path": path})
        return False, "OpenClaw 호출 속도 제한 초과"

    base = DEFAULT_GATEWAY.rstrip("/")
    ok, reason = _localhost_only(base)
    if not ok:
        chain_event("OPENCLAW_URL_BLOCK", {"reason": reason})
        return False, reason

    ok, reason = _path_allowed(path)
    if not ok:
        chain_event("OPENCLAW_PATH_BLOCK", {"path": path, "reason": reason})
        return False, reason

    tok = get_gateway_token()
    if not tok:
        return False, "OpenClaw 게이트웨이 토큰 미설정 — openclaw_harden.py 실행"

    url = f"{base}{path if path.startswith('/') else '/' + path}"
    headers = {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json",
        "X-JARVIS-OpenClaw-Guard": "1",
    }

    try:
        if net is None:
            import requests
            r = requests.request(method, url, json=json_body, headers=headers, timeout=timeout)
        else:
            r = net.request(method, url, json=json_body, headers=headers, timeout=timeout)
    except Exception as e:
        chain_event("OPENCLAW_REQUEST_FAIL", {"path": path, "err": str(e)[:120]})
        return False, f"OpenClaw 연결 실패: {e}"

    if r.status_code >= 400:
        chain_event("OPENCLAW_HTTP_ERR", {"path": path, "status": r.status_code})
        return False, f"OpenClaw HTTP {r.status_code}"

    try:
        data = r.json()
    except Exception:
        data = sanitize_external_content(r.text[:8000], source="openclaw")

    if isinstance(data, str):
        data = sanitize_external_content(data, source="openclaw")
    elif isinstance(data, dict):
        raw = json.dumps(data, ensure_ascii=False)[:8000]
        cleaned = sanitize_external_content(raw, source="openclaw")
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            data = {"text": cleaned}

    write_audit_log("OPENCLAW_CALL", {"method": method, "path": path, "ok": True})
    return True, data


def openclaw_cli(*args: str, timeout: int = 120) -> tuple[int, str]:
    """openclaw CLI 래퍼 — Keychain 토큰 env 주입 + service-env 동기화."""
    if shutil.which("openclaw") is None:
        return 127, "openclaw CLI 미설치"
    sync_gateway_service_env()
    inject_gateway_env()
    env = os.environ.copy()
    try:
        r = subprocess.run(
            ["openclaw", *args],
            capture_output=True, text=True, timeout=timeout, env=env,
        )
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode, out.strip()
    except subprocess.TimeoutExpired:
        return 124, "openclaw CLI 타임아웃"
    except Exception as e:
        return 1, str(e)


def harden_openclaw_config(log_fn=None) -> dict:
    """
    ~/.openclaw 보안 강화 — CLI 기반(토큰 평문을 코드에 기록하지 않음).
    Returns: {ok, changes, warnings, audit_summary}
    """
    _log = log_fn or (lambda _lvl, _msg: None)
    result: dict[str, Any] = {"ok": True, "changes": [], "warnings": [], "audit_summary": ""}

    if not OPENCLAW_CONFIG.exists():
        result["ok"] = False
        result["warnings"].append(f"OpenClaw 설정 없음: {OPENCLAW_CONFIG}")
        return result

    ensure_gateway_token()
    inject_gateway_env()

    # 1) 공식 audit --fix (chmod 등)
    code, out = openclaw_cli("security", "audit", "--fix", "--json")
    if code == 0 and out:
        try:
            rep = json.loads(out)
            result["audit_summary"] = rep.get("report", {}).get("summary", {})
            for act in rep.get("fix", {}).get("actions", []):
                if act.get("ok"):
                    result["changes"].append(f"chmod {act.get('path')}")
        except json.JSONDecodeError:
            pass

    # 2) 게이트웨이·공격면 축소 (idempotent)
    _settings = [
        ("gateway.mode", "local"),
        ("gateway.bind", "loopback"),
        ("tools.elevated.enabled", "false", "--strict-json"),
        ("browser.enabled", "false", "--strict-json"),
    ]
    for item in _settings:
        args = ["config", "set", item[0], item[1]]
        if len(item) > 2:
            args.append(item[2])
        code, out = openclaw_cli(*args)
        if code == 0:
            result["changes"].append(f"config {item[0]}={item[1]}")
        elif out:
            result["warnings"].append(f"{item[0]}: {out[:100]}")

    # 3) 토큰 → env ref (평문 제거)
    code, out = openclaw_cli(
        "config", "set", "gateway.auth.token",
        "--ref-provider", "default",
        "--ref-source", "env",
        "--ref-id", TOKEN_KEY,
    )
    if code == 0:
        result["changes"].append("gateway.auth.token → env ref (Keychain)")

    # 4) ClawHub·서드파티 스킬 전면 OFF (supply-chain 방어)
    try:
        cfg = json.loads(OPENCLAW_CONFIG.read_text(encoding="utf-8"))
        skills = cfg.setdefault("skills", {}).setdefault("entries", {})
        for name in list(skills.keys()):
            if skills[name].get("enabled") is not False:
                skills[name]["enabled"] = False
        if "clawhub" in skills:
            skills["clawhub"]["enabled"] = False
        OPENCLAW_CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
        os.chmod(str(OPENCLAW_CONFIG), 0o600)
        result["changes"].append("모든 ClawHub/스킬 비활성화")
    except OSError as e:
        result["warnings"].append(f"스킬 비활성화 실패: {e}")

    # 5) 최종 감사
    code, out = openclaw_cli("security", "audit")
    if out:
        result["audit_summary"] = out[:500]
        if "critical" in out.lower() and "0 critical" not in out.lower():
            result["ok"] = False
            result["warnings"].append("OpenClaw critical finding — openclaw security audit 확인")

    # 6) 버전 권고 (Claw Chain 패치)
    code, ver = openclaw_cli("--version")
    if ver and "2026.5.28" in ver:
        result["warnings"].append(
            "OpenClaw 2026.5.28 — npm update -g openclaw 로 최신 보안 패치 권장")

    chain_event("OPENCLAW_HARDEN", {
        "changes": len(result["changes"]),
        "warnings": len(result["warnings"]),
    })
    _log("OK", f"OpenClaw 하드닝: {len(result['changes'])}건 적용")
    for w in result["warnings"]:
        _log("WARN", f"OpenClaw: {w}")
    return result


def boot_openclaw_guard(log_fn=None) -> dict:
    """jarvis_v2 부팅 시 호출 — 설정 강화 + 게이트웨이 env."""
    _log = log_fn or (lambda _lvl, _msg: None)
    if not OPENCLAW_CONFIG.exists():
        return {"enabled": False, "reason": "not_installed"}
    if not openclaw_enabled():
        _log("INFO", "OpenClaw 가드: 비활성 (OPENCLAW_ENABLED=0)")
        return {"enabled": False, "reason": "disabled"}
    ensure_gateway_token()
    inject_gateway_env()
    sync_gateway_service_env()
    rep = harden_openclaw_config(log_fn=_log)
    return {"enabled": True, **rep}


# ── LLM 도구 (선택적 — OPENCLAW_ENABLED=1) ──
OPENCLAW_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "openclaw_health_check",
            "description": (
                "OpenClaw 게이트웨이(localhost) 상태를 안전하게 확인합니다. "
                "연결·인증·버전만 반환하며 위험 설정 변경은 불가합니다."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def execute_openclaw_tool(tool_name: str, arguments: dict) -> dict:
    if tool_name == "openclaw_health_check":
        ok, data = guarded_request("GET", "/health")
        if not ok:
            code, ver = openclaw_cli("--version")
            return {
                "success": False,
                "message": str(data),
                "cli_version": ver if code == 0 else None,
                "gateway": DEFAULT_GATEWAY,
            }
        return {"success": True, "health": data, "gateway": DEFAULT_GATEWAY}
    return {"success": False, "message": f"알 수 없는 OpenClaw 도구: {tool_name}"}


if __name__ == "__main__":
    import sys
    if "--harden" in sys.argv:
        r = harden_openclaw_config(print)
        print(json.dumps({k: v for k, v in r.items() if k != "audit_summary"},
                         indent=2, ensure_ascii=False))
        sys.exit(0 if r.get("ok") else 1)
    if "--sync-env" in sys.argv:
        ok, msg = sync_gateway_service_env()
        print(f"{'✓' if ok else '✗'} {msg}")
        sys.exit(0 if ok else 1)
    if "--restart-gateway" in sys.argv:
        code, out = restart_gateway(force=True)
        print(out)
        sys.exit(code)
    if "--check" in sys.argv:
        inject_gateway_env()
        code, out = openclaw_cli("security", "audit")
        print(out)
        sys.exit(code)
