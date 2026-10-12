"""
J.A.R.V.I.S — 중앙 보안 정책 (Security Policy)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
JARVIS_SECURITY_LEVEL 환경변수로 보안 강도를 일괄 제어한다.

  standard  — 기본 (기존 동작, 개발·일상 사용)
  strict    — SSRF 엄격·감사 엄격·속도 제한 강화·FDS 도구 게이트
  maximum   — strict + Keychain 전용·샌드박스 강제·고위험 도구 FDS 필수·
              네트워크 이그레스 SSRF 검증

부팅 시 jarvis_v2.py 가 apply_environment_hardening() 을 최우선 호출한다.
"""
from __future__ import annotations

import os
from enum import IntEnum


class SecurityLevel(IntEnum):
    STANDARD = 0
    STRICT = 1
    MAXIMUM = 2


_LEVEL_ALIASES = {
    "standard": SecurityLevel.STANDARD,
    "normal": SecurityLevel.STANDARD,
    "default": SecurityLevel.STANDARD,
    "0": SecurityLevel.STANDARD,
    "strict": SecurityLevel.STRICT,
    "high": SecurityLevel.STRICT,
    "1": SecurityLevel.STRICT,
    "maximum": SecurityLevel.MAXIMUM,
    "max": SecurityLevel.MAXIMUM,
    "paranoid": SecurityLevel.MAXIMUM,
    "2": SecurityLevel.MAXIMUM,
}


def get_security_level() -> SecurityLevel:
    raw = os.getenv("JARVIS_SECURITY_LEVEL", "standard").strip().lower()
    return _LEVEL_ALIASES.get(raw, SecurityLevel.STANDARD)


def is_strict_or_above() -> bool:
    return get_security_level() >= SecurityLevel.STRICT


def is_maximum_security() -> bool:
    return get_security_level() >= SecurityLevel.MAXIMUM


# ── 레벨별 기본값 ────────────────────────────────────────────────
_POLICY = {
    SecurityLevel.STANDARD: {
        "tool_rate_max": 30,
        "destructive_rate_max": 8,
        "ssrf_strict": "0",
        "audit_enforce": "warn",
        "sandbox_enforce": "true",
        "keychain_only": "0",
        "net_egress_guard": "0",
        "fds_tools": "0",
        "paths_sandbox_only": "0",
        "voice_auth_required": "0",
        "boot_halt_on_security_warn": "0",
    },
    SecurityLevel.STRICT: {
        "tool_rate_max": 20,
        "destructive_rate_max": 5,
        "ssrf_strict": "1",
        "audit_enforce": "strict",
        "sandbox_enforce": "true",
        "keychain_only": "0",
        "net_egress_guard": "1",
        "fds_tools": "1",
        "paths_sandbox_only": "0",
        "voice_auth_required": "0",
        "boot_halt_on_security_warn": "0",
    },
    SecurityLevel.MAXIMUM: {
        "tool_rate_max": 12,
        "destructive_rate_max": 3,
        "ssrf_strict": "1",
        "audit_enforce": "strict",
        "sandbox_enforce": "true",
        "keychain_only": "1",
        "net_egress_guard": "1",
        "fds_tools": "1",
        "paths_sandbox_only": "1",
        "voice_auth_required": "1",
        "boot_halt_on_security_warn": "1",
    },
}

# 사용자가 명시적으로 설정한 env 는 덮어쓰지 않음 (_APPLIED 플래그로 1회만)
_APPLIED = False

