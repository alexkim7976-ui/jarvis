"""
J.A.R.V.I.S — Media Pipeline 통합 테스트 (기존 스위트와 독립 실행)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
실행:
  .venv/bin/python test_media_pipeline.py
  .venv/bin/python -m pytest test_media_pipeline.py -v

검증:
  · URL SSRF 가드 + 리다이렉트 10회 상한
  · subprocess 리스트 인자·프로세스 정리
  · streamlink|ffmpeg 명령 조립
  · AudioPrep sidecar/manifest
  · Worker 큐·이벤트·런타임 poll 계약
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── URL Guard ────────────────────────────────────────────────────

def test_guard_blocks_localhost():
    from media_pipeline.url_guard import guard_media_url
    ok, reason = guard_media_url("http://localhost/stream")
    assert not ok
    assert "차단" in reason or "localhost" in reason.lower()
    print("✓ SSRF — localhost 차단")


def test_guard_allows_https():
    from media_pipeline.url_guard import guard_media_url
    ok, reason = guard_media_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert ok, reason
    print("✓ SSRF — 공개 HTTPS 허용")


def test_redirect_limit_exceeded():
    from media_pipeline.errors import URLGuardError
    from media_pipeline import url_guard

    chain = ["https://a.example/1"]
    for i in range(12):
        chain.append(f"https://a.example/r{i}")

    mock_resp = MagicMock()
    mock_resp.status_code = 302
    mock_resp.headers = {"Location": chain[1]}

    responses = []
    for i in range(len(chain) - 1):
        r = MagicMock()
        r.status_code = 302
        r.headers = {"Location": chain[i + 1]}
        responses.append(r)

    with patch.object(url_guard, "PREFLIGHT_REDIRECTS", True), \
         patch("net.SESSION.head", side_effect=responses):
        try:
            url_guard.preflight_media_url(chain[0], max_redirects=10)
            assert False, "11번째 리다이렉트에서 차단되어야 함"
        except URLGuardError as e:
            assert e.stage == "redirect"
            assert "10" in e.message
    print("✓ 리다이렉트 10회 초과 차단")


def test_strict_dns_failure():
    from media_pipeline.errors import URLGuardError
    from media_pipeline import url_guard

    with patch.dict(os.environ, {"JARVIS_SSRF_STRICT": "1"}), \
         patch.object(url_guard, "guard_media_url", return_value=(True, "OK")), \
         patch.object(url_guard, "PREFLIGHT_REDIRECTS", True), \
         patch("net.SESSION.head", side_effect=OSError("DNS fail")):
        try:
            url_guard.preflight_media_url("https://valid.example.com/live")
            assert False, "엄격 모드 DNS 실패 시 차단"
        except URLGuardError as e:
            assert e.stage == "dns"
    print("✓ Strict 모드 DNS 실패 차단")


# ── Process cleanup ──────────────────────────────────────────────

def test_managed_process_terminate():
    from media_pipeline.proc import ManagedProcess

    proc = ManagedProcess(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    proc.start()
    assert proc.pid
    proc.terminate(force=True)
    assert proc.proc is None
    print("✓ ManagedProcess — 강제 종료·정리")


def test_pipeline_builds_list_argv():
    from media_pipeline.downloader import StreamCapture
    from media_pipeline.proc import require_binaries

    try:
        require_binaries("streamlink", "ffmpeg")
    except Exception:
        print("⊘ pipeline argv — streamlink/ffmpeg 미설치, 스킵")
        return

    cap = StreamCapture(max_seconds=60, timeout_sec=120)
    pipe = cap.build_pipeline("https://www.youtube.com/watch?v=test", Path("/tmp/x.wav"))
    assert isinstance(pipe.upstream.argv, list)
    assert isinstance(pipe.downstream.argv, list)
    assert "shell" not in str(pipe.upstream.argv)
    assert pipe.upstream.argv[0].endswith("streamlink") or "streamlink" in pipe.upstream.argv[0]
    assert "-O" in pipe.upstream.argv
    assert "pipe:0" in pipe.downstream.argv
    print("✓ streamlink|ffmpeg 리스트 인자 파이프라인")


# ── Audio prep ───────────────────────────────────────────────────

def test_audio_prep_manifest(tmp_path=None):
    from media_pipeline.audio_prep import AudioPrepPipeline
    from sandbox import temporary_sandbox

    with temporary_sandbox(prefix="media_test_") as sbx:
        job_id = "testjob01"
        capture = sbx / job_id / "capture.wav"
        capture.parent.mkdir(parents=True)
        # minimal silent wav via ffmpeg if available
        import shutil
        if not shutil.which("ffmpeg"):
            print("⊘ audio prep — ffmpeg 미설치, 스킵")
            return
        subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "0.2", str(capture)],
            check=True, capture_output=True,
        )
        prep = AudioPrepPipeline(output_dir=sbx)
        artifact = prep.prepare(capture, job_id=job_id, title="Test", url="https://example.com")
        assert Path(artifact.master_wav).exists()
        assert Path(artifact.whisper_wav).exists()
        assert Path(artifact.sidecar_json).exists()
        import json
        manifest = json.loads(Path(artifact.sidecar_json).read_text(encoding="utf-8"))
        assert manifest["downstream"]["transcription"] == artifact.whisper_wav
        assert manifest["downstream"]["midi_engine"] == artifact.master_wav
    print("✓ AudioPrep — master/whisper/sidecar 생성")


# ── Worker + Runtime ─────────────────────────────────────────────

def test_worker_submit_and_events():
    from media_pipeline.runtime import MediaPipelineRuntime
    from media_pipeline.events import JobStatus

    events = []
    rt = MediaPipelineRuntime()
    rt.worker._on_complete = lambda job, art: rt._on_job_complete(job, art)

    fake_capture = MagicMock()
    fake_capture.capture.return_value = Path("/fake/capture.wav")
    fake_prep = MagicMock()
    from media_pipeline.events import PrepArtifact
    fake_prep.prepare.return_value = PrepArtifact(
        job_id="x", master_wav="/fake/master.wav", whisper_wav="/fake/w.wav",
        sidecar_json="/fake/m.json",
    )
    rt.worker.capture = fake_capture
    rt.worker.prep = fake_prep

    job = rt.submit_url("https://www.youtube.com/watch?v=abc", title="Live")
    rt.worker.start()
    deadline = time.time() + 5
    while time.time() < deadline:
        if rt.worker.get_job(job.job_id) and rt.worker.get_job(job.job_id).status == JobStatus.READY:
            break
        time.sleep(0.05)
    rt.stop()

    finished = rt.worker.get_job(job.job_id)
    assert finished.status == JobStatus.READY
    evts = rt.get_events()
    assert evts and evts[0]["type"] == "media_ready"
    prep = rt.drain_prep_queue()
    assert prep and prep[0]["master_wav"] == "/fake/master.wav"
    print("✓ Worker+Runtime — mock 캡처·prep·이벤트")


def test_capture_timeout_kills_pipeline():
    from media_pipeline.errors import CaptureError
    from media_pipeline.proc import ManagedProcess, ProcessPipeline
    import os as _os
    import time

    # downstream 20초 sleep — timeout=1 에서 반드시 CaptureError(stage=timeout)
    upstream = ManagedProcess(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    downstream = ManagedProcess(
        [sys.executable, "-c", "import time; time.sleep(20)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    pipeline = ProcessPipeline(upstream, downstream)
    pipeline.start()
    up_pid = upstream.pid
    down_pid = downstream.pid
    t0 = time.time()
    try:
        pipeline.wait(timeout=1)
        assert False, "타임아웃되어야 함"
    except CaptureError as e:
        assert e.stage == "timeout"
        assert time.time() - t0 < 3.0, "타임아웃 kill이 3초 내 완료되어야 함"
    except subprocess.TimeoutExpired:
        assert False, "CaptureError(stage=timeout)가 전파되어야 함"
    finally:
        pipeline.terminate(force=True)

    for label, pid in (("upstream", up_pid), ("downstream", down_pid)):
        if pid:
            try:
                _os.kill(pid, 0)
                assert False, f"{label} PID {pid} 가 아직 살아 있음"
            except ProcessLookupError:
                pass
            except PermissionError:
                pass
    print("✓ Capture timeout — 파이프라인 강제 종료")


def main():
    test_guard_blocks_localhost()
    test_guard_allows_https()
    test_redirect_limit_exceeded()
    test_strict_dns_failure()
    test_managed_process_terminate()
    test_pipeline_builds_list_argv()
    test_audio_prep_manifest()
    test_worker_submit_and_events()
    test_capture_timeout_kills_pipeline()
    print("\n✅ Media Pipeline 테스트 통과")


if __name__ == "__main__":
    main()
