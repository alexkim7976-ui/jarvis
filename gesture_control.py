"""
J.A.R.V.I.S — 제스처 마우스 & OS 제어
───────────────────────────────────────
MediaPipe 손가락 좌표로 마우스 이동, 클릭, 볼륨, 스크롤, 단축키를 제어합니다.

제스처 목록:
  ☝ 검지만 펴기       → 마우스 커서 이동
  🤏 엄지+검지 핀치    → 클릭
  ✌ 검지+중지 (V)     → 우클릭
  🖐→✊ 손바닥→주먹    → 드래그
  👆👇 검지+중지 위아래 → 스크롤
  👍 엄지 위           → 볼륨 업
  👎 엄지 아래         → 볼륨 다운
  ✋ 세 손가락         → 재생/일시정지
  🤙 엄지+새끼         → 스크린샷

토글: G키로 제스처 제어 ON/OFF
의존성: pip install pyautogui
"""

from __future__ import annotations

import sys
import time
import math
import subprocess
import threading
from datetime import datetime
from collections import deque

import cv2
import numpy as np

try:
    import pyautogui
    pyautogui.FAILSAFE = False  # 모서리 안전장치 해제
    pyautogui.PAUSE = 0.01     # 명령 간 딜레이 최소화
    PYAUTOGUI_AVAILABLE = True
except ImportError:
    PYAUTOGUI_AVAILABLE = False


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 🖐 [제스처] {msg}")


# ──────────────────────────────────────────────
#  손가락 상태 판별 유틸리티
# ──────────────────────────────────────────────
class HandState:
    """MediaPipe 랜드마크에서 손가락 상태 추출"""

    # 랜드마크 인덱스
    WRIST = 0
    THUMB_TIP = 4;  THUMB_IP = 3;   THUMB_MCP = 2
    INDEX_TIP = 8;  INDEX_PIP = 6;  INDEX_MCP = 5
    MIDDLE_TIP = 12; MIDDLE_PIP = 10
    RING_TIP = 16;   RING_PIP = 14
    PINKY_TIP = 20;  PINKY_PIP = 18

    def __init__(self, landmarks, frame_w, frame_h):
        self.lm = landmarks
        self.w = frame_w
        self.h = frame_h

    def _pt(self, idx):
        """랜드마크를 픽셀 좌표로 변환"""
        l = self.lm[idx]
        return (l.x * self.w, l.y * self.h)

    def _dist(self, idx1, idx2):
        """두 랜드마크 사이 거리"""
        p1 = self._pt(idx1)
        p2 = self._pt(idx2)
        return math.sqrt((p1[0]-p2[0])**2 + (p1[1]-p2[1])**2)

    def _is_finger_up(self, tip, pip):
        """손가락이 펴져 있는지 (tip이 pip보다 위)"""
        return self.lm[tip].y < self.lm[pip].y

    @property
    def index_up(self):
        return self._is_finger_up(self.INDEX_TIP, self.INDEX_PIP)

    @property
    def middle_up(self):
        return self._is_finger_up(self.MIDDLE_TIP, self.MIDDLE_PIP)

    @property
    def ring_up(self):
        return self._is_finger_up(self.RING_TIP, self.RING_PIP)

    @property
    def pinky_up(self):
        return self._is_finger_up(self.PINKY_TIP, self.PINKY_PIP)

    @property
    def thumb_up(self):
        """엄지가 위로 향하는지"""
        return self.lm[self.THUMB_TIP].y < self.lm[self.THUMB_MCP].y - 0.05

    @property
    def thumb_down(self):
        """엄지가 아래로 향하는지"""
        return self.lm[self.THUMB_TIP].y > self.lm[self.THUMB_MCP].y + 0.08

    @property
    def pinch_distance(self):
        """엄지-검지 사이 거리 (0~1 정규화)"""
        return self._dist(self.THUMB_TIP, self.INDEX_TIP) / self.w

    @property
    def index_pos(self):
        """검지 끝 좌표 (0~1 정규화)"""
        return (self.lm[self.INDEX_TIP].x, self.lm[self.INDEX_TIP].y)

    @property
    def fingers_up_count(self):
        """펴진 손가락 수"""
        count = 0
        if self.index_up: count += 1
        if self.middle_up: count += 1
        if self.ring_up: count += 1
        if self.pinky_up: count += 1
        if self.lm[self.THUMB_TIP].x > self.lm[self.THUMB_IP].x: count += 1
        return count

    def detect_gesture(self):
        """현재 제스처 판별"""
        iu = self.index_up
        mu = self.middle_up
        ru = self.ring_up
        pu = self.pinky_up
        pinch = self.pinch_distance < 0.04

        # 핀치 (엄지+검지 가까움)
        if pinch:
            return "PINCH"

        # 엄지+새끼만 (전화/스크린샷)
        if pu and not iu and not mu and not ru and self.thumb_up:
            return "CALL"

        # 엄지만 위
        if self.thumb_up and not iu and not mu and not ru and not pu:
            return "THUMB_UP"

        # 엄지만 아래
        if self.thumb_down and not iu and not mu and not ru and not pu:
            return "THUMB_DOWN"

        # 검지만 (포인트)
        if iu and not mu and not ru and not pu:
            return "POINT"

        # 검지+중지 (V = 스크롤/우클릭)
        if iu and mu and not ru and not pu:
            return "PEACE"

        # 세 손가락 (재생/일시정지)
        if iu and mu and ru and not pu:
            return "THREE"

        # 손바닥 (모두 펴짐)
        if iu and mu and ru and pu:
            return "OPEN"

        # 주먹 (모두 접힘)
        if not iu and not mu and not ru and not pu:
            return "FIST"

        return "NONE"


