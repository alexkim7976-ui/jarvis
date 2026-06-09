"""
J.A.R.V.I.S — 보안 모듈
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
경로 트래버설 방지, 입력 새니타이징, 명령 인젝션 차단, 감사 로그.
"""
from __future__ import annotations
import os, re, logging, hashlib, hmac, json, secrets, time, ipaddress, socket, threading
from pathlib import Path
from datetime import datetime
from functools import wraps
from contextlib import contextmanager
from urllib.parse import urlparse

try:
    import fcntl  # POSIX 파일 잠금 (cross-process append 동기화)
    _HAS_FCNTL = True
except ImportError:  # Windows
    _HAS_FCNTL = False

# ── 감사 로그 설정 ──
_LOG_DIR = Path(".logs")
_LOG_DIR.mkdir(exist_ok=True)
os.chmod(str(_LOG_DIR), 0o700)

_audit_logger = logging.getLogger("jarvis.security")
_audit_logger.setLevel(logging.INFO)
_handler = logging.FileHandler(_LOG_DIR / "security_audit.log", encoding="utf-8")
_handler.setFormatter(logging.Formatter(
    "[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
_audit_logger.addHandler(_handler)

# ── 허용 경로 (샌드박스) ──
HOME = Path.home()
ALLOWED_ROOTS = [
    HOME,                                   # 홈 전체 (파일 관리)
    Path("/Applications"),                  # 앱 실행
    Path("/tmp"),                           # 임시 파일
]
# 프로젝트 루트(비밀 파일 위치) — 화이트리스트(HOME) 안이라도 명시 차단
_PROJECT_ROOT = Path(__file__).resolve().parent

# 접근 금지 경로 (화이트리스트 안에 있더라도 차단)
BLOCKED_PATHS = [
    HOME / ".ssh",
    HOME / ".gnupg",
    HOME / ".aws",
    HOME / ".config" / "gcloud",
    HOME / "Library" / "Keychains",
    Path("/etc/shadow"),
    Path("/etc/passwd"),
    Path("/private/etc"),
    # 프로젝트 비밀/자격증명 — 프롬프트 인젝션으로 열람·이동 방지
    _PROJECT_ROOT / ".env",
    _PROJECT_ROOT / ".credentials",
    _PROJECT_ROOT / ".logs",
    _PROJECT_ROOT / "token.json",
    _PROJECT_ROOT / "credentials.json",
    _PROJECT_ROOT / "portfolio.json",
]

# ── 위험 명령 / 인자 패턴 ──
DANGEROUS_PATTERNS = [
    r";\s*rm\s",           # 명령 체이닝 + rm
    r"\|\s*sh\b",          # 파이프 → 쉘
    r"\$\(",               # 명령 치환
    r"`[^`]+`",            # 백틱 명령 치환
    r"&&\s*(rm|curl|wget)", # 체이닝 + 위험 명령
    r"\.\./\.\./",         # 깊은 경로 트래버설
    r"~root",
    r"/etc/(shadow|passwd|sudoers)",
]
_DANGEROUS_RE = re.compile("|".join(DANGEROUS_PATTERNS), re.IGNORECASE)

# ── 경로 검증 ──
def validate_path(path_str: str, must_exist: bool = False) -> tuple[bool, str, Path | None]:
    """
    경로가 허용 범위 내인지 검증.
    Returns: (안전여부, 사유, 정규화된 Path 또는 None)
    """
    if not path_str or not isinstance(path_str, str):
        return False, "빈 경로", None

    try:
        p = Path(path_str).expanduser().resolve()
    except (ValueError, OSError) as e:
        _audit_logger.warning(f"PATH_INVALID path={path_str!r} err={e}")
        return False, f"잘못된 경로: {e}", None

    # 심볼릭 링크 해제 후 실제 경로 확인
    real = p.resolve()

    # 차단 경로 확인 (우선)
    for blocked in BLOCKED_PATHS:
        try:
            real.relative_to(blocked)
            _audit_logger.warning(f"PATH_BLOCKED path={path_str!r} resolved={real} blocked_by={blocked}")
            return False, f"보안 경로 접근 차단: {blocked.name}", None
        except ValueError:
            continue

    # 허용 루트 확인
    allowed = False
    for root in ALLOWED_ROOTS:
        try:
            real.relative_to(root)
            allowed = True
            break
        except ValueError:
            continue

    if not allowed:
        _audit_logger.warning(f"PATH_OUTSIDE path={path_str!r} resolved={real}")
        return False, "허용 범위 밖의 경로", None

    if must_exist and not real.exists():
        return False, f"존재하지 않는 경로: {p}", None

    return True, "OK", real


def sanitize_filename(name: str) -> str:
    """파일명에서 위험 문자 제거."""
    # null byte, 경로 구분자, 쉘 메타문자 제거
    cleaned = re.sub(r'[\x00/\\;|&$`\'"<>{}()]', '', name)
    # 경로 트래버설 제거
    cleaned = cleaned.replace('..', '')
    return cleaned.strip() or "unnamed"


# ── 경로 인자 키 (파일 관련 도구 — 입력/출력 가드레일 공용) ──
_PATH_ARG_KEYS = {"file_path", "folder_path", "source_folder", "destination",
                  "target_folder", "path", "folder_name",
                  "dest_folder", "destination_folder", "dst_folder"}


# ── 입력 새니타이징 ──
def sanitize_tool_args(tool_name: str, args: dict) -> tuple[bool, str, dict]:
    """
    도구 인자를 검증/새니타이즈.
    Returns: (안전여부, 사유, 정제된 인자)
    """
    if not isinstance(args, dict):
        return False, "인자가 dict가 아님", {}

    cleaned = dict(args)

    # 모든 문자열 인자에서 위험 패턴 검사
    for k, v in cleaned.items():
        if isinstance(v, str):
            if _DANGEROUS_RE.search(v):
                _audit_logger.warning(
                    f"INJECTION_BLOCKED tool={tool_name} key={k} value={v!r}")
                return False, f"위험한 입력 감지 (인자: {k})", {}

    # 경로 인자 검증 (파일 관련 도구)
    path_keys = _PATH_ARG_KEYS
    for k in path_keys:
        if k in cleaned and isinstance(cleaned[k], str):
            safe, reason, resolved = validate_path(cleaned[k])
            if not safe:
                _audit_logger.warning(
                    f"PATH_REJECTED tool={tool_name} key={k} path={cleaned[k]!r} reason={reason}")
                return False, f"경로 보안 위반 ({k}): {reason}", {}

    # URL 인자 검증 (SSRF 차단) — 도구가 외부로 보내는 모든 URL을 게이트에서 검사
    url_keys = {"url", "stream_url", "source_url", "image_url", "link", "webhook_url"}
    for k in url_keys:
        if k in cleaned and isinstance(cleaned[k], str) and cleaned[k].strip():
            safe, reason = validate_url(cleaned[k])
            if not safe:
                _audit_logger.warning(
                    f"URL_REJECTED tool={tool_name} key={k} url={cleaned[k][:80]!r} reason={reason}")
                return False, f"URL 보안 위반 ({k}): {reason}", {}

    return True, "OK", cleaned


# ── 도구 호출 감사 로그 ──
def audit_tool_call(tool_name: str, args: dict, result: dict | None = None):
    """도구 호출을 감사 로그에 기록."""
    args_summary = {k: (str(v)[:80] + "..." if len(str(v)) > 80 else v)
                    for k, v in args.items()} if args else {}
    success = result.get("success", "?") if result else "?"
    _audit_logger.info(f"TOOL_CALL tool={tool_name} args={args_summary} success={success}")


# ── 속도 제한 ──
class RateLimiter:
    """간단한 슬라이딩 윈도우 속도 제한."""
    def __init__(self, max_calls: int = 30, window_sec: int = 60):
        self.max_calls = max_calls
        self.window = window_sec
        self._calls: list[float] = []

    def allow(self) -> bool:
        now = time.time()
        self._calls = [t for t in self._calls if now - t < self.window]
        if len(self._calls) >= self.max_calls:
            _audit_logger.warning(f"RATE_LIMIT_HIT {len(self._calls)} calls in {self.window}s")
            return False
        self._calls.append(now)
        return True


# 도구 호출 글로벌 속도 제한 (분당 30회)
tool_rate_limiter = RateLimiter(max_calls=30, window_sec=60)


# ── 환경 보안 검증 ──
def check_env_security() -> list[str]:
    """부팅 시 보안 상태 점검 — 경고 목록 반환."""
    warnings = []
    project = Path(__file__).parent

    # .gitignore 존재 확인
    if not (project / ".gitignore").exists():
        warnings.append(".gitignore 없음 — API 키 Git 유출 위험")

    # .env 권한
    env_file = project / ".env"
    if env_file.exists():
        mode = oct(env_file.stat().st_mode)[-3:]
        if mode != "600":
            warnings.append(f".env 권한 {mode} → 600 권장 (chmod 600 .env)")

    # credentials 권한
    cred_dir = project / ".credentials"
    if cred_dir.exists():
        dir_mode = oct(cred_dir.stat().st_mode)[-3:]
        if dir_mode != "700":
            warnings.append(f".credentials/ 권한 {dir_mode} → 700 권장")
        for f in cred_dir.glob("*.json"):
            fm = oct(f.stat().st_mode)[-3:]
            if fm != "600":
                warnings.append(f"{f.name} 권한 {fm} → 600 권장")

    # Git 추적 상태
    gitdir = project / ".git"
    if gitdir.exists():
        import subprocess
        try:
            r = subprocess.run(
                ["git", "ls-files", "--cached", ".env", "credentials.json",
                 "token.json", ".credentials/"],
                capture_output=True, text=True, cwd=str(project), timeout=5)
            tracked = r.stdout.strip()
            if tracked:
                warnings.append(f"Git이 민감 파일을 추적 중: {tracked} → git rm --cached 필요")
        except Exception:
            pass

    return warnings


# ── API 키 마스킹 (로그 출력용) ──
def mask_key(key: str, visible: int = 4) -> str:
    """API 키를 마스킹 (앞 4자만 표시)."""
    if not key or len(key) <= visible:
        return "***"
    return key[:visible] + "*" * (len(key) - visible)


# ════════════════════════════════════════════════════════════════════
#  레이어 6: 프롬프트 인젝션 / 탈옥(Jailbreak) 방어  (OWASP LLM01)
# ════════════════════════════════════════════════════════════════════
# JARVIS는 전체 파일시스템 접근·앱 실행·파일 이동 권한을 가진 LLM 에이전트다.
# 따라서 ① 사용자 음성 입력, ② 웹/뉴스 검색으로 되돌아오는 외부 콘텐츠
# (간접 프롬프트 인젝션)를 모두 스캔해 시스템 프롬프트 무력화·도구 오남용·
# 비밀 추출 시도를 탐지한다.
_INJECTION_PATTERNS = [
    # 시스템 프롬프트 무력화 (영어)
    r"ignore\s+(all\s+|the\s+|any\s+)?(previous|above|prior|earlier)\s+(instruction|prompt|message|rule)",
    r"disregard\s+(your|the|all)\s+(system\s+)?(prompt|instruction|rule|guideline)",
    r"forget\s+(everything|all|your\s+instructions|previous)",
    r"override\s+(your\s+|the\s+)?(safety|security|guardrail|restriction|filter)",
    r"you\s+are\s+now\s+(a\s+|an\s+)?(dan|jailbroken|developer\s+mode|unrestricted)",
    r"(enable|activate)\s+(dan|developer\s+mode|jailbreak)",
    r"pretend\s+(you\s+are|to\s+be)\s+.{0,30}(no\s+restriction|unfiltered|evil)",
    r"act\s+as\s+.{0,30}(no\s+restriction|without\s+(rules|filter|safety))",
    # 비밀/시스템 프롬프트 추출
    r"(reveal|print|show|repeat|tell\s+me|output|dump|leak)\s+(me\s+)?(your|the|all)\s+"
    r"(system\s+prompt|initial\s+prompt|instruction|api\s*key|secret|token|password|credential)",
    r"what\s+(is|are)\s+your\s+(system\s+prompt|instruction|api\s*key|secret)",
    r"repeat\s+(the\s+words\s+above|everything\s+above|your\s+prompt)",
    # 위험 명령 유도 (자연어)
    r"delete\s+(all|every|my)\s+(file|data|folder)",
    r"(rm\s+-rf|format\s+(the\s+)?(disk|drive)|wipe\s+(the\s+)?(disk|system))",
    r"(send|upload|exfiltrate|email)\s+.{0,40}(api\s*key|secret|password|\.env|credential)",
    # 한국어
    r"(이전|위|앞)(의|에)?\s*(모든\s*)?(지시|명령|프롬프트|규칙)(사항)?\s*(을|를)?\s*(무시|잊어|삭제|취소)",
    r"시스템\s*프롬프트.{0,20}(알려|보여|출력|말해|공개)",
    r"(api\s*키|비밀번호|토큰|시크릿|자격\s*증명).{0,20}(알려|보여|출력|공개|전송|보내)",
    r"(모든|전체)\s*(파일|데이터|폴더).{0,10}(삭제|지워|제거)",
    r"(보안|안전|제한|가드레일|필터)\s*(을|를)?\s*(해제|무시|우회|꺼)",
    r"개발자\s*모드|탈옥|제한\s*없이",
]
_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.IGNORECASE)


def detect_prompt_injection(text: str, source: str = "user") -> tuple[bool, str]:
    """
    프롬프트 인젝션/탈옥 시도를 탐지.
    source: "user"(직접 음성) 또는 "web"/"news"(간접 인젝션) 등.
    Returns: (탐지여부, 매칭된 사유)
    """
    if not text or not isinstance(text, str):
        return False, ""
    m = _INJECTION_RE.search(text)
    if m:
        snippet = m.group()[:60]
        _audit_logger.warning(
            f"PROMPT_INJECTION source={source} pattern={snippet!r} "
            f"text={text[:120]!r}")
        return True, snippet
    return False, ""


def sanitize_external_content(text: str, source: str = "web") -> str:
    """
    웹/뉴스 등 외부에서 LLM 컨텍스트로 되돌아오는 콘텐츠에서
    간접 프롬프트 인젝션 문구를 무력화(중화)한다. 콘텐츠는 보존하되
    명령형 인젝션 토큰만 [차단됨]으로 치환해 LLM이 지시로 오인하지 않게 한다.
    """
    if not text or not isinstance(text, str):
        return text
    if _INJECTION_RE.search(text):
        _audit_logger.warning(f"INDIRECT_INJECTION_NEUTRALIZED source={source}")
        return _INJECTION_RE.sub("[보안필터: 차단된 지시문]", text)
    return text


def validate_command_input(text: str, source: str = "user") -> tuple[bool, str, str]:
    """음성 STT 결과든 사용자가 직접 타이핑한 명령이든, LLM/백엔드로 들어가기 전에
    공통으로 거치는 입력 게이트.
      · 명령어 주입(쉘 메타문자·체이닝)·경로 조작(../../) → 하드 차단
      · 프롬프트 인젝션/탈옥 문구 → 콘텐츠는 보존하되 지시문만 중화(neutralize)
    Returns: (안전여부, 사유, 정제된 텍스트)
    """
    if not text or not isinstance(text, str):
        return False, "빈 입력", ""
    stripped = text.strip()
    if not stripped:
        return False, "빈 입력", ""

    # 1) 명령어 주입 / 경로 조작 — 하드 차단
    if _DANGEROUS_RE.search(stripped):
        _audit_logger.warning(
            f"CMD_INPUT_BLOCKED source={source} text={stripped[:120]!r}")
        return False, "명령어 주입/경로 조작 패턴 감지", ""

    # 2) 프롬프트 인젝션 — 지시문만 중화하고 콘텐츠는 보존해 통과
    cleaned = sanitize_external_content(stripped, source=source)
    return True, "OK", cleaned


# ════════════════════════════════════════════════════════════════════
#  레이어 9: 양방향 가드레일 (Inbound / Outbound Guardrails)
# ════════════════════════════════════════════════════════════════════
# 탈옥(Jailbreak)/가스라이팅 특유의 강제 어조·역할극 변조·제한 해제 패턴.
# _INJECTION_PATTERNS(detect_prompt_injection)를 보완하는 추가 규칙.
_JAILBREAK_PATTERNS = [
    # 영어 탈옥/역할극
    r"ignore\s+(all\s+|the\s+|any\s+)?(previous|prior|above|earlier)",
    r"developer\s*mode", r"do\s+anything\s+now", r"\bDAN\b", r"jail\s*break",
    r"pretend\s+(you\s+are|to\s+be)", r"act\s+as\s+(if\s+)?(an?\s+)?(unrestricted|unfiltered)",
    r"system\s*prompt", r"reveal\s+your\s+(instruction|prompt|rule)",
    r"without\s+(any\s+)?(restriction|filter|rule|limitation)",
    # 한국어 탈옥/가스라이팅
    r"이전\s*(의)?\s*(지침|지시|명령|규칙|프롬프트)\s*(은|는|을|를)?\s*(무시|잊)",
    r"(제한|제약|안전장치|안전\s*모드|필터|가드레일)\s*(을|를|은|는)?\s*(해제|풀어|꺼|끄|우회|무력화)",
    r"개발자\s*모드", r"탈옥", r"제한\s*없(이|는)\s*(대답|응답|행동)",
    r"시스템\s*프롬프트", r"규칙\s*(을|를)?\s*무시", r"가이드라인\s*(을|를)?\s*무시",
    r"너(는|의|에게)?\s*(이제|지금부터)?\s*.{0,20}(제약|규칙|제한)\s*(이|은|는)?\s*없",
    r"역할극.{0,20}(제약|규칙|무시|해제)",
]
_JAILBREAK_RE = re.compile("|".join(_JAILBREAK_PATTERNS), re.IGNORECASE)


def validate_llm_input(user_input: str) -> tuple[bool, str]:
    """[Inbound] 사용자 입력(음성 STT 포함)을 LLM에 넘기기 전 검사.
    프롬프트 인젝션·탈옥·가스라이팅·명령어 주입을 탐지하면 (False, 사유)를 반환하고
    변조 방지 감사 체인(chain_event)에 기록한다.
    Returns: (안전여부, 사유)
    """
    if not user_input or not isinstance(user_input, str):
        return False, "빈 입력"
    text = user_input.strip()
    if not text:
        return False, "빈 입력"

    # 1) 기존 프롬프트 인젝션 탐지기
    is_inj, why = detect_prompt_injection(text, source="user")
    if is_inj:
        chain_event("LLM_INPUT_BLOCK", {"kind": "prompt_injection", "pattern": why})
        return False, f"프롬프트 인젝션 탐지: {why}"

    # 2) 탈옥/가스라이팅 패턴
    m = _JAILBREAK_RE.search(text)
    if m:
        snip = m.group()[:50]
        _audit_logger.warning(f"LLM_INPUT_JAILBREAK pattern={snip!r} text={text[:120]!r}")
        chain_event("LLM_INPUT_BLOCK", {"kind": "jailbreak", "pattern": snip})
        return False, f"탈옥/가스라이팅 시도 탐지: {snip}"

    # 3) 명령어 주입 / 경로 조작
    if _DANGEROUS_RE.search(text):
        _audit_logger.warning(f"LLM_INPUT_CMD_INJECTION text={text[:120]!r}")
        chain_event("LLM_INPUT_BLOCK", {"kind": "cmd_injection"})
        return False, "명령어 주입/경로 조작 패턴 탐지"

    return True, "OK"


# ── 출력 가드레일용 도구 스키마 레지스트리 (ALL_TOOLS 화이트리스트) ──
_TOOL_REGISTRY: dict[str, dict] = {}


def register_tools(tools: list) -> None:
    """ALL_TOOLS 스키마를 등록해 출력 가드레일의 화이트리스트/타입 검증 근거로 사용."""
    for t in tools or []:
        func = t.get("function", {}) if isinstance(t, dict) else {}
        name = func.get("name")
        if not name:
            continue
        params = func.get("parameters", {}) or {}
        props = params.get("properties", {}) or {}
        _TOOL_REGISTRY[name] = {
            "types": {k: (v.get("type") or "string") for k, v in props.items()},
            "required": list(params.get("required", []) or []),
        }
    _audit_logger.info(f"TOOL_REGISTRY 등록: {len(_TOOL_REGISTRY)}개 도구")


def _arg_type_ok(expected: str, value) -> bool:
    """설계 스키마 타입과 실제 인자 값의 호환성(느슨) 검사 — LLM이 number를 문자열로 줄 수 있음."""
    e = (expected or "string").lower()
    if e in ("number", "integer"):
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            s = value.strip().lstrip("+-")
            return s.replace(".", "", 1).isdigit()
        return False
    if e == "boolean":
        return isinstance(value, bool) or (isinstance(value, str) and value.strip().lower() in ("true", "false"))
    if e == "array":
        return isinstance(value, (list, tuple, str))   # JSON 문자열 허용
    if e == "object":
        return isinstance(value, (dict, str))
    return isinstance(value, (str, int, float, bool))   # string: 관대


# 포트폴리오 강제 전량매도/초기화 등 가스라이팅 유발 인자 패턴
_PORTFOLIO_ABUSE_RE = re.compile(
    r"(전량|전부|모두|모든|전체|청산|초기화|reset|liquidat|wipe|clear\s*all|sell\s*all|all\s*in)",
    re.IGNORECASE)
_PORTFOLIO_NUMERIC_KEYS = ("shares", "quantity", "qty", "amount", "amount_krw",
                           "cash", "cash_krw", "price", "krw")


def validate_llm_output(tool_name: str, tool_args: dict) -> tuple[bool, str]:
    """[Outbound] LLM이 선택한 도구 호출이 실행 레이어로 가기 직전 검증.
    화이트리스트(ALL_TOOLS) 이탈, 인자 타입 위반, 차단 경로(BLOCKED_PATHS) 침범,
    포트폴리오 강제 청산/초기화 같은 권한 초과·가스라이팅성 호출을 차단하고 감사 체인에 기록.
    Returns: (허용여부, 사유)
    """
    if not tool_name or not isinstance(tool_name, str):
        return False, "도구명 누락/형식 오류"
    if not isinstance(tool_args, dict):
        chain_event("OUTBOUND_BLOCK", {"tool": str(tool_name), "kind": "bad_args_type"})
        return False, "도구 인자 형식 오류"

    # 1) 화이트리스트 — 등록된 ALL_TOOLS 범위를 벗어난 도구 차단
    if _TOOL_REGISTRY and tool_name not in _TOOL_REGISTRY:
        _audit_logger.warning(f"OUTBOUND_UNKNOWN_TOOL tool={tool_name}")
        chain_event("OUTBOUND_BLOCK", {"tool": tool_name, "kind": "unknown_tool"})
        return False, f"허용되지 않은 도구 호출: {tool_name}"

    spec = _TOOL_REGISTRY.get(tool_name, {})
    types = spec.get("types", {})

    # 2) 인자 타입 검증 (설계된 스키마 범위)
    for k, v in tool_args.items():
        if k in types and not _arg_type_ok(types[k], v):
            _audit_logger.warning(
                f"OUTBOUND_ARG_TYPE tool={tool_name} key={k} expected={types[k]} got={type(v).__name__}")
            chain_event("OUTBOUND_BLOCK",
                        {"tool": tool_name, "kind": "arg_type", "key": k, "expected": types[k]})
            return False, f"인자 타입 위반 ({k}: {types[k]} 기대)"

    # 3) 경로 인자 — 시스템 디렉토리/차단 경로(BLOCKED_PATHS) 침범 차단
    for k, v in tool_args.items():
        if isinstance(v, str) and v.strip() and k in _PATH_ARG_KEYS:
            safe, reason, _ = validate_path(v)
            if not safe:
                _audit_logger.warning(f"OUTBOUND_PATH tool={tool_name} key={k} path={v!r} reason={reason}")
                chain_event("OUTBOUND_BLOCK",
                            {"tool": tool_name, "kind": "path", "key": k, "reason": reason})
                return False, f"경로 권한 초과 ({k}): {reason}"

    # 4) 포트폴리오 — 강제 전량매도/초기화·비정상 수치 차단 (가스라이팅 방어)
    if "portfolio" in tool_name or tool_name.startswith(("record_stock", "deposit_portfolio")):
        for k, v in tool_args.items():
            if isinstance(v, str) and _PORTFOLIO_ABUSE_RE.search(v):
                _audit_logger.warning(f"OUTBOUND_PORTFOLIO_ABUSE tool={tool_name} key={k} value={v[:60]!r}")
                chain_event("OUTBOUND_BLOCK",
                            {"tool": tool_name, "kind": "portfolio_abuse", "key": k})
                return False, "비정상 포트폴리오 조작(전량/초기화) 인자 차단"
        for k in _PORTFOLIO_NUMERIC_KEYS:
            if k in tool_args:
                try:
                    val = float(tool_args[k])
                except (TypeError, ValueError):
                    continue
                if val < 0 or val > 1e12:
                    _audit_logger.warning(f"OUTBOUND_PORTFOLIO_RANGE tool={tool_name} key={k} value={tool_args[k]!r}")
                    chain_event("OUTBOUND_BLOCK",
                                {"tool": tool_name, "kind": "portfolio_range", "key": k})
                    return False, f"포트폴리오 인자 범위 초과 ({k})"

    return True, "OK"


# ════════════════════════════════════════════════════════════════════
#  레이어 7: SSRF / 이그레스(Egress) URL 가드  (OWASP API)
# ════════════════════════════════════════════════════════════════════
# ir_interpreter 등 LLM이 제어하는 URL을 외부 프로세스(streamlink/ffmpeg)에
# 넘기기 전, 내부망·루프백·클라우드 메타데이터 엔드포인트로의 요청을 차단한다.
_ALLOWED_URL_SCHEMES = {"http", "https", "rtmp", "rtmps"}
# 클라우드 메타데이터/내부 서비스 (SSRF 단골 표적)
_BLOCKED_HOST_LITERALS = {
    "169.254.169.254",          # AWS/GCP/Azure IMDS
    "metadata.google.internal",
    "metadata",
    "localhost",
}


def _ip_is_dangerous(ip: ipaddress._BaseAddress) -> bool:
    """루프백·사설·링크로컬·예약·멀티캐스트·미지정 대역인지."""
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_reserved or ip.is_multicast or ip.is_unspecified
    )


