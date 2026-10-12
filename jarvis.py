from __future__ import annotations
import os
import sys
import time
import wave
import queue
import shutil
import threading
import subprocess
from pathlib import Path
from datetime import datetime

import cv2
import mediapipe as mp
import numpy as np
import pyaudio
import requests
from openai import OpenAI
from dotenv import load_dotenv

from speech_style import USER_DISPLAY_NAME, USER_NAME_RULES

# ──────────────────────────────────────────────
#  설정 로드
# ──────────────────────────────────────────────
load_dotenv()

OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
FISH_API_KEY    = os.getenv("FISH_AUDIO_API_KEY", "")
FISH_VOICE_ID   = os.getenv("FISH_VOICE_ID", "")
LLM_PROVIDER    = os.getenv("LLM_PROVIDER", "gemini").lower()  # "openai" | "gemini"
GEMINI_API_KEY  = os.getenv("GEMINI_API_KEY", "")
GEMINI_FLASH_MODEL = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")  # 대화 경량 작업 → FLASH
AUDIO_DIR       = Path(os.getenv("AUDIO_SAVE_DIR", "audio_cache"))
AUDIO_DIR.mkdir(exist_ok=True)

# 오디오 설정
SAMPLE_RATE        = 16_000
CHANNELS           = 1
CHUNK              = 1_024
RECORD_FORMAT      = pyaudio.paInt16
MAX_RECORD_SECONDS = 15

# ──────────────────────────────────────────────
#  자비스 시스템 프롬프트
# ──────────────────────────────────────────────
JARVIS_SYSTEM_PROMPT = """You are J.A.R.V.I.S. (Just A Rather Very Intelligent System), 
the highly sophisticated AI assistant created for your user. 

Your personality traits:
- Speak with calm, measured intelligence and dry wit
- Address the user as "Sir" or "Ma'am" (detect from context, default to "Sir")
- Provide precise, concise answers — never ramble
- Occasionally reference data, statistics, or technical details naturally
- Show subtle concern for the user's wellbeing
- Use British-influenced phrasing: "Shall I...", "Might I suggest...", "Quite right..."
- Never break character; you are JARVIS, not an AI assistant

Response format:
- Keep responses under 3 sentences for conversational queries
- For complex tasks, list steps clearly but briefly
- Always end with an optional proactive offer when appropriate
- Respond in the SAME LANGUAGE the user speaks (Korean -> Korean, English -> English)

Korean mode: When speaking Korean, maintain the JARVIS persona with natural casual speech.
""" + USER_NAME_RULES + f"""Always address the user as "{USER_DISPLAY_NAME}". Never use "Tony", "토니", or "선생님"."""

# ──────────────────────────────────────────────
#  유틸리티
# ──────────────────────────────────────────────
def log(level: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "◈", "OK": "✓", "WARN": "⚠", "ERR": "✗", "JARVIS": "◆"}
    print(f"[{ts}] {icons.get(level,'·')} {msg}")