# ──────────────────────────────────────────────
#  제스처 컨트롤러
# ──────────────────────────────────────────────
class GestureController:
    """손 제스처로 마우스/OS 제어"""

    def __init__(self):
        self.enabled = False
        self._prev_gesture = "NONE"
        self._gesture_hold = 0
        self._action_cooldown = {}  # 제스처별 마지막 실행 시각

        # 마우스 스무딩
        self._mouse_history = deque(maxlen=5)
        self._screen_w, self._screen_h = self._get_screen_size()
        self._prev_pinch = False

        # 스크롤 상태
        self._scroll_base_y = 0
        self._scrolling = False

        # 볼륨 상태
        self._volume_cooldown = 0

        # 현재 제스처 (HUD 표시용)
        self.current_gesture = "NONE"
        self.current_action = ""

        if not PYAUTOGUI_AVAILABLE:
            _log("pyautogui 미설치 — pip install pyautogui")

    def _get_screen_size(self):
        try:
            if PYAUTOGUI_AVAILABLE:
                return pyautogui.size()
        except:
            pass
        return (1920, 1080)

    def _cooldown_ok(self, action, interval=0.5):
        """쿨다운 확인"""
        now = time.time()
        last = self._action_cooldown.get(action, 0)
        if now - last >= interval:
            self._action_cooldown[action] = now
            return True
        return False

    def process(self, hand_landmarks, frame_w, frame_h):
        """
        매 프레임 호출 — 손 랜드마크를 받아 제스처 판별 및 동작 실행

        Returns: (gesture_name, action_description) 튜플
        """
        if not self.enabled or not PYAUTOGUI_AVAILABLE:
            return ("OFF", "")

        hs = HandState(hand_landmarks.landmark, frame_w, frame_h)
        gesture = hs.detect_gesture()
        self.current_gesture = gesture

        # 제스처 연속 카운트
        if gesture == self._prev_gesture:
            self._gesture_hold += 1
        else:
            self._gesture_hold = 0
            self._prev_gesture = gesture
            self._scrolling = False

        action = ""

        # ── 포인트: 마우스 이동 ──
        if gesture == "POINT":
            ix, iy = hs.index_pos
            # 웹캠 좌표 → 화면 좌표 (반전 + 범위 확장)
            # 손이 화면 중앙 부분만 사용하도록 매핑 (0.15~0.85 → 0~1)
            mx = max(0, min(1, (ix - 0.15) / 0.7))
            my = max(0, min(1, (iy - 0.1) / 0.7))
            target_x = int(mx * self._screen_w)
            target_y = int(my * self._screen_h)

            # 스무딩
            self._mouse_history.append((target_x, target_y))
            if len(self._mouse_history) >= 2:
                avg_x = int(sum(p[0] for p in self._mouse_history) / len(self._mouse_history))
                avg_y = int(sum(p[1] for p in self._mouse_history) / len(self._mouse_history))
                pyautogui.moveTo(avg_x, avg_y, _pause=False)
            action = f"마우스 ({target_x},{target_y})"

        # ── 핀치: 클릭 ──
        elif gesture == "PINCH":
            if not self._prev_pinch and self._cooldown_ok("click", 0.4):
                pyautogui.click(_pause=False)
                action = "클릭!"
                _log("클릭")
            self._prev_pinch = True

        # ── 피스: 스크롤 ──
        elif gesture == "PEACE":
            iy = hs.lm[hs.INDEX_TIP].y
            if not self._scrolling:
                self._scroll_base_y = iy
                self._scrolling = True
            else:
                delta = iy - self._scroll_base_y
                if abs(delta) > 0.02:
                    scroll_amount = int(-delta * 30)
                    pyautogui.scroll(scroll_amount, _pause=False)
                    direction = "↑" if scroll_amount > 0 else "↓"
                    action = f"스크롤 {direction}"
                    self._scroll_base_y = iy

        # ── 엄지 위: 볼륨 업 ──
        elif gesture == "THUMB_UP":
            if self._cooldown_ok("volume", 0.8):
                self._adjust_volume("up")
                action = "볼륨 🔊"

        # ── 엄지 아래: 볼륨 다운 ──
        elif gesture == "THUMB_DOWN":
            if self._cooldown_ok("volume", 0.8):
                self._adjust_volume("down")
                action = "볼륨 🔉"

        # ── 세 손가락: 재생/일시정지 ──
        elif gesture == "THREE":
            if self._cooldown_ok("media", 1.0):
                self._media_playpause()
                action = "재생/일시정지 ⏯"

        # ── 전화 (엄지+새끼): 스크린샷 ──
        elif gesture == "CALL":
            if self._cooldown_ok("screenshot", 2.0):
                self._take_screenshot()
                action = "스크린샷 📸"

        else:
            self._prev_pinch = False
            self._scrolling = False

        if gesture != "PINCH":
            self._prev_pinch = False

        self.current_action = action
        return (gesture, action)

    def draw_overlay(self, frame, hand_landmarks):
        """제스처 상태를 화면에 시각적으로 표시"""
        if not self.enabled:
            return frame

        h, w = frame.shape[:2]
        lm = hand_landmarks.landmark

        # 검지 끝에 원 그리기
        ix = int(lm[HandState.INDEX_TIP].x * w)
        iy = int(lm[HandState.INDEX_TIP].y * h)

        if self.current_gesture == "POINT":
            # 포인터 커서 (시안 원 + 십자)
            cv2.circle(frame, (ix, iy), 15, (255, 220, 0), 2, cv2.LINE_AA)
            cv2.line(frame, (ix-8, iy), (ix+8, iy), (255, 220, 0), 1, cv2.LINE_AA)
            cv2.line(frame, (ix, iy-8), (ix, iy+8), (255, 220, 0), 1, cv2.LINE_AA)

        elif self.current_gesture == "PINCH":
            # 클릭 효과 (빨간 원)
            cv2.circle(frame, (ix, iy), 20, (0, 0, 255), 3, cv2.LINE_AA)
            cv2.circle(frame, (ix, iy), 8, (0, 0, 255), -1, cv2.LINE_AA)

        elif self.current_gesture == "PEACE":
            # 스크롤 화살표
            cv2.arrowedLine(frame, (ix, iy+20), (ix, iy-20), (0, 255, 255), 2, cv2.LINE_AA)
            cv2.arrowedLine(frame, (ix, iy-20), (ix, iy+20), (0, 255, 255), 2, cv2.LINE_AA)

        elif self.current_gesture in ("THUMB_UP", "THUMB_DOWN"):
            # 볼륨 아이콘
            color = (0, 255, 0) if self.current_gesture == "THUMB_UP" else (0, 100, 255)
            tx = int(lm[HandState.THUMB_TIP].x * w)
            ty = int(lm[HandState.THUMB_TIP].y * h)
            cv2.circle(frame, (tx, ty), 18, color, 3, cv2.LINE_AA)

        # 제스처 이름 + 동작 표시 (우상단)
        if self.current_gesture != "NONE":
            label = f"GESTURE: {self.current_gesture}"
            cv2.putText(frame, label, (w-250, 85),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 220, 0), 1, cv2.LINE_AA)
        if self.current_action:
            cv2.putText(frame, self.current_action, (w-250, 105),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 100), 1, cv2.LINE_AA)

        # 제스처 모드 표시
        cv2.putText(frame, "[G] GESTURE CTRL ON", (w-200, 50),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 200, 255), 1, cv2.LINE_AA)

        return frame

    # ──────────────────────────────────
    #  OS 제어 함수
    # ──────────────────────────────────
    def _adjust_volume(self, direction):
        """볼륨 조절"""
        try:
            if sys.platform == "darwin":
                # macOS: osascript
                if direction == "up":
                    subprocess.run(["osascript", "-e", "set volume output volume ((output volume of (get volume settings)) + 10)"],
                                   capture_output=True, timeout=2)
                else:
                    subprocess.run(["osascript", "-e", "set volume output volume ((output volume of (get volume settings)) - 10)"],
                                   capture_output=True, timeout=2)
                _log(f"볼륨 {'↑' if direction=='up' else '↓'}")
            elif sys.platform.startswith("linux"):
                sign = "+" if direction == "up" else "-"
                subprocess.run(["amixer", "set", "Master", f"5%{sign}"],
                               capture_output=True, timeout=2)
        except Exception as e:
            _log(f"볼륨 조절 실패: {e}")

    def _media_playpause(self):
        """미디어 재생/일시정지"""
        try:
            if sys.platform == "darwin":
                subprocess.run(["osascript", "-e",
                    'tell application "System Events" to key code 49 using {}'],
                    capture_output=True, timeout=2)
                # Spotify 제어 시도
                subprocess.run(["osascript", "-e",
                    'tell application "Spotify" to playpause'],
                    capture_output=True, timeout=2)
                _log("재생/일시정지")
        except:
            pass

    def _take_screenshot(self):
        """스크린샷"""
        try:
            if sys.platform == "darwin":
                subprocess.run(["screencapture", "-i", "-c"],
                               capture_output=True, timeout=10)
                _log("스크린샷 촬영")
            elif sys.platform.startswith("linux"):
                subprocess.run(["gnome-screenshot", "-c"],
                               capture_output=True, timeout=5)
        except Exception as e:
            _log(f"스크린샷 실패: {e}")