_ENV_MAP = {
    "ssrf_strict": "JARVIS_SSRF_STRICT",
    "audit_enforce": "JARVIS_AUDIT_ENFORCE",
    "sandbox_enforce": "JARVIS_SANDBOX_ENFORCE",
    "keychain_only": "JARVIS_KEYCHAIN_ONLY",
    "net_egress_guard": "JARVIS_NET_EGRESS_GUARD",
    "fds_tools": "JARVIS_FDS_TOOLS",
    "paths_sandbox_only": "JARVIS_PATHS_SANDBOX_ONLY",
    "tool_rate_max": "JARVIS_TOOL_RATE_MAX",
    "destructive_rate_max": "JARVIS_DESTRUCTIVE_RATE_MAX",
    "voice_auth_required": "JARVIS_VOICE_AUTH_REQUIRED",
    "boot_halt_on_security_warn": "JARVIS_BOOT_HALT_ON_SECURITY_WARN",
}


def apply_environment_hardening() -> SecurityLevel:
    """정책 레벨에 맞는 환경변수 기본값을 주입(사용자 명시값은 보존)."""
    global _APPLIED
    try:
        from free_mode import apply_free_mode_defaults
        apply_free_mode_defaults()
    except Exception:
        pass
    level = get_security_level()
    cfg = _POLICY[level]
    for key, env_name in _ENV_MAP.items():
        if not os.getenv(env_name):
            os.environ[env_name] = str(cfg[key])
    _APPLIED = True
    # requests 전역 SSRF 패치 (data_providers 등 net.py 미경유 호출 차단)
    try:
        from security_bootstrap import patch_requests_egress_guard
        patch_requests_egress_guard()
    except Exception:
        pass
    return level


def policy_int(key: str, default: int) -> int:
    env_name = _ENV_MAP.get(key, key)
    try:
        return int(os.getenv(env_name, str(default)))
    except ValueError:
        return default


def policy_flag(key: str) -> bool:
    env_name = _ENV_MAP.get(key, key)
    return os.getenv(env_name, "0").strip().lower() in ("1", "true", "yes", "on")


def paths_sandbox_only() -> bool:
    return policy_flag("paths_sandbox_only")


def fds_tools_enabled() -> bool:
    return policy_flag("fds_tools")


def net_egress_guard_enabled() -> bool:
    return policy_flag("net_egress_guard")


def keychain_only_mode() -> bool:
    return policy_flag("keychain_only")


def voice_auth_required() -> bool:
    return policy_flag("voice_auth_required")


def boot_halt_on_security_warn() -> bool:
    return policy_flag("boot_halt_on_security_warn")


# ── FDS / 고위험 도구 ────────────────────────────────────────────
FDS_MONITORED_TOOLS = frozenset({
    "record_stock_trade",
    "deposit_portfolio_cash",
    "move_files",
    "organize_folder",
    "delete_file",
    "delete_event",
    "remove_face",
    "simulate_supply_chain_scenario",
    "simulate_scenario",
    "analyze_supply_chain_impact",
    "set_price_alert",
    "submit_media_url",
    "cancel_media_job",
    "openclaw_health_check",
    "mirror_whale_portfolio",
})

HIGH_RISK_TOOLS = frozenset(FDS_MONITORED_TOOLS)


def security_summary() -> dict:
    level = get_security_level()
    return {
        "level": level.name,
        "ssrf_strict": policy_flag("ssrf_strict"),
        "audit_enforce": os.getenv("JARVIS_AUDIT_ENFORCE", "warn"),
        "sandbox_enforce": policy_flag("sandbox_enforce"),
        "keychain_only": keychain_only_mode(),
        "net_egress_guard": net_egress_guard_enabled(),
        "fds_tools": fds_tools_enabled(),
        "paths_sandbox_only": paths_sandbox_only(),
        "tool_rate_max": policy_int("tool_rate_max", 30),
        "destructive_rate_max": policy_int("destructive_rate_max", 8),
        "voice_auth_required": voice_auth_required(),
        "boot_halt_on_security_warn": boot_halt_on_security_warn(),
    }


def reconfigure_security() -> None:
    """security 모듈의 속도 제한·차단 경로 등을 정책에 맞게 재설정."""
    try:
        import security as sec
        sec.reconfigure_from_policy()
    except Exception:
        pass
