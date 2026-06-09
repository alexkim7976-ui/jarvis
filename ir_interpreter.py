"""
J.A.R.V.I.S — IR Live Interpreter
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
유튜브 라이브/기업 IR 페이지 오디오 스트림을 백그라운드에서 수집하고,
Whisper STT + Gemini 번역/요약으로 한국어 IR 콘콜 브리핑 보드를 갱신한다.

파이프라인:
  streamlink(URL) -> ffmpeg(raw PCM 16k mono) -> 메모리 WAV chunk
  -> OpenAI Whisper(wisper-1) -> Gemini 한국어 번역
  -> 5분 누적 Gemini IR 요약 -> 이벤트 큐 -> jarvis_v2 HUD/TTS

.env:
  OPENAI_API_KEY=...
  GEMINI_API_KEY=...
  IR_CHUNK_SECONDS=30
  IR_SUMMARY_INTERVAL=300

의존성:
  brew install streamlink ffmpeg
  pip install openai google-generativeai
"""

from __future__ import annotations

import io
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import wave
from collections import deque
from datetime import datetime

from openai import OpenAI


OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
IR_MODEL = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")  # 번역·요약 경량 작업 → FLASH
IR_CHUNK_SECONDS = int(os.getenv("IR_CHUNK_SECONDS", "30"))
IR_SUMMARY_INTERVAL = int(os.getenv("IR_SUMMARY_INTERVAL", "300"))
IR_SAMPLE_RATE = 16000
IR_CHANNELS = 1
IR_SAMPLE_WIDTH = 2  # s16le


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 🎧 [IR통역] {msg}")


