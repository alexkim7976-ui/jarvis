from __future__ import annotations

import os, sys, time, wave, struct, threading, tempfile, collections
from pathlib import Path
from datetime import datetime
import pyaudio

from voice_profile import VoiceProfile

WAKE_WORD_ENABLED  = os.getenv("WAKE_WORD_ENABLED","true").lower()=="true"
WAKE_WORDS_STR     = os.getenv(
    "WAKE_WORDS",
    "자비스야,자비스,jarvis,hey jarvis,쟈비스,자바스,저비스",
)
WAKE_WORDS         = [w.strip().lower() for w in WAKE_WORDS_STR.split(",")]
WAKE_THRESHOLD     = int(os.getenv("WAKE_WORD_THRESHOLD","600"))
WAKE_COOLDOWN      = float(os.getenv("WAKE_WORD_COOLDOWN","3.0"))
WAKE_CLIP_DURATION = 1.2    # 클립 최대 길이(초) — 보통 침묵 감지로 더 일찍 종료됨
WAKE_SILENCE_CHUNKS = 2     # 연속 침묵 청크 수(≈130ms) 도달 시 클립 조기 종료 → 반응 단축
SAMPLE_RATE=16_000; CHANNELS=1; CHUNK=1_024; FORMAT=pyaudio.paInt16

def _log(level, msg):
    ts=datetime.now().strftime("%H:%M:%S")
    icons={"INFO":"◈","OK":"✓","WARN":"⚠","WAKE":"🔔","AUTH":"🔒","DENY":"🚫"}
    print(f"[{ts}] {icons.get(level,'·')} [웨이크] {msg}")

