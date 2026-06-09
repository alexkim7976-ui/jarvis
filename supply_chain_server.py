"""
J.A.R.V.I.S — Supply Chain Agent 백엔드 자동 기동/종료
──────────────────────────────────────────
jarvis_v2.py 실행 시 Java Spring Boot 백엔드가 꺼져 있으면 자동으로 띄운다.
종료 시 JARVIS가 기동한 프로세스만 정리한다.
"""
from __future__ import annotations

import atexit
import os
import secrets
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

try:
    from secure_config import get_secret
except ImportError:
    def get_secret(key: str, default: str = "") -> str:
        return os.getenv(key, default)

try:
    import tls_certs
except Exception:  # pragma: no cover
    tls_certs = None  # type: ignore

from supply_chain_client import SUPPLY_CHAIN_URL, health_check, submit_typed_command

PROJECT_ROOT = Path(__file__).resolve().parent
AGENT_DIR = PROJECT_ROOT / "supply-chain-agent"
LOG_DIR = PROJECT_ROOT / ".logs"
STARTUP_TIMEOUT = int(os.getenv("SUPPLY_CHAIN_STARTUP_TIMEOUT", "120"))
POLL_INTERVAL = 2.0

_process: subprocess.Popen | None = None
_started_by_jarvis = False
_log_file = None


