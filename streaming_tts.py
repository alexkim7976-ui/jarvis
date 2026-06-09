"""
J.A.R.V.I.S — 스트리밍 TTS 파이프라인
──────────────────────────────────────────
LLM 스트리밍 응답을 문장 단위로 쪼개어
Fish Audio에 실시간으로 보내고 즉시 재생합니다.

흐름:
  LLM 스트리밍 → 문장 분리기 → TTS 큐 → 합성 워커 → 재생 워커
                                  ↓
                            첫 문장 도착 1~2초 이내 재생 시작
"""

from __future__ import annotations

import os
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

FISH_API_KEY  = os.getenv("FISH_AUDIO_API_KEY", "")
FISH_VOICE_ID = os.getenv("FISH_VOICE_ID", "")
AUDIO_DIR     = Path(os.getenv("AUDIO_SAVE_DIR", "audio_cache"))

# 한국어 문장 종결 패턴
SENTENCE_ENDERS = {
    ".", "!", "?", "。", "！", "？",
    "다.", "요.", "죠.", "까?", "니?", "나?",
    "다!", "요!", "죠!",
    "습니다.", "됩니다.", "입니다.", "합니다.",
    "세요.", "드릴까요?", "겠습니다.",
}

def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 🔊 [스트리밍] {msg}")


def _is_sentence_end(text):
    """텍스트가 문장 끝인지 판단"""
    text = text.rstrip()
    if not text:
        return False
    # 명확한 문장 부호
    if text[-1] in ".!?。！？":
        return True
    # 한국어 종결 어미
    for ender in SENTENCE_ENDERS:
        if text.endswith(ender):
            return True
    # 줄바꿈
    if text.endswith("\n"):
        return True
    return False


class StreamingTTSPipeline:
    """
    실시간 문장 단위 TTS 파이프라인

    사용법:
        pipeline = StreamingTTSPipeline()
        pipeline.start()

        # LLM 스트리밍 중:
        pipeline.feed("안녕하세요, ")
        pipeline.feed("선생님. ")       # → "안녕하세요, 선생님." 즉시 TTS
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

        # 재생 프로세스 (인터럽트용)
        self._current_process = None

    def start(self):
        """파이프라인 시작"""
        self._running = True
        self._finished = False
        self._interrupted = False
        self._buffer = ""
        self._full_text = ""
        self._sentences_sent = 0
        self._start_time = time.time()
        self._first_sentence_time = 0
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
            remaining = self._buffer.strip()
            self._buffer = ""

        if remaining:
            self._sentence_q.put(remaining)
            self._sentences_sent += 1

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

    def _try_flush(self):
        """버퍼에서 완성된 문장을 큐로 보냄"""
        with self._lock:
            if not self._buffer:
                return

            # 문장 종결 위치 찾기
            best_pos = -1
            for i in range(len(self._buffer)):
                if _is_sentence_end(self._buffer[:i+1]):
                    best_pos = i + 1

            if best_pos > 0:
                sentence = self._buffer[:best_pos].strip()
                self._buffer = self._buffer[best_pos:]

                if sentence:
                    if self._first_sentence_time == 0:
                        self._first_sentence_time = time.time()
                        latency = self._first_sentence_time - self._start_time
                        _log(f"첫 문장 감지 ({latency:.1f}초): {sentence[:30]}...")

                    self._sentence_q.put(sentence)
                    self._sentences_sent += 1

    def _synth_worker(self):
        """문장 큐 → Fish Audio TTS → 오디오 큐"""
        seq = 0
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

            # Fish Audio TTS 합성
            audio_path = self._synthesize(sentence, seq)
            seq += 1

            if audio_path and not self._interrupted:
                self._audio_q.put(audio_path)

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

    def _synthesize(self, text, seq):
        """Fish Audio로 단일 문장 합성"""
        # 키는 호출 시점에 읽는다 — jarvis_v2가 이 모듈을 import한 뒤 load_dotenv()를 부르므로,
        # import 시점 상수(FISH_API_KEY)는 비어 있을 수 있다. (음성 묵음 0.0초 버그 방지)
        fish_key = os.getenv("FISH_AUDIO_API_KEY", "") or FISH_API_KEY
        fish_voice = os.getenv("FISH_VOICE_ID", "") or FISH_VOICE_ID
        if not fish_key or not fish_voice:
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