class WakeWordDetector:
    def __init__(self, openai_client):
        self._client=openai_client
        self._pa=None; self._stream=None; self._thread=None; self._running=False
        self._triggered=False; self._last_trigger=0; self._lock=threading.Lock()
        self._stream_lock=threading.RLock()  # read()와 close()/open() 직렬화 → PortAudio ring buffer use-after-free 방지
        self._check_count=0; self._detect_count=0
        self._pre_buf=collections.deque(maxlen=8)  # ~500ms 사전 버퍼 (단어 앞부분 보존)
        self.listening=False; self.last_heard=""; self.status="OFF"
        self.enabled=WAKE_WORD_ENABLED
        self.voice_profile=VoiceProfile()
        self._local_detections=0; self._api_detections=0; self._rejections=0
        self._oww_detections=0
        self.last_rejection=""  # 마지막 거부 사유
        self._stt_busy=False  # STT 추론 중 중복 클립 방지

    @property
    def triggered(self):
        with self._lock: return self._triggered

    def reset(self):
        with self._lock: self._triggered=False; self.status="LISTENING"

    def start(self, pa):
        if not self.enabled: return
        self._pa=pa; self._running=True
        self._thread=threading.Thread(target=self._listen_loop,daemon=True)
        self._thread.start()
        # 저사양+API STT 모드면 로컬 Whisper 프리로드 생략 (RAM/CPU 절감)
        try:
            from stt_backend import should_preload_local_whisper
            if should_preload_local_whisper():
                threading.Thread(target=self._ensure_model, daemon=True).start()
        except ImportError:
            threading.Thread(target=self._ensure_model, daemon=True).start()
        self.status="LISTENING"; self.listening=True
        if self.voice_profile.trained:
            try:
                from voice_profile import is_known_device
                _tag = " (등록 기기)" if is_known_device() else ""
            except ImportError:
                _tag = ""
            _log("AUTH", f"음성 인증 활성 — 등록된 주인만 웨이크 가능 "
                         f"({self.voice_profile._sample_count}샘플){_tag}")
        else:
            try:
                from voice_profile import is_known_device, voice_setup_required
                if is_known_device() and not voice_setup_required(self.voice_profile):
                    _log("AUTH", f"등록 기기 — 저장 프로필 적용 ({self.voice_profile._sample_count}샘플)")
                else:
                    _log("WARN", "음성 프로필 미학습 — 누구나 웨이크 가능 (V키로 학습)")
            except ImportError:
                _log("WARN", "음성 프로필 미학습 — 누구나 웨이크 가능 (V키로 학습)")

    def stop(self):
        self._running=False; self.listening=False; self.status="OFF"
        if self._thread: self._thread.join(timeout=3)
        self._release_stream()

    def _release_stream(self):
        """CoreAudio 입력 장치 점유 해제 — 녹음/TTS와 동시 오픈 시 macOS UI 프리징 방지.
        _stream_lock으로 in-flight read()가 끝날 때까지 대기 후 close → ring buffer use-after-free(SIGSEGV) 방지."""
        with self._stream_lock:
            if self._stream:
                try:
                    self._stream.stop_stream()
                    self._stream.close()
                except Exception:
                    pass
                self._stream = None

    def pause(self):
        self.listening = False
        self.status = "PAUSED"
        # macOS: listening=False만으로는 스트림이 장치를 붙잡음 → 녹음기와 충돌
        if sys.platform == "darwin":
            self._release_stream()

    def resume(self):
        if self.enabled and self._running:
            self.listening = True
            self.status = "LISTENING"
            # 스트림은 _listen_loop가 listening=True일 때 _open_stream()으로 복구

    def _open_stream(self):
        """마이크 스트림 오픈(재오픈 포함). 장시간 실행 중 CoreAudio가 스트림을 끊으면 복구용으로 재호출된다."""
        with self._stream_lock:
            if self._stream:
                try: self._stream.stop_stream(); self._stream.close()
                except: pass
                self._stream=None
            try:
                self._stream=self._pa.open(format=FORMAT,channels=CHANNELS,
                    rate=SAMPLE_RATE,input=True,frames_per_buffer=CHUNK)
                return True
            except Exception as e:
                _log("WARN",f"마이크 스트림 오픈 실패: {e}"); return False

    def _read_chunk(self):
        """스트림 read를 _stream_lock 안에서 수행 → close()/open()과 직렬화.
        스트림이 닫혔으면 None 반환(호출부에서 재오픈/탈출 처리)."""
        with self._stream_lock:
            s=self._stream
            if s is None: return None
            return s.read(CHUNK,exception_on_overflow=False)

    def _listen_loop(self):
        if not self._open_stream():
            self.status="ERROR"; return
        fail=0
        while self._running:
            if not self.listening:
                time.sleep(0.1)
                continue
            if self._stream is None:
                if not self._open_stream():
                    time.sleep(0.5)
                    continue
            try:
                data=self._read_chunk()
                if data is None:  # pause 등으로 스트림이 닫힘 → 다음 루프에서 재오픈 판단
                    time.sleep(0.05); continue
                fail=0  # 정상 읽기 → 실패 카운터 리셋
                rms=self._calc_rms(data)
                self._pre_buf.append(data)  # 항상 버퍼링 (단어 앞부분 보존용)
                if rms<WAKE_THRESHOLD: continue
                now=time.time()
                if now-self._last_trigger<WAKE_COOLDOWN: continue

                self.status="CHECKING"
                frames=list(self._pre_buf); silence=0; t0=time.time()  # 사전 버퍼 포함
                while (time.time()-t0)<WAKE_CLIP_DURATION:
                    try:
                        c=self._read_chunk()
                        if c is None: break  # 스트림 닫힘 → 클립 캡처 중단
                        frames.append(c)
                        if self._calc_rms(c)<WAKE_THRESHOLD: silence+=1
                        else: silence=0
                        if silence>=WAKE_SILENCE_CHUNKS: break
                    except: break

                audio_data=b"".join(frames)

                # STT는 별도 스레드에서 실행 — 메인 웨이크 루프(마이크 read)를 블로킹하지 않음
                if self._stt_busy:
                    self.status="LISTENING"
                    continue
                self._stt_busy=True
                threading.Thread(
                    target=self._process_wake_clip,
                    args=(list(frames),),
                    daemon=True,
                    name="wake-stt",
                ).start()

            except Exception as e:
                fail+=1
                if fail==1 or fail%50==0: _log("WARN",f"마이크 읽기 실패({fail}): {e}")
                if fail>=5:
                    _log("WARN","마이크 스트림 재시작 시도...")
                    if self._open_stream(): _log("OK","마이크 스트림 복구 완료"); fail=0
                    else: time.sleep(2)
                else: time.sleep(0.1)

        self._release_stream()

    def _process_wake_clip(self, frames):
        """Whisper STT + 음성 프로필 검증 — 웨이크 listen 루프와 분리."""
        try:
            audio_data = b"".join(frames)
            vp_match, vp_conf = False, 0.0
            if self.voice_profile.trained:
                vp_match, vp_conf = self.voice_profile.match(audio_data)
                if not vp_match:
                    _log("INFO", f"음성 프로필 불일치 (유사도={vp_conf})")

            wav_path = self._save_temp_wav(frames)
            detected = False
            api_ok = True
            wake_source = ""
            if wav_path:
                detected, api_ok, wake_source = self._check_wake(audio_data, wav_path)
                self._check_count += 1
                try:
                    Path(wav_path).unlink()
                except Exception:
                    pass

            if not detected and not api_ok and self.voice_profile.trained and vp_match:
                _log("INFO", f"Whisper API 실패 → 음성 프로필 폴백 (유사도={vp_conf})")
                detected = True

            if detected:
                _strict_auth = False
                try:
                    from security_policy import voice_auth_required
                    _strict_auth = voice_auth_required()
                except ImportError:
                    pass
                if _strict_auth:
                    if not self.voice_profile.trained:
                        self._rejections += 1
                        self.last_rejection = "음성 프로필 미학습"
                        _log("DENY", "최대 보안 — V키로 음성 등록 후 웨이크 가능")
                        self.status = "LISTENING"
                        return
                    if not vp_match:
                        self._rejections += 1
                        self.last_rejection = f"음성 불일치 (유사도={vp_conf:.2f})"
                        _log("DENY", f"등록된 주인 목소리 아님 (유사도={vp_conf:.2f})")
                        self.status = "LISTENING"
                        return
                with self._lock:
                    self._triggered = True
                    self._last_trigger = time.time()
                    self._detect_count += 1
                    if self.voice_profile.trained:
                        self._local_detections += 1
                    else:
                        self._api_detections += 1
                self.status = "DETECTED"
                if wake_source == "openwakeword":
                    self._oww_detections += 1
                mode = (
                    "openWakeWord" if wake_source == "openwakeword"
                    else ("프로필 폴백" if (not api_ok and vp_match)
                    else ("학습" if self.voice_profile.trained else "미학습"))
                )
                _log("WAKE", f"감지 ({mode}, heard='{self.last_heard}')")
            else:
                self.status = "LISTENING"
        finally:
            self._stt_busy = False

    def _ensure_model(self):
        """faster-whisper 웨이크 모델 lazy 로드. whisper_pool로 본 STT와 동일 설정 시 인스턴스 공유(RAM 절감)."""
        if getattr(self,"_fw_model",None) is not None: return self._fw_model
        try:
            from whisper_pool import get_whisper_model
            _log("INFO","웨이크 Whisper 모델 로드 중...")
            self._fw_model=get_whisper_model(role="wake")
            _log("OK","웨이크 Whisper 모델 준비")
        except Exception as e:
            _log("WARN",f"Whisper 모델 로드 실패: {e}"); self._fw_model=None
        return self._fw_model

    def _check_wake(self, pcm_bytes, wav_path):
        """Returns (detected, api_ok, source). openWakeWord → Whisper fuzzy 순."""
        try:
            from wake_engine import detect_wake_from_pcm, oww_enabled, get_oww_frontend
            if oww_enabled():
                get_oww_frontend().load()
            oww_hit, src, detail = detect_wake_from_pcm(
                pcm_bytes, stt_text="", wake_words=WAKE_WORDS,
            )
            if oww_hit:
                self.last_heard = detail or "hey jarvis"
                return True, True, src
        except ImportError:
            pass

        try:
            from stt_backend import transcribe_wake_clip
            detected, api_ok, text = transcribe_wake_clip(wav_path, self._client, WAKE_WORDS)
            self.last_heard = text
            return detected, api_ok, "whisper" if detected else ""
        except ImportError:
            pass
        # 폴백: 기존 인라인 로직
        model=self._ensure_model()
        if model is not None:
            try:
                from wake_engine import matches_wake_word
                segments,_=model.transcribe(wav_path, language="ko",
                    beam_size=1, condition_on_previous_text=False)
                text=" ".join(seg.text.strip() for seg in segments).strip().lower()
                self.last_heard=text
                if not text: return False, True, ""
                if matches_wake_word(text, WAKE_WORDS):
                    return True, True, "whisper"
                return False, True, ""
            except Exception as e:
                _log("WARN", f"로컬 Whisper 실패: {e}")
        try:
            from wake_engine import matches_wake_word
            with open(wav_path,"rb") as f:
                r=self._client.audio.transcriptions.create(
                    model="whisper-1",file=f,response_format="text",language="ko")
            text=r.strip().lower(); self.last_heard=text
            if not text: return False, True, ""
            if matches_wake_word(text, WAKE_WORDS):
                return True, True, "whisper"
            return False, True, ""
        except Exception as e:
            _log("WARN",f"Whisper API 실패: {e}"); return False, False, ""

    def _save_temp_wav(self, frames):
        if not frames: return None
        try:
            import tempfile
            fd,path=tempfile.mkstemp(suffix=".wav")
            with wave.open(path,"wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(self._pa.get_sample_size(FORMAT))
                wf.setframerate(SAMPLE_RATE); wf.writeframes(b"".join(frames))
            os.close(fd); return path
        except: return None

    @staticmethod
    def _calc_rms(data):
        c=len(data)//2
        if c==0: return 0
        s=struct.unpack(f"{c}h",data)
        return int((sum(x*x for x in s)/c)**0.5)

    @property
    def stats(self):
        return {"enabled":self.enabled,"status":self.status,
                "total":self._detect_count,"local":self._local_detections,
                "api":self._api_detections,"oww":self._oww_detections,
                "rejections":self._rejections,
                "voice_trained":self.voice_profile.trained,
                "voice_samples":self.voice_profile._sample_count}