def _coerce_ip(host: str):
    """다양한 표기의 IP를 ipaddress 객체로 변환.

    점표기/IPv6뿐 아니라 정수(2130706433)·16진(0x7f000001)·8진(017700000001)
    표기까지 처리해 127.0.0.1 우회를 차단한다. 변환 불가 시 None(=도메인).
    """
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    h = host.strip()
    try:
        if h.lower().startswith("0x"):
            val = int(h, 16)
        elif h.startswith("0") and h != "0" and all(c in "01234567" for c in h):
            val = int(h, 8)
        elif h.isdigit():
            val = int(h, 10)
        else:
            return None
        if 0 <= val <= 0xFFFFFFFF:
            return ipaddress.ip_address(val)
    except ValueError:
        return None
    return None


def _resolve_host_ips(host: str, port: int | None):
    """호스트명을 실제 IP로 DNS 해석.

    실패 시 기본 동작은 빈 리스트 반환(연결 자체가 실패하므로 허용 — 가용성 우선).
    단 JARVIS_SSRF_STRICT=1 이면 None 을 반환해 호출부에서 차단(fail-closed):
    일시적 DNS 장애가 정상 외부 호출을 막을 수 있는 트레이드오프를 감수하고
    SSRF 표면을 최대로 줄이고 싶을 때 사용한다."""
    if os.getenv("JARVIS_SSRF_DNS_CHECK", "1").strip().lower() in ("0", "false", "no", "off"):
        return []
    strict = os.getenv("JARVIS_SSRF_STRICT", "0").strip().lower() in ("1", "true", "yes", "on")
    prev = socket.getdefaulttimeout()
    try:
        socket.setdefaulttimeout(3.0)
        infos = socket.getaddrinfo(host, port or 80, proto=socket.IPPROTO_TCP)
    except Exception:
        return None if strict else []
    finally:
        socket.setdefaulttimeout(prev)
    out = []
    for info in infos:
        try:
            out.append(ipaddress.ip_address(info[4][0]))
        except (ValueError, IndexError):
            continue
    return out


