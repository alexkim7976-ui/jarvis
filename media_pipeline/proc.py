"""
media_pipeline — 안전 서브프로세스 실행·정리
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
리스트 인자만 사용(shell=False). 타임아웃·파이프 누수·좀비 프로세스 방지.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from typing import IO

from .config import PROC_TERMINATE_GRACE_SEC
from .errors import CaptureError


def _which(name: str) -> str | None:
    import shutil
    return shutil.which(name)


class ManagedProcess:
    """단일 서브프로세스 — 종료 시 SIGTERM → wait → SIGKILL."""

    def __init__(
        self,
        argv: list[str],
        *,
        stdin: IO | int | None = None,
        stdout: IO | int | None = subprocess.PIPE,
        stderr: IO | int | None = subprocess.PIPE,
    ):
        if not argv or not isinstance(argv, list):
            raise ValueError("argv must be a non-empty list")
        if any(not isinstance(a, str) for a in argv):
            raise ValueError("argv elements must be str")
        self.argv = list(argv)
        self._stdin = stdin
        self._stdout = stdout
        self._stderr = stderr
        self.proc: subprocess.Popen | None = None
        self._closed_stdout = False

    def start(self) -> "ManagedProcess":
        kwargs: dict = {
            "stdin": self._stdin,
            "stdout": self._stdout,
            "stderr": self._stderr,
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen(self.argv, **kwargs)  # noqa: S603
        return self

    @property
    def pid(self) -> int | None:
        return self.proc.pid if self.proc else None

    @property
    def stdout_pipe(self):
        return self.proc.stdout if self.proc else None

    def close_stdout_to_child(self) -> None:
        if self.proc and self.proc.stdout and not self._closed_stdout:
            try:
                self.proc.stdout.close()
            except Exception:
                pass
            self._closed_stdout = True

    def wait(self, timeout: float | None = None) -> int:
        if not self.proc:
            return 0
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            self.terminate(force=True)
            raise exc

    def terminate(self, *, force: bool = False, grace_sec: float | None = None) -> None:
        proc = self.proc
        if not proc or proc.poll() is not None:
            self.proc = None
            return
        grace = PROC_TERMINATE_GRACE_SEC if grace_sec is None else grace_sec
        try:
            if sys.platform == "win32":
                if force:
                    proc.kill()
                else:
                    proc.terminate()
            else:
                pgid = os.getpgid(proc.pid)
                sig = signal.SIGKILL if force else signal.SIGTERM
                os.killpg(pgid, sig)
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                if sys.platform == "win32":
                    proc.kill()
                else:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=1)
            except Exception:
                pass
        except ProcessLookupError:
            pass
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        finally:
            for stream in (proc.stdout, proc.stderr, proc.stdin):
                if stream:
                    try:
                        stream.close()
                    except Exception:
                        pass
            self.proc = None

    def read_stderr_tail(self, max_bytes: int = 4096) -> str:
        if not self.proc or not self.proc.stderr:
            return ""
        try:
            data = self.proc.stderr.read(max_bytes)
            return (data or b"").decode("utf-8", errors="replace").strip()
        except Exception:
            return ""


class ProcessPipeline:
    """streamlink | ffmpeg 파이프라인 — 두 프로세스를 한 그룹으로 정리."""

    def __init__(self, upstream: ManagedProcess, downstream: ManagedProcess):
        self.upstream = upstream
        self.downstream = downstream

    def start(self) -> "ProcessPipeline":
        self.upstream.start()
        self.downstream.start()
        self.upstream.close_stdout_to_child()
        return self

    def wait(self, timeout: float | None = None) -> tuple[int, int]:
        """downstream 종료 대기 후 upstream 정리. 타임아웃 시 전체 kill."""
        try:
            rc_down = self.downstream.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.terminate(force=True)
            raise CaptureError("캡처 타임아웃", stage="timeout")
        rc_up = self.upstream.proc.wait(timeout=5) if self.upstream.proc else 0
        err = self.downstream.read_stderr_tail() or self.upstream.read_stderr_tail()
        if rc_down != 0:
            raise CaptureError(f"ffmpeg 종료 코드 {rc_down}: {err[:200]}", stage="ffmpeg")
        if rc_up not in (0, -15, -9, 1):
            raise CaptureError(f"streamlink 종료 코드 {rc_up}: {err[:200]}", stage="streamlink")
        return rc_up, rc_down

    def terminate(self, *, force: bool = False) -> None:
        self.downstream.terminate(force=force)
        self.upstream.terminate(force=force)


def require_binaries(*names: str) -> dict[str, str]:
    """streamlink/ffmpeg 존재 확인 — 경로 dict 반환."""
    found: dict[str, str] = {}
    missing = []
    for name in names:
        path = _which(name)
        if path:
            found[name] = path
        else:
            missing.append(name)
    if missing:
        raise CaptureError(
            f"필수 바이너리 없음: {', '.join(missing)} (brew install {' '.join(missing)})",
            stage="deps",
        )
    return found