def _log(level: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    icons = {"OK": "✓", "WARN": "⚠", "INFO": "◈", "ERR": "✗"}
    print(f"[{ts}] {icons.get(level, '·')} [백엔드] {msg}")


def _auto_start_enabled() -> bool:
    return os.getenv("SUPPLY_CHAIN_AUTO_START", "true").strip().lower() in (
        "true", "1", "yes", "on"
    )


def _ensure_api_key() -> None:
    """공유 시크릿이 없으면 무작위 키를 생성해 os.environ에 주입한다.
    같은 프로세스의 클라이언트(supply_chain_client)와 자식 Java 프로세스(env 상속)가
    동일 키를 공유하므로, 사용자가 설정하지 않아도 인증이 자동 활성화된다."""
    if not os.getenv("SUPPLY_CHAIN_API_KEY", "").strip():
        os.environ["SUPPLY_CHAIN_API_KEY"] = secrets.token_urlsafe(32)
        _log("INFO", "백엔드 공유 시크릿 자동 생성 (X-API-Key 인증 활성화)")


def _server_port() -> int:
    parsed = urlparse(SUPPLY_CHAIN_URL)
    return parsed.port or (443 if parsed.scheme == "https" else 80)


def _ensure_tls() -> bool:
    """로컬 HTTPS 인증서를 보장하고 env(키스토어/CA 경로)를 주입한다.
    SUPPLY_CHAIN_URL이 https일 때만 시도. 실패 시 False를 반환해 호출부가
    평문 폴백을 결정한다. (verify=False 같은 임시방편은 사용하지 않음)"""
    if urlparse(SUPPLY_CHAIN_URL).scheme != "https":
        return False
    if tls_certs is None:
        _log("WARN", "tls_certs 모듈 없음 — HTTPS 비활성")
        return False
    info = tls_certs.ensure_certificates()
    if not info:
        _log("WARN", "TLS 인증서 생성 실패(cryptography 확인) — HTTPS 비활성")
        return False
    tls_certs.inject_env(info)
    _log("OK", f"로컬 TLS 인증서 준비 (PKCS12 {Path(info['keystore']).name}, CA {Path(info['ca_cert']).name})")
    return True


def _harden_service_account_key() -> None:
    """Firestore 접근용 Google 서비스 계정 키 JSON의 OS 권한을 소유자 읽기 전용으로 제한.
    · POSIX: chmod 400 (소유자 읽기 전용)
    · Windows: icacls 로 상속 제거 후 현재 사용자에게만 읽기 권한 부여
    GOOGLE_APPLICATION_CREDENTIALS(ADC 표준) 또는 SUPPLY_CHAIN_SA_KEY 경로를 대상으로 한다."""
    key_path = (os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")
                or os.getenv("SUPPLY_CHAIN_SA_KEY", "")).strip()
    if not key_path:
        return
    p = Path(key_path).expanduser()
    if not p.is_file():
        _log("WARN", f"서비스 계정 키 파일 없음 — 권한 변경 생략: {p}")
        return
    try:
        if sys.platform == "win32":
            user = os.getenv("USERNAME") or os.getenv("USER") or ""
            subprocess.run(["icacls", str(p), "/inheritance:r"],
                           check=False, capture_output=True)
            if user:
                subprocess.run(["icacls", str(p), "/grant:r", f"{user}:R"],
                               check=False, capture_output=True)
            try:
                os.chmod(p, 0o400)  # 보조적 (Windows에서는 제한적 효과)
            except OSError:
                pass
            _log("OK", f"서비스 계정 키 권한 제한 완료 (icacls 소유자 전용): {p.name}")
        else:
            os.chmod(p, 0o400)  # 소유자 읽기 전용
            _log("OK", f"서비스 계정 키 권한 제한 완료 (chmod 400): {p.name}")
    except OSError as e:
        _log("WARN", f"서비스 계정 키 권한 변경 실패: {e}")


def _find_mvn() -> str | None:
    for candidate in (shutil.which("mvn"), "/opt/homebrew/bin/mvn", "/usr/local/bin/mvn"):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _find_java() -> str | None:
    for candidate in (
        shutil.which("java"),
        "/opt/homebrew/opt/openjdk@21/bin/java",
        "/usr/local/opt/openjdk@21/bin/java",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def _find_jar() -> Path | None:
    target = AGENT_DIR / "target"
    if not target.is_dir():
        return None
    jars = sorted(target.glob("supply-chain-agent-*.jar"))
    jars = [j for j in jars if not j.name.endswith("-sources.jar")]
    return jars[-1] if jars else None


def _build_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("SUPPLY_CHAIN_STORAGE", "memory")
    if sys.platform == "win32":
        # Windows: install.ps1 / 시스템 PATH의 Java 사용
        java_home = env.get("JAVA_HOME", "")
        if not java_home:
            for candidate in (
                r"C:\Program Files\Microsoft\jdk-21",
                r"C:\Program Files\Java\jdk-21",
                r"C:\Program Files\Eclipse Adoptium\jdk-21",
            ):
                if Path(candidate).exists():
                    env["JAVA_HOME"] = candidate
                    env["PATH"] = f"{candidate}\\bin;" + env.get("PATH", "")
                    break
    else:
        # macOS Homebrew Java (install.sh와 동일)
        for jhome in (
            "/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home",
            "/usr/local/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home",
        ):
            if Path(jhome).exists():
                env.setdefault("JAVA_HOME", jhome)
                env["PATH"] = f"{jhome}/bin:" + env.get("PATH", "")
                break
        env["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + env.get("PATH", "")
    api_key = get_secret("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    if api_key:
        env["GEMINI_API_KEY"] = api_key
    gemini_model = os.getenv("GEMINI_MODEL", "")
    if gemini_model:
        env["GEMINI_MODEL"] = gemini_model
    firestore = os.getenv("FIRESTORE_PROJECT_ID", "")
    if firestore:
        env["FIRESTORE_PROJECT_ID"] = firestore
    return env


def _launch_command(port: int) -> list[str] | None:
    """JAR 우선, 없으면 Maven spring-boot:run."""
    jar = _find_jar()
    java = _find_java()
    if jar and java:
        return [java, "-jar", str(jar), f"--server.port={port}"]
    mvn = _find_mvn()
    if mvn:
        return [
            mvn, "-q", "spring-boot:run",
            f"-Dspring-boot.run.arguments=--server.port={port}",
        ]
    return None


def start_backend() -> bool:
    """
    백엔드가 이미 떠 있으면 True.
    꺼져 있으면 Maven으로 기동 후 health OK까지 대기.
    """
    global _process, _started_by_jarvis, _log_file

    # 초기 health_check가 https 호출이므로, 그 전에 CA 인증서를 보장해야 검증이 가능.
    _ensure_tls()

    if health_check():
        _log("OK", f"Supply Chain Agent 이미 실행 중 ({SUPPLY_CHAIN_URL})")
        return True

    if not _auto_start_enabled():
        _log("INFO", "자동 기동 비활성 (SUPPLY_CHAIN_AUTO_START=false)")
        return False

    if not AGENT_DIR.is_dir():
        _log("WARN", f"supply-chain-agent 디렉터리 없음: {AGENT_DIR}")
        return False

    mvn = _find_mvn()
    jar = _find_jar()
    if not jar and not mvn:
        _log("WARN", "Java/Maven 없음 — ./install.sh 실행 또는 brew install openjdk@21 maven")
        return False

    api_key = get_secret("GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        _log("WARN", "GEMINI_API_KEY 없음 — 백엔드 기동 생략")
        return False

    # 우리가 백엔드를 직접 띄우는 경우 → 공유 시크릿 보장 (인증 자동 활성화)
    _ensure_api_key()
    # Firestore 서비스 계정 키 권한 최소화 (소유자 읽기 전용)
    _harden_service_account_key()

    port = _server_port()
    cmd = _launch_command(port)
    if not cmd:
        _log("WARN", "백엔드 기동 명령 구성 실패")
        return False

    LOG_DIR.mkdir(exist_ok=True)
    _log_file = open(LOG_DIR / "supply_chain_backend.log", "a", encoding="utf-8")
    _log_file.write(f"\n--- boot {time.strftime('%Y-%m-%d %H:%M:%S')} port={port} ---\n")
    _log_file.flush()

    mode = "JAR" if jar else "Maven"
    _log("INFO", f"Supply Chain Agent 기동 중… ({mode}, 포트 {port}, 최대 {STARTUP_TIMEOUT}초)")

    try:
        _process = subprocess.Popen(
            cmd,
            cwd=str(AGENT_DIR) if jar is None else None,
            env=_build_env(),
            stdout=_log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        _started_by_jarvis = True
    except OSError as e:
        _log("ERR", f"프로세스 기동 실패: {e}")
        return False

    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if _process.poll() is not None:
            _log("ERR", f"백엔드 조기 종료 (exit={_process.returncode}) — .logs/supply_chain_backend.log 확인")
            _started_by_jarvis = False
            _process = None
            return False
        if health_check():
            _log("OK", f"Supply Chain Agent 준비 완료 ({SUPPLY_CHAIN_URL})")
            return True
        time.sleep(POLL_INTERVAL)

    _log("WARN", f"헬스체크 타임아웃 ({STARTUP_TIMEOUT}초) — Maven 첫 빌드는 더 걸릴 수 있음")
    return False


def stop_backend():
    """JARVIS가 기동한 백엔드만 종료."""
    global _process, _started_by_jarvis, _log_file

    if not _started_by_jarvis or _process is None:
        return

    if _process.poll() is not None:
        _process = None
        _started_by_jarvis = False
        return

    _log("INFO", "Supply Chain Agent 종료 중…")
    try:
        os.killpg(os.getpgid(_process.pid), signal.SIGTERM)
        _process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(_process.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass
    except ProcessLookupError:
        pass
    except Exception as e:
        _log("WARN", f"종료 중 오류: {e}")
    finally:
        _process = None
        _started_by_jarvis = False
        if _log_file:
            try:
                _log_file.close()
            except Exception:
                pass
            _log_file = None
        _log("OK", "Supply Chain Agent 종료")


def ensure_running() -> bool:
    """부팅 시 호출 — atexit에 종료 핸들러 등록."""
    atexit.register(stop_backend)
    return start_backend()


# ════════════════════════════════════════════════════════════════════
#  타이핑(텍스트) 명령 인터페이스 — 음성과 동일한 인증·검증 경로 사용
# ════════════════════════════════════════════════════════════════════
def submit_text_command(text: str, *, mode: str = "simulate") -> dict:
    """사용자가 CLI에서 직접 타이핑한 텍스트 명령을 처리한다.

    음성(STT)과 동일하게:
      1) 백엔드가 떠 있는지 보장(없으면 자동 기동)하고 공유 시크릿(X-API-Key)을 확보,
      2) supply_chain_client.submit_typed_command()로 입력 게이트(validate_command_input,
         명령어 주입/경로 조작 차단 + 인젝션 중화)와 인증 헤더를 거쳐 백엔드로 전송한다.
    Returns: 백엔드 API JSON ({"success": False, ...} 형태로 실패 사유 포함 가능)
    """
    if not (text or "").strip():
        return {"success": False, "message": "빈 입력입니다."}

    # 음성 경로와 동일한 시크릿 보장 → 인증 헤더 누락 방지
    _ensure_api_key()
    # 클라이언트 HTTPS 검증용 CA 보장 (health_check가 https 호출)
    _ensure_tls()
    if not health_check():
        if not start_backend():
            return {
                "success": False,
                "message": "Supply Chain Agent 백엔드를 사용할 수 없습니다 (기동 실패).",
            }
    try:
        return submit_typed_command(text, mode=mode)
    except ValueError as e:
        # 입력 게이트 차단 (명령어 주입/경로 조작 등)
        _log("WARN", f"타이핑 입력 차단: {e}")
        return {"success": False, "message": str(e)}
    except Exception as e:
        _log("ERR", f"타이핑 명령 처리 실패: {e}")
        return {"success": False, "message": f"처리 실패: {e}"}


def _interactive_cli() -> None:
    """터미널에서 타이핑 명령을 반복 입력받아 백엔드로 전송하는 대화형 모드."""
    _log("INFO", "타이핑 명령 모드 — 빈 줄 또는 'exit' 입력 시 종료")
    ensure_running()
    while True:
        try:
            text = input("\n[타이핑] 시나리오/명령 입력 > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text or text.lower() in ("exit", "quit", "q"):
            break
        result = submit_text_command(text)
        msg = result.get("jarvisBriefingMessage") or result.get("message") or str(result)
        _log("OK" if result.get("success", True) else "WARN", msg)


if __name__ == "__main__":
    argv = sys.argv[1:]
    if argv and argv[0] in ("--text", "-t"):
        # 단발성 타이핑 명령:  python supply_chain_server.py --text "TSMC 2주 가동중단 시뮬레이션"
        command = " ".join(argv[1:]).strip()
        res = submit_text_command(command)
        print(res.get("jarvisBriefingMessage") or res.get("message") or res)
        sys.exit(0 if res.get("success", True) else 1)
    if argv and argv[0] in ("--cli", "-i"):
        # 대화형 타이핑 모드
        _interactive_cli()
        sys.exit(0)
    ok = ensure_running()
    sys.exit(0 if ok else 1)