def validate_url(url: str) -> tuple[bool, str]:
    """
    외부 호출용 URL을 검증 — SSRF/이그레스 차단.
    문자열 검사뿐 아니라 실제 DNS 해석 결과 IP까지 사설/루프백 대역인지 확인한다.
    Returns: (안전여부, 사유)
    """
    if not url or not isinstance(url, str):
        return False, "빈 URL"
    try:
        parsed = urlparse(url.strip())
    except Exception as e:
        return False, f"URL 파싱 실패: {e}"

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_URL_SCHEMES:
        _audit_logger.warning(f"URL_SCHEME_BLOCKED url={url[:80]!r} scheme={scheme}")
        return False, f"허용되지 않은 스킴: {scheme or '(없음)'}"

    # 후행 점(FQDN 절대표기) 정규화 — 'localhost.'/'169.254.169.254.' 같은
    # 변형으로 리터럴 차단·IP 강제 검사를 우회하는 SSRF를 막는다(RFC 1035).
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        return False, "호스트 없음"

    if host in _BLOCKED_HOST_LITERALS:
        _audit_logger.warning(f"URL_HOST_BLOCKED url={url[:80]!r} host={host}")
        return False, f"내부/메타데이터 호스트 차단: {host}"

    # 1) 호스트가 IP(점표기/정수/16진/8진)인 경우 — 즉시 대역 검사
    literal_ip = _coerce_ip(host)
    if literal_ip is not None:
        if _ip_is_dangerous(literal_ip):
            _audit_logger.warning(f"URL_PRIVATE_IP_BLOCKED url={url[:80]!r} ip={literal_ip}")
            return False, f"사설/예약 IP 차단: {literal_ip}"
        return True, "OK"

    # 2) 도메인 — 명백한 내부 도메인 차단
    if host.endswith(".local") or host.endswith(".internal") or host == "0.0.0.0":
        _audit_logger.warning(f"URL_INTERNAL_DOMAIN_BLOCKED url={url[:80]!r} host={host}")
        return False, f"내부 도메인 차단: {host}"

    # 3) 도메인 — 실제 DNS 해석 후 모든 결과 IP가 안전 대역인지 검증 (DNS 리바인딩 차단)
    resolved = _resolve_host_ips(host, parsed.port)
    if resolved is None:
        # 엄격 모드(JARVIS_SSRF_STRICT=1): DNS 해석 실패 → fail-closed 차단
        _audit_logger.warning(f"URL_DNS_FAIL_STRICT_BLOCKED url={url[:80]!r} host={host}")
        return False, f"DNS 해석 실패 — 엄격 모드 차단: {host}"
    for ip in resolved:
        if _ip_is_dangerous(ip):
            _audit_logger.warning(
                f"URL_DNS_REBIND_BLOCKED url={url[:80]!r} host={host} resolved={ip}")
            return False, f"내부 IP로 해석되는 호스트 차단: {host} → {ip}"

    return True, "OK"