def play_audio_file(filepath: str):
    try:
        if sys.platform == "darwin":
            subprocess.run(["afplay", filepath], check=True)
        elif sys.platform.startswith("linux"):
            for player in ["mpg123", "aplay", "paplay", "ffplay"]:
                if shutil.which(player):
                    args = [player]
                    if player == "ffplay":
                        args += ["-nodisp", "-autoexit"]
                    args.append(filepath)
                    subprocess.run(args, check=True, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                    return
            log("WARN", "오디오 플레이어를 찾을 수 없습니다. 파일 저장 완료: " + filepath)
        elif sys.platform == "win32":
            import winsound
            winsound.PlaySound(filepath, winsound.SND_FILENAME)
    except Exception as e:
        log("WARN", f"재생 실패: {e}  →  파일: {filepath}")


# ──────────────────────────────────────────────
#  음성 녹음 (PyAudio)
# ──────────────────────────────────────────────
class AudioRecorder:
    def __init__(self):
        self.pa = pyaudio.PyAudio()
        self._frames = []
        self._recording = False
        self._thread = None

    def start(self):
        if self._recording:
            return
        self._frames = []
        self._recording = True
        self._thread = threading.Thread(target=self._record_loop, daemon=True)
        self._thread.start()
        log("INFO", "🎙  녹음 시작 — 손동작으로 종료하세요")

    def stop(self):
        if not self._recording:
            return None
        self._recording = False
        if self._thread:
            self._thread.join(timeout=3)
        return self._save_wav()

    def _record_loop(self):
        stream = self.pa.open(
            format=RECORD_FORMAT,
            channels=CHANNELS,
            rate=SAMPLE_RATE,
            input=True,
            frames_per_buffer=CHUNK,
        )
        start = time.time()
        while self._recording and (time.time() - start) < MAX_RECORD_SECONDS:
            self._frames.append(stream.read(CHUNK, exception_on_overflow=False))
        stream.stop_stream()
        stream.close()
        self._recording = False

    def _save_wav(self):
        if not self._frames:
            return None
        path = str(AUDIO_DIR / f"input_{int(time.time())}.wav")
        with wave.open(path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(self.pa.get_sample_size(RECORD_FORMAT))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(self._frames))
        log("OK", f"WAV 저장 완료: {path}")
        return path

    def __del__(self):
        try:
            self.pa.terminate()
        except Exception:
            pass


# ──────────────────────────────────────────────
#  Whisper STT
# ──────────────────────────────────────────────
def transcribe_audio(wav_path, client):
    log("INFO", "Whisper STT 처리 중...")
    with open(wav_path, "rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="text",
        )
    text = result.strip()
    log("OK", f"인식 결과: {text}")
    return text


# ──────────────────────────────────────────────
#  LLM 답변 생성 — OpenAI GPT
# ──────────────────────────────────────────────
def ask_jarvis_openai(user_text, client, history):
    messages = [{"role": "system", "content": JARVIS_SYSTEM_PROMPT}]
    messages.extend(history[-10:])
    messages.append({"role": "user", "content": user_text})

    response = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=0.7,
        max_tokens=300,
    )
    return response.choices[0].message.content.strip()


# ──────────────────────────────────────────────
#  LLM 답변 생성 — Google Gemini
# ──────────────────────────────────────────────
def ask_jarvis_gemini(user_text, history):
    import google.generativeai as genai

    genai.configure(api_key=GEMINI_API_KEY)

    model = genai.GenerativeModel(
        model_name=GEMINI_FLASH_MODEL,
        system_instruction=JARVIS_SYSTEM_PROMPT,
    )

    gemini_history = []
    for msg in history[-10:]:
        role = "user" if msg["role"] == "user" else "model"
        gemini_history.append({
            "role": role,
            "parts": [msg["content"]],
        })

    chat = model.start_chat(history=gemini_history)
    response = chat.send_message(user_text)
    return response.text.strip()


# ──────────────────────────────────────────────
#  LLM 라우터
# ──────────────────────────────────────────────
def ask_jarvis(user_text, openai_client, history):
    log("INFO", f"LLM({LLM_PROVIDER}) 처리 중...")

    if LLM_PROVIDER == "gemini":
        if not GEMINI_API_KEY:
            log("ERR", ".env 파일에 GEMINI_API_KEY가 없습니다.")
            return f"죄송합니다, {USER_DISPLAY_NAME}. Gemini API 키가 설정되지 않았습니다."
        answer = ask_jarvis_gemini(user_text, history)
    else:
        answer = ask_jarvis_openai(user_text, openai_client, history)

    log("JARVIS", f"답변: {answer}")
    return answer


# ──────────────────────────────────────────────
#  Fish Audio TTS
# ──────────────────────────────────────────────
def synthesize_speech(text):
    if not FISH_API_KEY or not FISH_VOICE_ID:
        log("WARN", "Fish Audio 키/ID 없음 — TTS 건너뜀")
        return None

    log("INFO", "Fish Audio TTS 합성 중...")
    url = "https://api.fish.audio/v1/tts"
    headers = {
        "Authorization": f"Bearer {FISH_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "text": text,
        "reference_id": FISH_VOICE_ID,
        "format": "mp3",
        "mp3_bitrate": 192,
        "normalize": True,
        "latency": "normal",
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=30)
    if resp.status_code != 200:
        log("ERR", f"Fish Audio 오류 {resp.status_code}: {resp.text[:200]}")
        return None

    out_path = str(AUDIO_DIR / f"jarvis_{int(time.time())}.mp3")
    with open(out_path, "wb") as f:
        f.write(resp.content)
    log("OK", f"TTS 저장: {out_path}")
    return out_path