def _pcm_to_wav_bytes(pcm: bytes) -> io.BytesIO:
    """raw PCM(s16le mono 16k)을 Whisper 업로드 가능한 WAV 메모리 파일로 래핑."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(IR_CHANNELS)
        wf.setsampwidth(IR_SAMPLE_WIDTH)
        wf.setframerate(IR_SAMPLE_RATE)
        wf.writeframes(pcm)
    buf.seek(0)
    buf.name = "ir_chunk.wav"
    return buf


def _safe_json(text: str) -> dict:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.strip("`")
        if t.lower().startswith("json"):
            t = t[4:].strip()
    try:
        return json.loads(t)
    except Exception:
        return {}


def _gemini_model(system_instruction: str):
    import google.generativeai as genai
    genai.configure(api_key=GEMINI_API_KEY)
    return genai.GenerativeModel(model_name=IR_MODEL,
                                 system_instruction=system_instruction)


class IRInterpreter:
    """
    장시간 라이브 IR 통역 데몬.
    메인 PyAudio/웹캠 루프와 충돌하지 않도록 외부 프로세스 + 별도 스레드만 사용한다.
    """

    def __init__(self):
        self._event_q = deque(maxlen=30)
        self._lock = threading.Lock()
        self._text_lock = threading.Lock()
        self._running = False
        self._stream_thread = None
        self._summary_thread = None
        self._streamlink_proc = None
        self._ffmpeg_proc = None
        self._client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None
        self._translated_chunks = []
        self._latest_summary = None
        self._last_error = ""
        self.url = ""
        self.company = "IR 콘퍼런스 콜"
        self.chunk_seconds = IR_CHUNK_SECONDS
        self.summary_interval = IR_SUMMARY_INTERVAL

    # ── lifecycle ──
    def start(self, url: str, company: str = "", chunk_seconds: int = IR_CHUNK_SECONDS,
              summary_interval: int = IR_SUMMARY_INTERVAL) -> dict:
        if self._running:
            return {"success": False, "message": "이미 IR 통역이 실행 중입니다."}
        if not OPENAI_API_KEY:
            return {"success": False, "message": "OPENAI_API_KEY가 필요합니다."}
        if not GEMINI_API_KEY:
            return {"success": False, "message": "GEMINI_API_KEY가 필요합니다."}
        if not shutil.which("streamlink"):
            return {"success": False, "message": "streamlink가 필요합니다. brew install streamlink"}
        if not shutil.which("ffmpeg"):
            return {"success": False, "message": "ffmpeg가 필요합니다. brew install ffmpeg"}
        if not url:
            return {"success": False, "message": "IR 라이브 URL이 필요합니다."}

        # SSRF/이그레스 가드 — 내부망·루프백·클라우드 메타데이터 URL 차단
        try:
            from security import validate_url, chain_event
            safe, reason = validate_url(url)
            if not safe:
                chain_event("SSRF_BLOCK", {"url": url[:80], "reason": reason})
                return {"success": False, "message": f"보안 차단(SSRF 방지): {reason}"}
        except ImportError:
            pass

        self.url = url
        self.company = company or "IR 콘퍼런스 콜"
        self.chunk_seconds = max(10, int(chunk_seconds or IR_CHUNK_SECONDS))
        self.summary_interval = max(60, int(summary_interval or IR_SUMMARY_INTERVAL))
        self._translated_chunks = []
        self._latest_summary = None
        self._last_error = ""
        self._running = True

        self._stream_thread = threading.Thread(target=self._stream_worker, daemon=True)
        self._summary_thread = threading.Thread(target=self._summary_worker, daemon=True)
        self._stream_thread.start()
        self._summary_thread.start()
        _log(f"시작: {self.company} | chunk={self.chunk_seconds}s summary={self.summary_interval}s")
        return {
            "success": True,
            "message": f"{self.company} 실시간 IR 통역을 시작했습니다.",
            "briefing": f"선생님, {self.company} 실시간 IR 통역을 시작했습니다.",
        }

    def stop(self) -> dict:
        self._running = False
        self._terminate_processes()
        if self._stream_thread:
            self._stream_thread.join(timeout=3)
        if self._summary_thread:
            self._summary_thread.join(timeout=3)
        return {"success": True, "message": "IR 통역을 중지했습니다.", "briefing": "IR 통역을 중지했습니다, 선생님."}

    def get_events(self):
        events = []
        with self._lock:
            while self._event_q:
                events.append(self._event_q.popleft())
        return events

    def requeue(self, events):
        with self._lock:
            for evt in reversed(events):
                self._event_q.appendleft(evt)

    # ── subprocess streaming ──
    def _start_processes(self):
        streamlink_cmd = ["streamlink", "-O", self.url, "best"]
        ffmpeg_cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-i", "pipe:0",
            "-vn", "-f", "s16le", "-acodec", "pcm_s16le",
            "-ac", "1", "-ar", str(IR_SAMPLE_RATE), "pipe:1",
        ]
        self._streamlink_proc = subprocess.Popen(
            streamlink_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd, stdin=self._streamlink_proc.stdout, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        if self._streamlink_proc.stdout:
            self._streamlink_proc.stdout.close()

    def _terminate_processes(self):
        for proc in [self._ffmpeg_proc, self._streamlink_proc]:
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        self._ffmpeg_proc = None
        self._streamlink_proc = None

    def _read_exact(self, nbytes: int) -> bytes:
        out = bytearray()
        stdout = self._ffmpeg_proc.stdout if self._ffmpeg_proc else None
        while self._running and stdout and len(out) < nbytes:
            chunk = stdout.read(nbytes - len(out))
            if not chunk:
                break
            out.extend(chunk)
        return bytes(out)

    def _stream_worker(self):
        chunk_bytes = self.chunk_seconds * IR_SAMPLE_RATE * IR_CHANNELS * IR_SAMPLE_WIDTH
        try:
            self._start_processes()
            while self._running:
                pcm = self._read_exact(chunk_bytes)
                if not self._running:
                    break
                if len(pcm) < IR_SAMPLE_RATE * IR_SAMPLE_WIDTH * 3:
                    self._last_error = "오디오 스트림이 종료되었거나 읽기 실패"
                    break
                english = self._whisper_transcribe(pcm)
                if not english:
                    continue
                korean = self._translate_to_korean(english)
                if korean:
                    with self._text_lock:
                        self._translated_chunks.append({
                            "ts": datetime.now().strftime("%H:%M:%S"),
                            "text": korean,
                        })
        except Exception as e:
            self._last_error = str(e)
            _log(f"스트림 워커 오류: {e}")
        finally:
            self._terminate_processes()
            self._running = False

    # ── AI calls ──
    def _whisper_transcribe(self, pcm: bytes) -> str:
        try:
            wav_buf = _pcm_to_wav_bytes(pcm)
            resp = self._client.audio.transcriptions.create(
                model="whisper-1",
                file=wav_buf,
                response_format="text",
                language="en",
            )
            return (resp or "").strip()
        except Exception as e:
            self._last_error = f"Whisper 실패: {e}"
            _log(self._last_error)
            return ""

    def _translate_to_korean(self, text: str) -> str:
        try:
            model = _gemini_model("You translate earnings call English transcripts into concise Korean subtitles.")
            resp = model.generate_content(
                "다음 영어 IR 콘퍼런스콜 발화를 자연스러운 한국어 자막으로 번역하세요. "
                "불필요한 설명 없이 번역문만 출력하세요.\n\n" + text[:6000]
            )
            return (resp.text or "").strip()
        except Exception as e:
            self._last_error = f"번역 실패: {e}"
            _log(self._last_error)
            return text

    def _summarize_ir(self, text: str) -> dict:
        try:
            import google.generativeai as genai
            model = _gemini_model(
                "당신은 기업 IR 콘퍼런스 콜 전문 애널리스트입니다. JSON만 반환하세요."
            )
            prompt = (
                "현재 진행 중인 기업 IR 콘퍼런스 콜 내용이야. "
                "가이드라인 변경 사항, 올해 예상 매출, 질의응답 중 핵심 리스크를 요약해줘. "
                "아래 JSON 구조로만 반환해줘: "
                "{\"guidance\":\"...\",\"revenue\":\"...\",\"risks\":\"...\",\"qa\":\"...\",\"summary\":\"...\"}\n\n"
                + text[-14000:]
            )
            resp = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.2, response_mime_type="application/json"),
            )
            parsed = _safe_json(resp.text or "")
            if parsed:
                return parsed
        except Exception as e:
            self._last_error = f"요약 실패: {e}"
            _log(self._last_error)
        return {
            "guidance": "요약 데이터 부족",
            "revenue": "확인 필요",
            "risks": "확인 필요",
            "qa": "확인 필요",
            "summary": text[-500:] if text else "아직 누적된 발화가 충분하지 않습니다.",
        }

    def _summary_worker(self):
        next_at = time.time() + self.summary_interval
        last_index = 0
        while self._running:
            time.sleep(1)
            if time.time() < next_at:
                continue
            with self._text_lock:
                chunks = self._translated_chunks[last_index:]
                last_index = len(self._translated_chunks)
            if chunks:
                merged = "\n".join(f"[{c['ts']}] {c['text']}" for c in chunks)
                summary = self._summarize_ir(merged)
                self._latest_summary = summary
                event = self._build_summary_event(summary, chunks)
                with self._lock:
                    self._event_q.append(event)
                _log("5분 요약 이벤트 생성")
            next_at = time.time() + self.summary_interval

    def _build_summary_event(self, summary: dict, chunks: list) -> dict:
        rows = [
            ["가이던스", summary.get("guidance", "")[:36]],
            ["예상매출", summary.get("revenue", "")[:36]],
            ["핵심리스크", summary.get("risks", "")[:36]],
            ["Q&A", summary.get("qa", "")[:36]],
            ["최근자막", chunks[-1]["text"][:36] if chunks else ""],
        ]
        briefing = (
            "선생님, 현재 진행 중인 콘퍼런스 콜의 새로운 질의응답 핵심 요약이 도착했습니다. "
            + (summary.get("summary") or summary.get("risks") or "")[:180]
        )
        return {
            "source": "ir_interpreter",
            "type": "ir_summary",
            "company": self.company,
            "message": briefing,
            "summary": summary,
            "overlay_table": {
                "type": "table",
                "title": f"IR 콘콜 실시간 핵심 요약 | {self.company}",
                "headers": ["항목", "요약"],
                "rows": rows,
            },
            "timestamp": datetime.now().isoformat(),
        }

    @property
    def status(self):
        with self._text_lock:
            chunks = len(self._translated_chunks)
        return {
            "running": self._running,
            "company": self.company,
            "url": self.url,
            "chunks": chunks,
            "pending": len(self._event_q),
            "summary_interval": f"{self.summary_interval}초",
            "last_error": self._last_error,
            "has_summary": self._latest_summary is not None,
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LLM tools
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

IR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "start_ir_interpreter",
            "description": "유튜브 라이브/기업 IR 오디오 스트림 URL을 실시간 Whisper STT+Gemini 한국어 요약으로 감시합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "유튜브 라이브 또는 IR 오디오/비디오 스트림 URL"},
                    "company": {"type": "string", "description": "기업명 또는 콜 이름"},
                    "summary_interval_sec": {"type": "integer", "description": "요약 주기(초). 기본 300"},
                    "chunk_seconds": {"type": "integer", "description": "Whisper 전송 청크 길이(초). 기본 30"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stop_ir_interpreter",
            "description": "현재 실행 중인 IR 실시간 통역을 중지합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_ir_interpreter_status",
            "description": "IR 실시간 통역 상태와 최근 오류를 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_interpreter_instance = None


def set_ir_interpreter_instance(inst):
    global _interpreter_instance
    _interpreter_instance = inst


def execute_ir_tool(tool_name, arguments):
    global _interpreter_instance
    if not _interpreter_instance:
        return {"success": False, "message": "IR 통역 인스턴스 없음"}
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    except Exception:
        arguments = {}

    if tool_name == "start_ir_interpreter":
        def _int_or(value, default):
            try:
                value = int(value)
                return value if value > 0 else default
            except Exception:
                return default
        return _interpreter_instance.start(
            url=(arguments.get("url") or "").strip(),
            company=(arguments.get("company") or "").strip(),
            summary_interval=_int_or(arguments.get("summary_interval_sec"), IR_SUMMARY_INTERVAL),
            chunk_seconds=_int_or(arguments.get("chunk_seconds"), IR_CHUNK_SECONDS),
        )
    if tool_name == "stop_ir_interpreter":
        return _interpreter_instance.stop()
    if tool_name == "get_ir_interpreter_status":
        return {"success": True, **_interpreter_instance.status}
    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}