# ════════════════════════════════════════════════════════════════════
#  레이어 8: 파괴적(비가역) 도구 티어 분리 + 전용 속도 제한
# ════════════════════════════════════════════════════════════════════
# 파일을 비가역적으로 이동/정리하는 고위험 도구는 일반 도구보다 엄격한
# 속도 제한과 별도 감사를 적용한다. (최소 권한 / 권한 분리 원칙)
DESTRUCTIVE_TOOLS = {
    "move_files",
    "organize_folder",   # dry_run=False 일 때만 실제 파괴적
    "delete_file",       # 샌드박스 내부 한정이지만 비가역 → 전용 가드 적용
    "delete_event",
    "remove_face",
}

# 파괴적 작업 전용 속도 제한 (분당 8회) — 폭주성 파일 이동/삭제 방지
destructive_rate_limiter = RateLimiter(max_calls=8, window_sec=60)


def guard_destructive_tool(tool_name: str, args: dict) -> tuple[bool, str]:
    """
    파괴적 도구 호출에 대한 추가 가드.
    - organize_folder 는 dry_run=False(실제 이동)일 때만 파괴적으로 간주
    - 파괴적이면 전용 속도 제한 + 감사 로그 적용
    Returns: (허용여부, 사유)
    """
    if tool_name not in DESTRUCTIVE_TOOLS:
        return True, "OK"

    # organize_folder 는 미리보기(dry_run)면 안전
    if tool_name == "organize_folder" and args.get("dry_run", True) is not False:
        return True, "OK"

    if not destructive_rate_limiter.allow():
        _audit_logger.warning(f"DESTRUCTIVE_RATE_LIMIT tool={tool_name}")
        return False, "파괴적 작업 속도 제한 초과 — 잠시 후 다시 시도하세요."

    _audit_logger.info(
        f"DESTRUCTIVE_OP tool={tool_name} "
        f"args={ {k: str(v)[:60] for k, v in (args or {}).items()} }")
    return True, "OK"


