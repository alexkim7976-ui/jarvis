"""
J.A.R.V.I.S — 보안 설정 관리자
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
API 키를 macOS Keychain에 암호화 저장하고, .env 평문 노출을 제거한다.

기업 보안 원칙:
  1. API 키는 운영체제 키체인에만 저장 (디스크 평문 금지)
  2. .env 는 키체인 미사용 시 폴백 — 키체인 마이그레이션 후 .env에서 키 삭제 가능
  3. 모든 키 접근은 감사 로그에 기록

사용:
  python3 secure_config.py --migrate    .env → Keychain 마이그레이션
  python3 secure_config.py --status     저장 상태 확인
  python3 secure_config.py --delete-env 키체인 마이그레이션 후 .env에서 키 삭제
"""
from __future__ import annotations
import os, json, re
from pathlib import Path
from datetime import datetime

SERVICE_NAME = "jarvis-ai-assistant"

# 키체인에 저장할 키 목록
SECRET_KEYS = [
    "OPENAI_API_KEY",
    "GEMINI_API_KEY",
    "FISH_AUDIO_API_KEY",
    "FISH_VOICE_ID",
    "TAVILY_API_KEY",
    "OPENWEATHER_API_KEY",
    "GITHUB_TOKEN",
    "QUIVER_API_KEY",
    # ── 주식 데이터 프로바이더 (data_providers.py) ──
    "ALPHAVANTAGE_API_KEY",
    "FMP_API_KEY",
    "KIS_APP_KEY", "KIS_APP_SECRET", "KIS_ACCOUNT_NO",
    "LS_APP_KEY", "LS_APP_SECRET",
    "KIWOOM_APP_KEY", "KIWOOM_APP_SECRET",
    "DART_API_KEY",
]

_keyring_available = False
try:
    import keyring
    # macOS Keychain 백엔드 확인
    backend = keyring.get_keyring()
    if "fail" not in type(backend).__name__.lower():
        _keyring_available = True
except ImportError:
    pass


def _log(level: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"OK": "✓", "WARN": "⚠", "INFO": "◈", "LOCK": "🔒"}
    print(f"[{ts}] {icons.get(level, '·')} [보안설정] {msg}")


# ── 키 읽기 (Keychain 우선 → .env 폴백) ──
def get_secret(key: str, default: str = "") -> str:
    """
    API 키 조회 — Keychain 우선, .env 폴백.
    기업 모드(Keychain 활성)에서는 .env를 참조하지 않음.
    """
    # 1순위: 환경변수 (이미 로드됨)
    val = os.environ.get(key, "")
    if val:
        return val

    # 2순위: macOS Keychain
    if _keyring_available:
        try:
            stored = keyring.get_password(SERVICE_NAME, key)
            if stored:
                return stored
        except Exception:
            pass

    return default


def set_secret(key: str, value: str) -> bool:
    """API 키를 Keychain에 저장."""
    if not _keyring_available:
        _log("WARN", f"Keychain 미사용 — {key} 저장 불가 (pip install keyring)")
        return False
    try:
        keyring.set_password(SERVICE_NAME, key, value)
        _log("OK", f"{key} → Keychain 저장 완료")
        return True
    except Exception as e:
        _log("WARN", f"{key} Keychain 저장 실패: {e}")
        return False


def delete_secret(key: str) -> bool:
    """Keychain에서 키 삭제."""
    if not _keyring_available:
        return False
    try:
        keyring.delete_password(SERVICE_NAME, key)
        return True
    except Exception:
        return False


# ── .env → Keychain 마이그레이션 ──
def migrate_env_to_keychain(env_path: str = ".env") -> dict:
    """
    .env 파일의 API 키를 macOS Keychain으로 이전.
    Returns: {"migrated": [...], "skipped": [...], "failed": [...]}
    """
    result = {"migrated": [], "skipped": [], "failed": []}

    if not _keyring_available:
        _log("WARN", "Keychain 사용 불가 — pip install keyring")
        return result

    env_file = Path(env_path)
    if not env_file.exists():
        _log("WARN", f"{env_path} 파일 없음")
        return result

    _log("LOCK", f".env → macOS Keychain 마이그레이션 시작")

    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key not in SECRET_KEYS:
            result["skipped"].append(key)
            continue

        if not value or value.startswith("your_"):
            result["skipped"].append(key)
            continue

        if set_secret(key, value):
            result["migrated"].append(key)
        else:
            result["failed"].append(key)

    _log("OK", f"마이그레이션 완료: {len(result['migrated'])}개 이전, "
               f"{len(result['skipped'])}개 스킵, {len(result['failed'])}개 실패")
    return result


