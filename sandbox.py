"""
J.A.R.V.I.S — 파일 시스템 샌드박스 (OS 레벨 격리)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
file_manager.py 의 모든 파일 작업(열기/생성/이동/삭제)을 단일 가상 루트
(JARVIS_SANDBOX_ROOT) 안으로 완전히 격리한다.

핵심 방어선:
  1. 경로 앵커링(Anchoring): 사용자가 준 상대/절대 경로(`/`, `C:\\`, `~`, `..`)를
     모두 분해해 무조건 SANDBOX_ROOT 하위로 치환한다. 시스템 루트 호출 불가.
  2. 심볼릭 링크 디레퍼런싱: os.path.realpath / Path.resolve 로 모든 링크를
     끝까지 추적한 "최종 물리 경로"가 SANDBOX_ROOT 밖이면 PermissionError.
  3. commonpath 인터셉터: 모든 파일 IO 직전 os.path.commonpath 로 루트 이탈을
     원천 차단(크로스플랫폼, 특히 Windows).
  4. OS 격리: macOS/Linux 는 umask·작업 디렉토리 격리 헬퍼, Windows 는 위
     인터셉터로 스트림 차단.

환경변수:
  JARVIS_SANDBOX_ROOT     가상 루트 경로 (기본: <project>/data/sandbox)
  JARVIS_SANDBOX_ENFORCE  강제 여부 (기본: true)
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

# 감사 로그(해시 체인) 연동 — 없으면 무시
try:
    from security import chain_event as _chain_event
except Exception:  # pragma: no cover
    def _chain_event(event, detail=None):
        return None

PROJECT_ROOT = Path(__file__).resolve().parent


def _default_root() -> Path:
    env = os.getenv("JARVIS_SANDBOX_ROOT", "").strip()
    base = Path(env).expanduser() if env else (PROJECT_ROOT / "data" / "sandbox")
    return base.resolve()


# 가상 샌드박스 루트 (프로세스 전역, 절대 경로)
SANDBOX_ROOT: Path = _default_root()

# 강제 모드 — 끄면 file_manager 가 레거시(실제 HOME) 경로로 동작
ENFORCED: bool = os.getenv("JARVIS_SANDBOX_ENFORCE", "true").strip().lower() in (
    "1", "true", "yes", "on"
)

# 샌드박스 내부에 미리 만들어 두는 표준 작업 폴더 (폴더 별칭 UX 유지)
STANDARD_SUBDIRS = ("Desktop", "Downloads", "Documents", "Pictures", "Music", "Movies")

_WIN_DRIVE_RE = re.compile(r"^[A-Za-z]:[\\/](.*)$")


# ════════════════════════════════════════════════════════════════════
#  1) 경로 앵커링 — 모든 입력 경로를 SANDBOX_ROOT 하위로 강제
# ════════════════════════════════════════════════════════════════════
def _to_relative(user_path: str | os.PathLike) -> Path:
    """신뢰할 수 없는 경로를 SANDBOX_ROOT 기준 '상대 경로 조각'으로 정규화.
    드라이브 문자/선행 슬래시/홈(~)/'..'/'.'/빈 조각을 모두 제거해 루트 탈출을 봉쇄."""
    s = str(user_path).strip().replace("\\", "/")
    if not s:
        return Path()
    # 홈 디렉토리 표기 제거
    if s.startswith("~"):
        s = s[1:]
    # Windows 드라이브( C:\ , D:/ ) 제거
    m = _WIN_DRIVE_RE.match(s)
    if m:
        s = m.group(1)
    # UNC( \\\\server\\share ) 잔재 및 선행 슬래시 제거
    s = s.lstrip("/")
    parts: list[str] = []
    for part in s.split("/"):
        if part in ("", ".", ".."):
            # 상위 이동/현재/빈 조각은 폐기 → 루트 밖으로 못 올라감
            continue
        # 콜론(드라이브)·널바이트 등 잔여 위험 문자 제거
        part = part.replace("\x00", "").split(":")[0] if ":" in part else part
        if part:
            parts.append(part)
    return Path(*parts) if parts else Path()


def anchor_path(user_path: str | os.PathLike) -> Path:
    """사용자 경로를 SANDBOX_ROOT 하위 경로로 앵커링(미해석 절대경로 반환).
    시스템 루트('/', 'C:\\')를 호출해도 결과는 항상 SANDBOX_ROOT 내부."""
    rel = _to_relative(user_path)
    return (SANDBOX_ROOT / rel) if str(rel) != "." else SANDBOX_ROOT


# ════════════════════════════════════════════════════════════════════
#  2) 심볼릭 링크 디레퍼런싱 + 3) commonpath 인터셉터
# ════════════════════════════════════════════════════════════════════
def _real(path: str | os.PathLike) -> Path:
    """심볼릭 링크/.. 를 끝까지 추적한 최종 물리 경로 (존재하지 않아도 안전)."""
    return Path(os.path.realpath(os.fspath(path)))


def is_within_sandbox(path: str | os.PathLike) -> bool:
    """최종 물리 경로가 SANDBOX_ROOT 하위인지 commonpath 로 판정."""
    try:
        real = _real(path)
        return os.path.commonpath([str(SANDBOX_ROOT), str(real)]) == str(SANDBOX_ROOT)
    except (ValueError, OSError):
        # 서로 다른 드라이브 등 → 비교 불가 → 외부로 간주
        return False


def assert_within_sandbox(path: str | os.PathLike) -> Path:
    """파일 작업 직전 호출. 심볼릭 링크를 완전 추적한 최종 경로가 샌드박스 밖이면
    즉시 PermissionError. 통과 시 최종 물리 경로(Path)를 반환."""
    real = _real(path)
    try:
        inside = os.path.commonpath([str(SANDBOX_ROOT), str(real)]) == str(SANDBOX_ROOT)
    except (ValueError, OSError):
        inside = False
    if not inside:
        _chain_event("SANDBOX_ESCAPE_BLOCKED",
                     {"requested": str(path)[:200], "resolved": str(real)[:200]})
        raise PermissionError(
            f"샌드박스 외부 접근이 차단되었습니다 (symlink/경로 이탈): {real}")
    return real


def resolve_in_sandbox(user_path: str | os.PathLike, *, must_exist: bool = False,
                       create_parents: bool = False) -> Path:
    """앵커링 → 심볼릭 링크 추적 → 경계 검증을 한 번에 수행하는 안전 진입점.
    Returns: SANDBOX_ROOT 내부가 보장된 최종 물리 Path.
    Raises: PermissionError(경계 이탈), FileNotFoundError(must_exist)."""
    anchored = anchor_path(user_path)
    if create_parents:
        anchored.parent.mkdir(parents=True, exist_ok=True)
    real = assert_within_sandbox(anchored)
    if must_exist and not real.exists():
        raise FileNotFoundError(f"샌드박스 내 경로가 존재하지 않습니다: {real}")
    return real


# ════════════════════════════════════════════════════════════════════
#  4) 샌드박스 초기화 + 크로스플랫폼 OS 격리
# ════════════════════════════════════════════════════════════════════
def init_sandbox(create_standard_dirs: bool = True) -> Path:
    """SANDBOX_ROOT 와 표준 작업 폴더를 생성하고 소유자 전용 권한으로 잠근다."""
    SANDBOX_ROOT.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(SANDBOX_ROOT, stat.S_IRWXU)  # 0700
        except OSError:
            pass
    if create_standard_dirs:
        for sub in STANDARD_SUBDIRS:
            (SANDBOX_ROOT / sub).mkdir(exist_ok=True)
    return SANDBOX_ROOT


def enforce_os_isolation(chdir: bool = False) -> dict:
    """실행 환경을 감지해 OS 레벨 안전장치를 적용.

    · macOS/Linux: umask 0077(신규 파일 소유자 전용) 적용. chdir=True 면 작업
      디렉토리를 샌드박스로 이동(상대 경로 기준점을 루트로 고정). 추가로 더 강한
      격리가 필요하면 sandbox_exec_command() 로 sandbox-exec 래핑을 사용할 수 있다.
    · Windows: OS 차원의 chroot 가 없으므로, 본 모듈의 anchor_path +
      assert_within_sandbox(commonpath 인터셉터)가 모든 파일 IO 의 경계를 강제한다.

    Returns: 적용 내역 dict
    """
    init_sandbox()
    applied: dict = {"platform": sys.platform, "root": str(SANDBOX_ROOT),
                     "enforced": ENFORCED, "measures": []}
    if os.name == "nt":
        applied["measures"].append("commonpath-interceptor")
    else:
        try:
            os.umask(0o077)
            applied["measures"].append("umask=0077")
        except OSError:
            pass
        if chdir:
            try:
                os.chdir(SANDBOX_ROOT)
                applied["measures"].append("chdir-to-root")
            except OSError:
                pass
        applied["measures"].append("realpath-dereference")
    _chain_event("SANDBOX_INIT", applied)
    return applied


def sandbox_exec_command(argv: list[str]) -> list[str] | None:
    """(macOS 전용) sandbox-exec 로 파일 쓰기를 SANDBOX_ROOT 로만 제한하는 실행
    래퍼 명령을 생성해 반환한다. 호출부에서 subprocess 로 사용 가능.
    sandbox-exec 가 없거나 macOS 가 아니면 None."""
    if sys.platform != "darwin" or shutil.which("sandbox-exec") is None:
        return None
    profile = (
        "(version 1)"
        "(allow default)"
        "(deny file-write*)"
        f'(allow file-write* (subpath "{SANDBOX_ROOT}"))'
    )
    return ["sandbox-exec", "-p", profile, *argv]


# ════════════════════════════════════════════════════════════════════
#  5) 단위 테스트용 가상 디렉토리 유틸 (안전 삭제 보장)
# ════════════════════════════════════════════════════════════════════
def create_test_sandbox(prefix: str = "jarvis_sbx_") -> Path:
    """SANDBOX_ROOT 내부에 격리된 임시 테스트 디렉토리를 만들어 경로를 반환.
    LLM 오작동 시뮬레이션·단위 테스트가 실제 시스템을 건드리지 않도록 보장."""
    init_sandbox(create_standard_dirs=False)
    return Path(tempfile.mkdtemp(prefix=prefix, dir=str(SANDBOX_ROOT)))


def cleanup_path(path: str | os.PathLike) -> bool:
    """주어진 경로를 정리(삭제)하되, SANDBOX_ROOT 내부임을 강제 검증 후에만 삭제.
    외부를 가리키면 PermissionError 로 거부 → 시스템 파일 보호."""
    target = _real(path)
    if str(target) == str(SANDBOX_ROOT):
        raise PermissionError("샌드박스 루트 자체는 삭제할 수 없습니다.")
    assert_within_sandbox(target)
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=True)
    elif target.exists() or target.is_symlink():
        target.unlink(missing_ok=True)
    return True


def cleanup_sandbox(keep_root: bool = True) -> int:
    """SANDBOX_ROOT 내부의 모든 항목을 삭제(루트는 옵션에 따라 유지).
    Returns: 삭제한 최상위 항목 수."""
    if not SANDBOX_ROOT.exists():
        return 0
    removed = 0
    for child in SANDBOX_ROOT.iterdir():
        try:
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    if not keep_root:
        shutil.rmtree(SANDBOX_ROOT, ignore_errors=True)
    return removed


@contextmanager
def temporary_sandbox(prefix: str = "jarvis_test_"):
    """테스트용 임시 샌드박스 디렉토리를 생성하고 종료 시 자동 정리하는 컨텍스트 매니저.

    예:
        with temporary_sandbox() as d:
            (d / "a.txt").write_text("hi")   # d 는 SANDBOX_ROOT 내부 보장
        # 블록 종료 시 d 전체 삭제 (시스템엔 흔적 없음)
    """
    d = create_test_sandbox(prefix=prefix)
    try:
        yield d
    finally:
        try:
            cleanup_path(d)
        except (PermissionError, OSError):
            pass


if __name__ == "__main__":
    info = enforce_os_isolation()
    print("✓ 샌드박스 초기화 완료")
    for k, v in info.items():
        print(f"  {k}: {v}")