# ════════════════════════════════════════════════════════════════════
#  레이어 9: 실시간 감사 로그 해시 체인 (Audit Log Hash Chain)
# ════════════════════════════════════════════════════════════════════
# 평문 로그는 공격자가 흔적을 지울 수 있다. 각 레코드에 직전 레코드의
# current_hash 를 prev_hash 로 포함(블록체인식 해시 체인)하고, [현재 내용 +
# prev_hash]의 SHA-256 을 current_hash 로 기록해 행 삭제·수정·재배열을 사후에
# 탐지한다. (SIEM / 무결성 로깅 패턴)
#
# 로그 파일: jarvis_audit.log (JSON Lines)
# 최초 블록(Genesis)의 prev_hash 로 쓰는 고정 초기 벡터:
GENESIS_HASH = "0" * 64
_AUDIT_LOG_PATH = _LOG_DIR / "jarvis_audit.log"
# 하위 호환 별칭 (기존 코드가 참조)
_CHAIN_PATH = _AUDIT_LOG_PATH
_GENESIS = GENESIS_HASH
_CHAIN_KEY_PATH = _LOG_DIR / ".chain_key"


def _load_chain_key() -> bytes:
    """HMAC 서명 키 로드 — Keychain 우선, 없으면 600 권한 로컬 키 생성."""
    # 1순위: Keychain (secure_config 경유)
    try:
        from secure_config import get_secret, set_secret  # 지연 임포트(순환 방지)
        existing = get_secret("JARVIS_AUDIT_HMAC_KEY")
        if existing:
            return existing.encode("utf-8")
        new_key = secrets.token_hex(32)
        if set_secret("JARVIS_AUDIT_HMAC_KEY", new_key):
            return new_key.encode("utf-8")
    except Exception:
        pass

    # 2순위: 로컬 키 파일 (0600)
    if _CHAIN_KEY_PATH.exists():
        return _CHAIN_KEY_PATH.read_bytes()
    key = secrets.token_bytes(32)
    _CHAIN_KEY_PATH.write_bytes(key)
    try:
        os.chmod(str(_CHAIN_KEY_PATH), 0o600)
    except OSError:
        pass
    return key