# ──────────────────────────────────────────────
#  MediaPipe 손동작 인식
# ──────────────────────────────────────────────
mp_hands  = mp.solutions.hands
mp_draw   = mp.solutions.drawing_utils
mp_styles = mp.solutions.drawing_styles


def detect_gesture(hand_landmarks):
    lm     = hand_landmarks.landmark
    tips   = [4, 8, 12, 16, 20]
    joints = [3, 6, 10, 14, 18]

    fingers_open = 0
    if lm[4].x > lm[3].x:
        fingers_open += 1
    for tip, joint in zip(tips[1:], joints[1:]):
        if lm[tip].y < lm[joint].y:
            fingers_open += 1

    if fingers_open <= 1:
        return "FIST"
    elif fingers_open >= 4:
        return "OPEN_PALM"
    return "NONE"


# ──────────────────────────────────────────────
#  HUD 오버레이 (OpenCV)
# ──────────────────────────────────────────────
def draw_hud(frame, state, transcript="", answer=""):
    h, w = frame.shape[:2]
    overlay = frame.copy()

    cv2.rectangle(overlay, (0, 0), (w, 52), (0, 0, 0), -1)
    cv2.addWeighted(overlay, 0.55, frame, 0.45, 0, frame)

    cv2.putText(frame, "J.A.R.V.I.S", (14, 34),
                cv2.FONT_HERSHEY_DUPLEX, 0.9, (0, 220, 255), 2)

    state_colors = {
        "IDLE":       (100, 100, 100),
        "RECORDING":  (0, 60, 255),
        "PROCESSING": (0, 180, 255),
        "SPEAKING":   (0, 255, 140),
    }
    color = state_colors.get(state, (200, 200, 200))
    cv2.putText(frame, f"[ {state} ]", (w - 180, 34),
                cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2)

    provider_label = f"LLM: {LLM_PROVIDER.upper()}"
    cv2.putText(frame, provider_label, (w - 160, h - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.40, (80, 200, 120), 1)

    cv2.putText(frame, "FIST=Start  |  PALM=Stop", (14, h - 14),
                cv2.FONT_HERSHEY_SIMPLEX, 0.44, (160, 160, 160), 1)

    if transcript:
        txt = f"You: {transcript[:60]}{'...' if len(transcript) > 60 else ''}"
        cv2.putText(frame, txt, (14, h - 62),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.46, (200, 200, 255), 1)
    if answer:
        ans = f"JARVIS: {answer[:65]}{'...' if len(answer) > 65 else ''}"
        cv2.putText(frame, ans, (14, h - 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.46, (0, 255, 180), 1)

    if state == "RECORDING":
        cv2.circle(frame, (w - 20, 20), 8, (0, 0, 255), -1)

    return frame


# ──────────────────────────────────────────────
#  메인 루프
# ──────────────────────────────────────────────
def main():
    log("INFO", "J.A.R.V.I.S 초기화 중...")

    if not OPENAI_API_KEY:
        log("ERR", ".env 파일에 OPENAI_API_KEY가 없습니다. (Whisper STT에 필요)")
        sys.exit(1)

    if LLM_PROVIDER == "gemini" and not GEMINI_API_KEY:
        log("ERR", ".env 파일에 GEMINI_API_KEY가 없습니다.")
        sys.exit(1)

    log("INFO", f"LLM 제공자: {LLM_PROVIDER.upper()}")

    openai_client = OpenAI(api_key=OPENAI_API_KEY)
    recorder      = AudioRecorder()
    history       = []

    state             = "IDLE"
    last_gesture      = "NONE"
    gesture_hold      = 0
    GESTURE_THRESHOLD = 12
    last_transcript   = ""
    last_answer       = ""

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        log("ERR", "웹캠을 열 수 없습니다.")
        sys.exit(1)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    hands = mp_hands.Hands(
        model_complexity=1,
        max_num_hands=1,
        min_detection_confidence=0.7,
        min_tracking_confidence=0.6,
    )

    log("OK", "자비스 시스템 준비 완료. 웹캠 창을 클릭 후 'Q'로 종료.")
    log("INFO", "→ 주먹 쥐기: 음성 녹음 시작 | 손바닥 펼치기: 녹음 종료")

    result_q = queue.Queue()

    def process_audio_async(wav_path):
        try:
            text = transcribe_audio(wav_path, openai_client)
            if not text:
                result_q.put(("ERROR", "음성을 인식하지 못했습니다.", ""))
                return
            answer = ask_jarvis(text, openai_client, history)
            history.append({"role": "user",     "content": text})
            history.append({"role": "assistant", "content": answer})
            audio_path = synthesize_speech(answer)
            result_q.put(("OK", text, answer, audio_path))
        except Exception as e:
            log("ERR", f"처리 오류: {e}")
            result_q.put(("ERROR", str(e), ""))

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame = cv2.flip(frame, 1)

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        rgb.flags.writeable = False
        results = hands.process(rgb)
        rgb.flags.writeable = True

        current_gesture = "NONE"
        if results.multi_hand_landmarks:
            for hl in results.multi_hand_landmarks:
                mp_draw.draw_landmarks(
                    frame, hl,
                    mp_hands.HAND_CONNECTIONS,
                    mp_styles.get_default_hand_landmarks_style(),
                    mp_styles.get_default_hand_connections_style(),
                )
                current_gesture = detect_gesture(hl)

        if current_gesture == last_gesture:
            gesture_hold = min(gesture_hold + 1, GESTURE_THRESHOLD + 5)
        else:
            gesture_hold = 0
            last_gesture = current_gesture

        gesture_confirmed = gesture_hold >= GESTURE_THRESHOLD

        if state == "IDLE":
            if gesture_confirmed and current_gesture == "FIST":
                state = "RECORDING"
                gesture_hold = 0
                recorder.start()

        elif state == "RECORDING":
            if gesture_confirmed and current_gesture == "OPEN_PALM":
                state = "PROCESSING"
                gesture_hold = 0
                wav_path = recorder.stop()
                if wav_path:
                    log("INFO", "비동기 처리 시작...")
                    threading.Thread(
                        target=process_audio_async, args=(wav_path,), daemon=True
                    ).start()
                else:
                    log("WARN", "녹음 데이터 없음 → IDLE로 복귀")
                    state = "IDLE"

        elif state == "PROCESSING":
            if not result_q.empty():
                result = result_q.get_nowait()
                if result[0] == "OK":
                    _, last_transcript, last_answer, audio_path = result
                    state = "SPEAKING"

                    def play_and_return():
                        if audio_path:
                            play_audio_file(audio_path)

                    threading.Thread(target=play_and_return, daemon=True).start()
                    play_duration = max(2.0, len(last_answer) * 0.07)
                    threading.Timer(play_duration, lambda: None).start()
                    state = "IDLE"
                else:
                    log("ERR", f"처리 실패: {result[1]}")
                    state = "IDLE"

        frame = draw_hud(frame, state, last_transcript, last_answer)
        cv2.imshow("J.A.R.V.I.S", frame)

        key = cv2.waitKey(1) & 0xFF
        if key == ord("q") or key == 27:
            log("INFO", "자비스 종료 중...")
            break
        elif key == ord("c"):
            history.clear()
            last_transcript = ""
            last_answer = ""
            log("INFO", "대화 히스토리 초기화 완료")

    if recorder._recording:
        recorder.stop()
    hands.close()
    cap.release()
    cv2.destroyAllWindows()
    log("OK", "자비스 시스템 정상 종료.")


if __name__ == "__main__":
    main()
