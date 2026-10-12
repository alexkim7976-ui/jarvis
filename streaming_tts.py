"""
J.A.R.V.I.S — 스트리밍 TTS 파이프라인
──────────────────────────────────────────
LLM 스트리밍 응답을 자연스러운 길이의 구간으로 묶어
macOS say / Fish Audio에 보내고 재생합니다.

흐름:
  LLM 스트리밍 → 문장 분리기 → TTS 큐 → 합성 워커 → 재생 워커
                                  ↓
                            첫 문장 도착 1~2초 이내 재생 시작
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME

import os
import re
import sys
import time
import queue
import shutil
import threading
import subprocess
from pathlib import Path
from datetime import datetime

import requests
try:
    import net  # 연결 재사용·재시도·적응형 타임아웃 공용 세션
except Exception:
    net = None

try:
    from free_mode import free_only_enabled, use_paid_tts
    from local_tts import synthesize_macos
except ImportError:
    def free_only_enabled():  # type: ignore
        return False
    def use_paid_tts():  # type: ignore
        return True
    synthesize_macos = None  # type: ignore

FISH_API_KEY  = os.getenv("FISH_AUDIO_API_KEY", "")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "")
AUDIO_DIR     = Path(os.getenv("AUDIO_SAVE_DIR", "audio_cache"))

# 한국어 문장 종결 패턴 (짧은 조각 flush 방지 — 긴 어미 우선)
SENTENCE_ENDERS = (
    "습니다.", "됩니다.", "입니다.", "합니다.", "이에요.", "예요.", "거예요.",
    "겠습니다.", "드릴까요?", "세요.", "죠.", "까?", "니?", "나?",
    "거야.", "겠어.", "줄게.", "봤어.", "했어.", "좋겠어.", "같아.",
    "는데.", "거든.", "잖아.", "할게.", "볼게.", "있어.", "없어.", "됐어.",
    "이야.", "어.", "지.", "네.", "야.",
    ".", "!", "?", "。", "！", "？",
)


def _tts_one_at_a_time() -> bool:
    """True면 문장 하나씩 순차 재생 (병합·대기 없음)."""
    return os.getenv("JARVIS_TTS_ONE_AT_A_TIME", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def _tts_min_chars() -> int:
    if _tts_one_at_a_time():
        return max(8, int(os.getenv("JARVIS_TTS_MIN_CHARS", "18")))
    return max(24, int(os.getenv("JARVIS_TTS_MIN_CHARS", "110")))


def _tts_max_chars() -> int:
    return max(_tts_min_chars() + 20, int(os.getenv("JARVIS_TTS_MAX_CHARS", "380")))


def _tts_max_wait_sec() -> float:
    if _tts_one_at_a_time():
        return max(0.05, float(os.getenv("JARVIS_TTS_MAX_WAIT_SEC", "0.12")))
    return max(0.8, float(os.getenv("JARVIS_TTS_MAX_WAIT_SEC", "2.8")))


def _tts_synth_batch_wait() -> float:
    """합성 직전 짧은 대기 — 연속 조각을 한 번에 말하기."""
    if _tts_one_at_a_time():
        return 0.0
    return max(0.0, float(os.getenv("JARVIS_TTS_SYNTH_BATCH_WAIT", "0.35")))


def _is_sentence_end(text: str) -> bool:
    """텍스트가 문장 끝인지 판단"""
    text = text.rstrip()
    if not text:
        return False
    if text[-1] in ".!?。！？":
        return True
    for ender in SENTENCE_ENDERS:
        if text.endswith(ender):
            return True
    if text.endswith("\n"):
        return True
    return False


def _last_sentence_end_pos(text: str) -> int:
    """버퍼에서 마지막 문장 종결 위치( inclusive end index )"""
    best = -1
    for i in range(len(text)):
        if _is_sentence_end(text[: i + 1]):
            best = i + 1
    return best


def _find_soft_break(text: str, limit: int | None = None) -> int:
    """쉼표·접속 등 자연스러운 끊김 위치 (없으면 공백)."""
    chunk = text if limit is None else text[:limit]
    for sep in ("…", "...", " — ", " - ", "，", ",", "、", ";", ":", "·"):
        pos = chunk.rfind(sep)
        if pos > 0:
            return pos + len(sep)
    pos = chunk.rfind(" ")
    return pos + 1 if pos > 0 else -1


def _normalize_speech_text(text: str) -> str:
    """TTS용 — 불필요한 줄바꿈·공백 정리 + 부드러운 존댓말(해요체) 다듬기."""
    t = re.sub(r"\s+", " ", (text or "").replace("\n", " ")).strip()
    t = re.sub(r"([.!?])\s*([.!?])+", r"\1", t)
    try:
        from speech_style import polish_for_voice
        t = polish_for_voice(t)
    except Exception:
        pass
    return t


def _split_voice_sentences(text: str) -> list[str]:
    """한 덩어리에 여러 문장이 있으면 문장 단위로 분리."""
    text = (text or "").strip()
    if not text:
        return []
    if not _tts_one_at_a_time():
        return [text]
    parts: list[str] = []
    buf = ""
    for ch in text:
        buf += ch
        if _is_sentence_end(buf):
            piece = buf.strip()
            if piece:
                parts.append(piece)
            buf = ""
    if buf.strip():
        parts.append(buf.strip())
    return parts or [text]


def _extract_flush_chunk(buffer: str, *, force: bool = False) -> tuple[str, str] | None:
    """버퍼에서 자연스럽게 말할 한 덩어리 추출."""
    buf = buffer.strip()
    if not buf:
        return None

    min_c = _tts_min_chars()
    max_c = _tts_max_chars()

    if force:
        if len(buf) <= max_c:
            return _normalize_speech_text(buf), ""
        pos = _last_sentence_end_pos(buf[:max_c])
        if pos <= 0:
            pos = _find_soft_break(buf, max_c)
        if pos <= 0:
            pos = max_c
        return _normalize_speech_text(buf[:pos]), buf[pos:].lstrip()

    if len(buf) >= max_c:
        pos = _last_sentence_end_pos(buf[:max_c])
        if pos <= 0:
            pos = _find_soft_break(buf, max_c)
        if pos <= 0:
            pos = max_c
        return _normalize_speech_text(buf[:pos]), buf[pos:].lstrip()

    sent_pos = _last_sentence_end_pos(buf)
    if sent_pos > 0 and (_tts_one_at_a_time() or sent_pos >= min_c):
        return _normalize_speech_text(buf[:sent_pos]), buf[sent_pos:].lstrip()

    return None


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 🔊 [스트리밍] {msg}")


class StreamingTTSPipeline:
    """
    실시간 문장 단위 TTS 파이프라인

    사용법:
        pipeline = StreamingTTSPipeline()
        pipeline.start()

        # LLM 스트리밍 중:
        pipeline.feed("안녕하세요, ")
        pipeline.feed(f"{USER_DISPLAY_NAME}. ")       # → f"안녕하세요, {USER_DISPLAY_NAME}." 즉시 TTS
        pipeline.feed("오늘 날씨는 ")
        pipeline.feed("맑습니다.")      # → "오늘 날씨는 맑습니다." TTS
        pipeline.finish()               # 남은 텍스트 처리

        full_text = pipeline.get_full_text()
        pipeline.wait_done()
    """

    def __init__(self):
        self._sentence_q = queue.Queue()   # 완성된 문장 큐
        self._audio_q = queue.Queue()      # 합성된 오디오 파일 큐
        self._buffer = ""                  # 텍스트 버퍼
        self._full_text = ""               # 전체 텍스트 (히스토리용)
        self._synth_thread = None
        self._play_thread = None
        self._running = False
        self._finished = False             # feed 완료
        self._play_done = threading.Event()
        self._interrupted = False
        self._lock = threading.Lock()

        # 통계
        self._first_sentence_time = 0
        self._start_time = 0
        self._sentences_sent = 0
        self._buffer_started_at = 0.0
        self._synth_sentinel = False
        self._speech_held = False
        self._player_ref = None

        # 재생 프로세스 (인터럽트용)
        self._current_process = None

    def set_player(self, player):
        """전역 음성 게이트 — 다른 재생 경로(player)와 겹치지 않게."""
        self._player_ref = player

    def start(self, *, player=None):
        """파이프라인 시작 — 전역 음성 게이트 확보 후 1문장씩 순차 재생."""
        from speech_gate import begin_speech, end_speech, wait_audio_idle

        player = player if player is not None else self._player_ref
        if not wait_audio_idle(tts_pipeline=self, player=player, timeout=180.0):
            _log("⚠ 다른 음성 재생 대기 초과 — 스트리밍 TTS 스킵")
            self._interrupted = True
            return False
        if not begin_speech(timeout=180.0):
            _log("⚠ 음성 게이트 확보 실패 — 스트리밍 TTS 스킵")
            self._interrupted = True
            return False
        self._speech_held = True

        self._running = True
        self._finished = False
        self._interrupted = False
        self._buffer = ""
        self._full_text = ""
        self._sentences_sent = 0
        self._start_time = time.time()
        self._first_sentence_time = 0
        self._buffer_started_at = 0.0
        self._synth_sentinel = False
        self._play_done.clear()

        # 큐 비우기
        while not self._sentence_q.empty():
            try: self._sentence_q.get_nowait()
            except: break
        while not self._audio_q.empty():
            try: self._audio_q.get_nowait()
            except: break

        # 합성 워커 시작
        self._synth_thread = threading.Thread(target=self._synth_worker, daemon=True)
        self._synth_thread.start()

        # 재생 워커 시작
        self._play_thread = threading.Thread(target=self._play_worker, daemon=True)
        self._play_thread.start()
        return True

    def _release_speech_gate(self):
        if getattr(self, "_speech_held", False):
            try:
                from speech_gate import end_speech
                end_speech()
            except Exception:
                pass
            self._speech_held = False

    def feed(self, text_chunk):
        """LLM에서 텍스트 청크를 받아 버퍼에 추가"""
        if not text_chunk or self._interrupted:
            return

        with self._lock:
            self._buffer += text_chunk
            self._full_text += text_chunk

        # 문장 완성 여부 확인
        self._try_flush()

    def finish(self):
        """LLM 스트리밍 완료 — 남은 버퍼 처리"""
        with self._lock:
            while self._buffer.strip():
                ext = _extract_flush_chunk(self._buffer, force=True)
                if not ext:
                    chunk = _normalize_speech_text(self._buffer)
                    self._buffer = ""
                    if chunk:
                        self._enqueue_chunk(chunk)
                    break
                chunk, self._buffer = ext
                if chunk:
                    self._enqueue_chunk(chunk)
            self._buffer = ""
            self._buffer_started_at = 0.0

        self._finished = True
        self._sentence_q.put(None)  # 종료 신호

    def stop(self):
        """즉시 중단 (인터럽트)"""
        self._interrupted = True
        self._running = False
        self._finished = True

        # 재생 중단
        if self._current_process and self._current_process.poll() is None:
            try:
                self._current_process.terminate()
                self._current_process.wait(timeout=1)
            except:
                try: self._current_process.kill()
                except: pass

        # 큐에 종료 신호
        self._sentence_q.put(None)
        self._audio_q.put(None)
        self._play_done.set()
        self._release_speech_gate()

    def wait_done(self, timeout=30):
        """재생 완료 대기"""
        self._play_done.wait(timeout=timeout)

    def get_full_text(self):
        """전체 답변 텍스트 반환"""
        return self._full_text.strip()

    @property
    def is_playing(self):
        return self._running and not self._play_done.is_set()

    @property
    def latency(self):
        """첫 문장 재생까지 걸린 시간"""
        if self._first_sentence_time > 0:
            return round(self._first_sentence_time - self._start_time, 2)
        return 0

    def _enqueue_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        for piece in _split_voice_sentences(chunk):
            self._enqueue_single(piece)

    def _enqueue_single(self, chunk: str) -> None:
        if not chunk:
            return
        if self._first_sentence_time == 0:
            self._first_sentence_time = time.time()
            latency = self._first_sentence_time - self._start_time
            _log(f"발화 ({latency:.1f}초, {len(chunk)}자): {chunk[:40]}...")
        self._sentence_q.put(chunk)
        self._sentences_sent += 1

    def _try_flush(self):
        """버퍼에서 자연스러운 길이의 구간을 큐로 보냄 (짧은 문장 연속 flush 방지)."""
        with self._lock:
            if self._buffer and self._buffer_started_at == 0:
                self._buffer_started_at = time.time()

            while self._buffer.strip():
                chunk = None
                buf = self._buffer

                # 스트리밍이 잠깐 멈춘 경우 — (한 문장씩 모드가 아닐 때만) 묶어서 말하기
                if not _tts_one_at_a_time() and self._buffer_started_at > 0:
                    elapsed = time.time() - self._buffer_started_at
                    if elapsed >= _tts_max_wait_sec() and len(buf.strip()) >= 48:
                        pos = _last_sentence_end_pos(buf)
                        if pos <= 0 or pos < 48:
                            pos = _find_soft_break(buf)
                        if pos > 0:
                            chunk = _normalize_speech_text(buf[:pos])
                            self._buffer = buf[pos:].lstrip()

                if chunk is None:
                    ext = _extract_flush_chunk(self._buffer)
                    if not ext:
                        break
                    chunk, self._buffer = ext

                if chunk:
                    self._enqueue_chunk(chunk)

            if not self._buffer.strip():
                self._buffer_started_at = 0.0

    def _collect_synth_batch(self, first: str) -> str:
        """합성 직전 — 한 문장씩 모드면 병합하지 않음."""
        if _tts_one_at_a_time():
            return _normalize_speech_text(first)
        parts = [first]
        wait = _tts_synth_batch_wait()
        deadline = time.time() + wait if wait > 0 else time.time()
        max_c = _tts_max_chars()

        while len(" ".join(parts)) < max_c:
            if wait <= 0:
                break
            if time.time() >= deadline:
                try:
                    nxt = self._sentence_q.get_nowait()
                except queue.Empty:
                    break
            else:
                try:
                    nxt = self._sentence_q.get(timeout=min(0.08, deadline - time.time()))
                except queue.Empty:
                    continue

            if nxt is None:
                self._synth_sentinel = True
                break
            parts.append(nxt)
            deadline = time.time() + wait * 0.5  # 추가 조각이 오면 조금 더 기다림

        return _normalize_speech_text(" ".join(p for p in parts if p))

    def _synth_worker(self):
        """문장 큐 → TTS 합성 → 오디오 큐 (조각 병합 후 한 번에 합성)."""
        seq = 0
        self._synth_sentinel = False
        while self._running:
            try:
                sentence = self._sentence_q.get(timeout=0.5)
            except queue.Empty:
                if self._finished:
                    break
                continue

            if sentence is None:
                break

            if self._interrupted:
                break

            batch = self._collect_synth_batch(sentence)
            if not batch:
                if self._synth_sentinel:
                    break
                continue

            audio_path = self._synthesize(batch, seq)
            seq += 1

            if audio_path and not self._interrupted:
                self._audio_q.put(audio_path)

            if self._synth_sentinel:
                break

        self._audio_q.put(None)  # 재생 워커에 종료 신호

    def _play_worker(self):
        """오디오 큐 → 순차 재생.
        종료 조건: (1) audio_q에서 None sentinel 수신, (2) 인터럽트, (3) synth_worker 사망,
        (4) synth는 살아있지만 30초 무진행(=Fish API hang 등) — wake.resume() 복구 우선.
        주의: 과거엔 `_finished` 플래그만 보고 일찍 빠져나가 재생을 건너뛰는 버그가 있었고,
        반대로 sentinel만 기다리면 synth hang 시 `_play_done`이 영영 set 안 돼 wake가 영구 pause된다."""
        last_progress = time.time()
        while self._running:
            try:
                audio_path = self._audio_q.get(timeout=0.5)
            except queue.Empty:
                if self._interrupted: break
                if not (self._synth_thread and self._synth_thread.is_alive()):
                    break  # synth 끝났고 큐도 비었으면 더 올 게 없음
                if time.time() - last_progress > 30:
                    _log("⚠ synth 30초 무진행 — play_worker 강제 종료 (wake 복구 우선)")
                    break
                continue

            last_progress = time.time()   # 큐에서 뭔가 꺼냄 = 진행 중
            if audio_path is None: break
            if self._interrupted: break
            self._play_audio(audio_path)

        self._play_done.set()
        total = time.time() - self._start_time
        _log(f"재생 완료 (문장={self._sentences_sent}, 총={total:.1f}초)")
        self._release_speech_gate()

    def _synthesize(self, text, seq):
        """단일 문장 합성 — 무료 모드: macOS say / 유료: Fish Audio"""
        if free_only_enabled() or not use_paid_tts():
            if synthesize_macos:
                path = synthesize_macos(text, seq=seq, audio_dir=AUDIO_DIR)
                if path:
                    return path
            _log("로컬 TTS(say) 실패 — 음성 합성 건너뜀")
            return None

        # 키는 호출 시점에 읽는다 — jarvis_v2가 이 모듈을 import한 뒤 load_dotenv()를 부르므로,
        # import 시점 상수(FISH_API_KEY)는 비어 있을 수 있다. (음성 묵음 0.0초 버그 방지)
        fish_key = os.getenv("FISH_AUDIO_API_KEY", "") or FISH_API_KEY
        fish_voice = os.getenv("FISH_VOICE_ID", "") or FISH_VOICE_ID
        if not fish_key or not fish_voice:
            if synthesize_macos:
                path = synthesize_macos(text, seq=seq, audio_dir=AUDIO_DIR)
                if path:
                    return path
            _log("Fish Audio 키 없음 — 음성 합성 건너뜀 (.env FISH_AUDIO_API_KEY/FISH_VOICE_ID 확인)")
            return None

        try:
            _http = net if net is not None else requests
            resp = _http.post(
                "https://api.fish.audio/v1/tts",
                headers={
                    "Authorization": f"Bearer {fish_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "reference_id": fish_voice,
                    "format": "mp3",
                    "mp3_bitrate": 128,   # 스트리밍이므로 약간 낮춰서 빠르게
                    "normalize": True,
                    "latency": "balanced",  # 느린 회선에서 첫 바이트 더 빨리
                },
                timeout=(5, 20),
            )

            if resp.status_code != 200:
                _log(f"TTS 오류 {resp.status_code}")
                return None

            out_path = str(AUDIO_DIR / f"stream_{int(time.time())}_{seq}.mp3")
            with open(out_path, "wb") as f:
                f.write(resp.content)

            return out_path

        except Exception as e:
            _log(f"합성 오류: {e}")
            return None

    def _play_audio(self, filepath):
        """오디오 파일 재생 (블로킹)"""
        try:
            if sys.platform == "darwin":
                self._current_process = subprocess.Popen(
                    ["afplay", filepath],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif sys.platform == "win32":
                for player in ["ffplay", "ffplay.exe"]:
                    if shutil.which(player):
                        self._current_process = subprocess.Popen(
                            [player, "-nodisp", "-autoexit", "-loglevel", "quiet", filepath],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        break
            elif sys.platform.startswith("linux"):
                for player in ["mpg123", "aplay", "ffplay"]:
                    if shutil.which(player):
                        args = [player]
                        if player == "ffplay":
                            args += ["-nodisp", "-autoexit"]
                        args.append(filepath)
                        self._current_process = subprocess.Popen(
                            args,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                        )
                        break

            if self._current_process:
                deadline = time.time() + float(os.getenv("JARVIS_PLAY_TIMEOUT", "180"))
                while self._current_process.poll() is None:
                    if time.time() >= deadline:
                        try:
                            self._current_process.terminate()
                            self._current_process.wait(timeout=1)
                        except Exception:
                            try:
                                self._current_process.kill()
                            except Exception:
                                pass
                        break
                    time.sleep(0.05)
                self._current_process = None

        except Exception as e:
            _log(f"재생 오류: {e}")

        # 임시 파일 정리
        try:
            Path(filepath).unlink()
        except:
            pass