_CHAIN_KEY = _load_chain_key()


# ── 동시성 동기화 ──
# 멀티스레딩/비동기 환경에서 로그 라인이 겹쳐 깨지지 않도록 파일 입출력을 직렬화
_audit_lock = threading.Lock()            # 프로세스 내(멀티스레드) 직렬화
_chain_lock = _audit_lock                 # 하위 호환 별칭
_CHAIN_LOCKFILE = _LOG_DIR / ".chain.lock"  # 프로세스 간(fcntl) 직렬화


@contextmanager
def _file_lock():
    """append 임계구역을 cross-process 로 직렬화 (POSIX flock). 미지원 시 무동작."""
    if not _HAS_FCNTL:
        yield
        return
    f = None
    try:
        f = open(_CHAIN_LOCKFILE, "w")
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if f is not None:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            f.close()


# ── 민감정보 마스킹 (로그 평문 노출 방지) ──
# 키처럼 보이는 토큰(폴백용) — secure_config.filter_llm_output 실패 시 사용
_SECRET_LIKE_RE = re.compile(
    r"(sk-[A-Za-z0-9]{12,}"
    r"|AIza[A-Za-z0-9_\-]{16,}"
    r"|gh[pousr]_[A-Za-z0-9]{16,}"
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"
    r"|eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"  # JWT
    r"|\b[A-Fa-f0-9]{32,}\b)"                                              # 긴 hex 토큰
)
_USER_PATH_RES = [
    (re.compile(r"/Users/[^/\s:'\"]+"), "/Users/***"),
    (re.compile(r"/home/[^/\s:'\"]+"), "/home/***"),
    (re.compile(r"[A-Za-z]:\\Users\\[^\\\s:'\"]+"), r"C:\\Users\\***"),
]