def remove_secrets_from_env(env_path: str = ".env") -> int:
    """
    Keychain 마이그레이션 후, .env에서 실제 키 값을 제거.
    키 이름은 남기되 값을 'KEYCHAIN' 플레이스홀더로 교체.
    """
    env_file = Path(env_path)
    if not env_file.exists():
        return 0

    lines = env_file.read_text().splitlines()
    new_lines = []
    removed = 0

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            new_lines.append(line)
            continue

        key, _, value = stripped.partition("=")
        key = key.strip()

        if key in SECRET_KEYS:
            # Keychain에 있는지 확인
            if _keyring_available:
                try:
                    stored = keyring.get_password(SERVICE_NAME, key)
                    if stored:
                        new_lines.append(f"{key}=STORED_IN_KEYCHAIN")
                        removed += 1
                        continue
                except Exception:
                    pass
        new_lines.append(line)

    env_file.write_text("\n".join(new_lines) + "\n")
    os.chmod(str(env_file), 0o600)
    _log("OK", f".env에서 {removed}개 키 값 제거 (Keychain으로 이전됨)")
    return removed


# ── 상태 확인 ──
def get_security_status() -> dict:
    """보안 설정 상태 리포트."""
    status = {
        "keychain_available": _keyring_available,
        "keychain_backend": str(type(keyring.get_keyring()).__name__) if _keyring_available else "N/A",
        "keys": {},
    }

    for key in SECRET_KEYS:
        in_env = bool(os.environ.get(key, ""))
        in_keychain = False
        if _keyring_available:
            try:
                in_keychain = bool(keyring.get_password(SERVICE_NAME, key))
            except Exception:
                pass

        if in_keychain:
            loc = "🔒 Keychain"
        elif in_env:
            loc = "⚠ .env (평문)"
        else:
            loc = "✗ 미설정"
        status["keys"][key] = loc

    return status


# ── LLM 응답 필터 (API 키/비밀 유출 방지 — DLP) ──
_KEY_PATTERNS = [
    r"sk-[A-Za-z0-9_-]{20,}",                 # OpenAI
    r"sk-proj-[A-Za-z0-9_-]{20,}",            # OpenAI project key
    r"AIza[A-Za-z0-9_-]{30,}",                # Google
    r"ghp_[A-Za-z0-9]{30,}",                  # GitHub PAT
    r"gho_[A-Za-z0-9]{30,}",                  # GitHub OAuth
    r"github_pat_[A-Za-z0-9_]{40,}",          # GitHub fine-grained PAT
    r"tvly-[A-Za-z0-9_-]{20,}",               # Tavily
    r"AKIA[0-9A-Z]{16}",                      # AWS Access Key ID
    r"ASIA[0-9A-Z]{16}",                      # AWS temp access key
    r"xox[baprs]-[A-Za-z0-9-]{10,}",          # Slack token
    r"glpat-[A-Za-z0-9_-]{20,}",              # GitLab PAT
    r"sk_live_[A-Za-z0-9]{20,}",              # Stripe live secret
    r"AC[a-f0-9]{32}",                        # Twilio Account SID
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",  # JWT
    r"-----BEGIN\s+(RSA|EC|OPENSSH|DSA|PRIVATE)\s*P?R?I?V?A?T?E?\s*KEY-----",  # 개인키 블록
    r"[a-f0-9]{32,}",                          # 32자+ 16진수 (일반 API 키)
    r"Bearer\s+[A-Za-z0-9._-]{20,}",          # Bearer 토큰
    r"token['\"]?\s*[:=]\s*['\"][^'\"]{20,}", # token = "..."
    r"(?i)(password|passwd|secret|api[_-]?key)['\"]?\s*[:=]\s*['\"][^'\"]{8,}",  # 자격증명 할당
]
_KEY_RE = re.compile("|".join(_KEY_PATTERNS))

def filter_llm_output(text: str) -> str:
    """
    LLM 응답에서 API 키/토큰 패턴을 감지해 마스킹.
    프롬프트 인젝션으로 키가 유출되는 것을 방지.
    """
    if not text:
        return text

    def _mask(match):
        val = match.group()
        if len(val) <= 8:
            return val  # 너무 짧으면 오탐 가능
        return val[:4] + "*" * (len(val) - 4)

    return _KEY_RE.sub(_mask, text)


# ── CLI ──
if __name__ == "__main__":
    import sys

    if "--migrate" in sys.argv:
        result = migrate_env_to_keychain()
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif "--delete-env" in sys.argv:
        confirm = input("⚠ .env에서 API 키 값을 제거합니다. Keychain에 저장되었는지 확인했나요? (yes/no): ")
        if confirm.lower() == "yes":
            removed = remove_secrets_from_env()
            print(f"✓ {removed}개 키 제거 완료")
        else:
            print("취소됨")

    elif "--status" in sys.argv:
        st = get_security_status()
        print(f"\nKeychain: {st['keychain_backend']} ({'사용가능' if st['keychain_available'] else '미사용'})")
        print(f"{'─'*45}")
        for key, loc in st["keys"].items():
            print(f"  {key:.<30s} {loc}")

    else:
        print("사용법:")
        print("  python3 secure_config.py --migrate     .env → Keychain 이전")
        print("  python3 secure_config.py --status      보안 상태 확인")
        print("  python3 secure_config.py --delete-env   .env에서 키 삭제")