# 민감 키워드 = 값 형태 (api_key: xxx, SECRET=yyy, token => zzz ...) → 값 마스킹
_SENSITIVE_KV_RE = re.compile(
    r"(?i)\b(api[_-]?key|secret|secret[_-]?key|token|access[_-]?token|refresh[_-]?token"
    r"|password|passwd|pwd|authorization|bearer|private[_-]?key|client[_-]?secret)\b"
    r"\s*[:=>]+\s*['\"]?[^\s'\"]+"
)
_MASK = "[MASKED]"


def _mask_text(s: str) -> str:
    """문자열에서 사용자 절대 경로(Path.home())와 API_KEY/SECRET 등 민감 정보를 [MASKED] 처리."""
    if not s or not isinstance(s, str):
        return s
    # 1) 민감 키워드=값 → 키워드는 보존하고 값만 [MASKED]
    s = _SENSITIVE_KV_RE.sub(lambda m: re.split(r"[:=>]+", m.group(), 1)[0].rstrip() + "=" + _MASK, s)
    # 2) 키처럼 보이는 토큰 자체 → [MASKED]
    s = _SECRET_LIKE_RE.sub(_MASK, s)
    # 3) 사용자 홈 절대 경로(Path.home()) → [MASKED]
    try:
        home = str(HOME)
        if home and home in s:
            s = s.replace(home, _MASK)
    except Exception:
        pass
    # 4) 그 외 사용자 절대 경로 → [MASKED]
    for rx, _repl in _USER_PATH_RES:
        s = rx.sub(_MASK, s)
    return s


def _mask_sensitive(obj):
    """detail(dict/list/str) 전체를 재귀적으로 마스킹."""
    if isinstance(obj, dict):
        return {k: _mask_sensitive(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_mask_sensitive(v) for v in obj]
    if isinstance(obj, str):
        return _mask_text(obj)
    return obj


def _last_chain_hash() -> str:
    """체인의 마지막 레코드 해시 반환 (없으면 제네시스). 신/구 필드 모두 인식."""
    if not _CHAIN_PATH.exists():
        return _GENESIS
    last = None
    try:
        with open(_CHAIN_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    last = line
    except OSError:
        return _GENESIS
    if not last:
        return _GENESIS
    try:
        rec = json.loads(last)
        return rec.get("current_hash") or rec.get("hash") or _GENESIS
    except json.JSONDecodeError:
        return _GENESIS


def _count_lines() -> int:
    if not _CHAIN_PATH.exists():
        return 0
    try:
        with open(_CHAIN_PATH, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _record_hash(prev_hash: str, payload_json: str) -> str:
    """SHA-256([현재 내용 + prev_hash]) → current_hash.
    각 라인이 직전 라인 해시에 묶여, 한 글자라도 바뀌면 이후 모든 해시가 어긋난다."""
    return hashlib.sha256((payload_json + prev_hash).encode("utf-8")).hexdigest()


def _append_locked(event_type: str, details) -> str:
    """[_audit_lock + _file_lock 보유 가정] (이미 마스킹된) details 로 레코드 1건을 추가.
    Returns: 기록된 current_hash."""
    prev = _last_chain_hash()
    record = {
        "seq": _count_lines(),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "event_type": event_type,
        "details": details if details is not None else {},
        "prev_hash": prev,
    }
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True)
    record["current_hash"] = _record_hash(prev, canonical)
    with open(_AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    try:
        os.chmod(str(_AUDIT_LOG_PATH), 0o600)
    except OSError:
        pass
    return record["current_hash"]


def write_audit_log(event_type: str, details) -> None:
    """보안 이벤트 1건을 jarvis_audit.log 해시 체인에 안전하게 기록한다.

    절차:
      1) details(문자열/딕셔너리)에서 Path.home()·API_KEY·SECRET 등 민감정보를 [MASKED] 전처리
      2) threading.Lock + 파일 락으로 동시 기록 직렬화
      3) 직전 라인의 current_hash 를 prev_hash 로 읽어와, [현재 내용 + prev_hash]의
         SHA-256 을 current_hash 로 계산해 JSON 한 줄로 append
    """
    try:
        safe_details = _mask_sensitive(details if details is not None else {})
        with _audit_lock, _file_lock():
            _append_locked(event_type, safe_details)
    except Exception as e:
        _audit_logger.warning(f"AUDIT_APPEND_FAILED err={e}")


def chain_event(event: str, detail: dict | None = None):
    """[하위 호환] 기존 호출부를 위한 래퍼 — write_audit_log 로 위임."""
    write_audit_log(event, detail if detail is not None else {})


# ════════════════════════════════════════════════════════════════════
#  무결성 검증 + 자가복구
# ════════════════════════════════════════════════════════════════════
def _verify_core() -> tuple[bool, str, int]:
    """체인을 처음부터 끝까지 따라가며 고리(prev_hash)와 서명(current_hash)을 검증.
    Returns: (무결성OK, 메시지, 검증한 레코드 수). 신/구 스키마 모두 지원."""
    if not _CHAIN_PATH.exists():
        return True, "체인 로그 없음 (검증할 레코드 없음)", 0
    prev = _GENESIS
    count = 0
    try:
        with _chain_lock, open(_CHAIN_PATH, "r", encoding="utf-8") as f:
            for lineno, line in enumerate(f):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                # 신(current_hash)/구(hash) 서명 필드
                stored = rec.pop("current_hash", None)
                if stored is None:
                    stored = rec.pop("hash", None)
                # 신(prev_hash)/구(prev) 링크 필드
                rec_prev = rec.get("prev_hash", rec.get("prev"))
                if rec_prev != prev:
                    return False, f"체인 끊김 (행 {lineno}: prev_hash 불일치 — 행 삭제/누락/재배열 의심)", count
                canonical = json.dumps(rec, ensure_ascii=False, sort_keys=True)
                expected = _record_hash(prev, canonical)
                if not hmac.compare_digest(expected, stored or ""):
                    return False, f"서명 불일치 (행 {lineno}: 레코드 변조 의심)", count
                prev = stored
                count += 1
    except (OSError, json.JSONDecodeError) as e:
        return False, f"체인 파싱 오류: {e}", count
    return True, f"무결성 검증 통과 ({count}개 레코드, 변조 없음)", count


def verify_audit_chain() -> tuple[bool, str]:
    """감사 로그 해시 체인의 무결성을 검증. Returns: (무결성OK, 메시지)."""
    ok, msg, _ = _verify_core()
    return ok, msg


class AuditIntegrityError(RuntimeError):
    """감사 로그 무결성 위반 — 안전 모드 강제 종료에 사용."""


def verify_audit_log_integrity(raise_on_fail: bool = False) -> bool:
    """누적된 감사 로그를 처음부터 끝까지 읽어 사슬 무결성을 검사.
    한 줄이라도 누락/수정되어 해시가 어긋나면 False (raise_on_fail=True 면 예외).
    """
    ok, msg, _ = _verify_core()
    if ok:
        _audit_logger.info(f"AUDIT_INTEGRITY_OK {msg}")
    else:
        _audit_logger.error(f"AUDIT_INTEGRITY_FAIL {msg}")
        if raise_on_fail:
            raise AuditIntegrityError(msg)
    return ok


def reinitialize_audit_log(reason: str = "manual") -> str | None:
    """손상/변조된 체인을 포렌식 보존을 위해 격리(archive)하고 새 체인을 시작한다.
    새 체인의 첫 레코드(CHAIN_REINIT)에 격리 파일명과 원본 SHA-256을 남겨 추적성을 유지.
    Returns: 격리된 파일 경로(str) 또는 None.
    """
    with _chain_lock, _file_lock():
        archived = None
        old_sha = None
        if _CHAIN_PATH.exists():
            try:
                old_bytes = _CHAIN_PATH.read_bytes()
                old_sha = hashlib.sha256(old_bytes).hexdigest()
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                archived = str(_CHAIN_PATH) + f".broken-{ts}"
                os.replace(str(_CHAIN_PATH), archived)
                try:
                    os.chmod(archived, 0o600)
                except OSError:
                    pass
            except OSError as e:
                _audit_logger.error(f"CHAIN_ARCHIVE_FAILED err={e}")
        _append_locked("CHAIN_REINIT", _mask_sensitive({
            "reason": reason,
            "archived_to": os.path.basename(archived) if archived else None,
            "archived_sha256": old_sha,
        }))
    _audit_logger.warning(f"AUDIT_CHAIN_REINIT reason={reason} archived={archived}")
    return archived


# ── 민감 파일 권한 하드닝 (at-rest) ──
# 금융/개인 상태 파일이 그룹/타인 읽기 가능하면 0600으로 강제한다.
_SENSITIVE_FILES = [
    "portfolio.json",
    "social_baseline.json",
    "stock_monitor_rules.json",
    "campus_token.json",
    "token.json",
    "credentials.json",
]


def harden_sensitive_files() -> list[str]:
    """민감 데이터 파일 권한을 0600으로 강제. 변경된 파일 목록 반환."""
    project = Path(__file__).parent
    fixed = []
    for name in _SENSITIVE_FILES:
        fp = project / name
        if fp.exists() and fp.is_file():
            try:
                mode = oct(fp.stat().st_mode)[-3:]
                if mode != "600":
                    os.chmod(str(fp), 0o600)
                    fixed.append(f"{name} ({mode}→600)")
            except OSError:
                pass
    return fixed


# ── CLI: 감사 체인 검증 ──
if __name__ == "__main__":
    import sys
    if "--verify" in sys.argv:
        ok, msg = verify_audit_chain()
        icon = "✓" if ok else "✗"
        print(f"{icon} 감사 로그 무결성: {msg}")
        sys.exit(0 if ok else 1)
    elif "--reinit" in sys.argv:
        archived = reinitialize_audit_log(reason="cli_manual")
        print(f"✓ 감사 체인 재초기화 완료 (격리: {archived})")
    elif "--harden" in sys.argv:
        fixed = harden_sensitive_files()
        if fixed:
            print("권한 하드닝:", ", ".join(fixed))
        else:
            print("✓ 모든 민감 파일 권한 정상 (0600)")
    else:
        print("사용법:")
        print("  python3 security.py --verify   감사 로그 해시 체인 무결성 검증")
        print("  python3 security.py --reinit   손상 체인 격리 후 재초기화")
        print("  python3 security.py --harden   민감 파일 권한(0600) 강제")
