from __future__ import annotations

from speech_style import USER_DISPLAY_NAME

import os
import json
import time
import threading
from pathlib import Path
from datetime import datetime

import cv2
import numpy as np

# face_recognition 라이브러리 (없으면 폴백)
try:
    import face_recognition
    FACE_LIB_AVAILABLE = True
except ImportError:
    FACE_LIB_AVAILABLE = False

FACES_DIR = Path("faces")
FACES_DIR.mkdir(exist_ok=True)
# 안전한 저장 포맷 — pickle 역직렬화 RCE 방지 위해 numpy npz 사용 (allow_pickle=False)
ENCODINGS_FILE = FACES_DIR / "encodings.npz"


class FaceRecognizer:
    """얼굴 등록, 인식, 자동 인사/경고"""

    def __init__(self, tolerance=0.5, *, enabled: bool | None = None):
        self.tolerance = tolerance
        self._known_names = []
        self._known_encodings = []
        self._lock = threading.Lock()

        # 인사/경고 쿨다운 (같은 사람에게 반복 안 하도록)
        self._greeted = {}        # {name: timestamp}
        self._warned_unknown = 0  # 마지막 경고 시각
        self.GREET_COOLDOWN = 300     # 5분 쿨다운
        self.UNKNOWN_COOLDOWN = 60    # 1분 쿨다운

        # 상태 — enabled=False면 인코딩·캐스케이드 로드 생략(저사양 모드)
        if enabled is False:
            self.enabled = False
        else:
            self.enabled = FACE_LIB_AVAILABLE
        self.last_results = []    # 최근 인식 결과 [(name, (t,r,b,l)), ...]

        # 이벤트 큐 (메인 루프에서 TTS 트리거용)
        self.events = []  # [{"type": "greet"/"warn", "name": "...", "message": "..."}]

        if not self.enabled:
            self._log("INFO", "얼굴 인식 비활성화 (저사양/설정)")
            self._cascade = None
        elif not FACE_LIB_AVAILABLE:
            self._log("WARN", "face_recognition 미설치 — 얼굴 인식 비활성화")
            self._log("INFO", "설치: brew install cmake && pip install face-recognition")
            # OpenCV 폴백: 감지만 가능
            self._cascade = cv2.CascadeClassifier(
                cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
            )
        else:
            self._cascade = None
            self._load_encodings()

    def _log(self, level, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        icons = {"INFO": "◈", "OK": "✓", "WARN": "⚠", "FACE": "👤"}
        print(f"[{ts}] {icons.get(level, '·')} [얼굴] {msg}")

    # ──────────────────────────────────
    #  등록
    # ──────────────────────────────────
    def register_face(self, frame, name="Owner"):
        """현재 프레임에서 얼굴을 등록"""
        if not FACE_LIB_AVAILABLE:
            return {"success": False, "message": "face_recognition 라이브러리가 설치되지 않았습니다."}

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        locations = face_recognition.face_locations(rgb, model="hog")

        if not locations:
            return {"success": False, "message": "화면에서 얼굴을 찾을 수 없습니다. 카메라를 정면으로 바라봐주세요."}

        if len(locations) > 1:
            return {"success": False, "message": f"화면에 {len(locations)}명이 감지되었습니다. 등록할 사람만 화면에 있어야 합니다."}

        encoding = face_recognition.face_encodings(rgb, locations)[0]

        with self._lock:
            # 이미 같은 이름이 등록되어 있으면 업데이트
            if name in self._known_names:
                idx = self._known_names.index(name)
                self._known_encodings[idx] = encoding
                self._log("OK", f"'{name}' 얼굴 업데이트 완료")
            else:
                self._known_names.append(name)
                self._known_encodings.append(encoding)
                self._log("OK", f"'{name}' 얼굴 등록 완료")

        # 얼굴 이미지 저장
        top, right, bottom, left = locations[0]
        face_img = frame[top:bottom, left:right]
        img_path = str(FACES_DIR / f"{name}.jpg")
        cv2.imwrite(img_path, face_img)

        # 인코딩 저장
        self._save_encodings()

        return {
            "success": True,
            "message": f"'{name}' 얼굴이 성공적으로 등록되었습니다.",
            "registered_count": len(self._known_names),
        }

    def remove_face(self, name):
        """등록된 얼굴 삭제"""
        with self._lock:
            if name in self._known_names:
                idx = self._known_names.index(name)
                self._known_names.pop(idx)
                self._known_encodings.pop(idx)
                self._save_encodings()
                # 이미지 파일도 삭제
                img_path = FACES_DIR / f"{name}.jpg"
                if img_path.exists():
                    img_path.unlink()
                return {"success": True, "message": f"'{name}' 삭제 완료"}
            return {"success": False, "message": f"'{name}'이 등록되어 있지 않습니다."}

    def list_faces(self):
        """등록된 얼굴 목록"""
        return {
            "success": True,
            "faces": list(self._known_names),
            "count": len(self._known_names),
        }

    # ──────────────────────────────────
    #  인식
    # ──────────────────────────────────
    def recognize(self, frame):
        """프레임에서 얼굴 인식 → 결과 + 이벤트 생성"""
        results = []
        self.events = []

        if FACE_LIB_AVAILABLE:
            results = self._recognize_full(frame)
        else:
            results = self._recognize_fallback(frame)

        self.last_results = results
        now = time.time()

        for name, location in results:
            if name == "Unknown":
                # 모르는 사람 경고
                if now - self._warned_unknown > self.UNKNOWN_COOLDOWN:
                    self._warned_unknown = now
                    self.events.append({
                        "type": "warn",
                        "name": "Unknown",
                        "message": f"{USER_DISPLAY_NAME}, 등록되지 않은 인물이 감지되었습니다. 주의가 필요합니다.",
                    })
                    self._log("FACE", "⚠ 미등록 인물 감지")
            else:
                # 등록된 사람 인사
                last_greet = self._greeted.get(name, 0)
                if now - last_greet > self.GREET_COOLDOWN:
                    self._greeted[name] = now
                    # 현지(IP 기반) 시각으로 아침/낮/저녁/밤 구분 — 시스템 시간이 아닌 사용자 위치 기준
                    try:
                        from weather_module import local_time_greeting, local_offset_cached
                        greet, _, _ = local_time_greeting(local_offset_cached())
                    except Exception:
                        h = datetime.now().hour
                        greet = "좋은 아침입니다" if h < 12 else ("안녕하세요" if h < 18 else "좋은 저녁입니다")
                    display = USER_DISPLAY_NAME if name in ("Owner", "Boss") else name
                    greeting = f"{greet}, {display}."
                    self.events.append({
                        "type": "greet",
                        "name": name,
                        "message": greeting,
                    })
                    self._log("FACE", f"✓ {name} 인식 → 인사")

        return results

    def _recognize_full(self, frame):
        """face_recognition 라이브러리 사용"""
        small = cv2.resize(frame, (0, 0), fx=0.5, fy=0.5)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)

        locations = face_recognition.face_locations(rgb, model="hog")
        if not locations:
            return []

        encodings = face_recognition.face_encodings(rgb, locations)
        results = []

        with self._lock:
            for encoding, loc in zip(encodings, locations):
                name = "Unknown"
                if self._known_encodings:
                    distances = face_recognition.face_distance(
                        self._known_encodings, encoding
                    )
                    min_idx = np.argmin(distances)
                    if distances[min_idx] < self.tolerance:
                        name = self._known_names[min_idx]

                # 좌표를 원본 크기로 복원 (2배)
                top, right, bottom, left = loc
                results.append((name, (top*2, right*2, bottom*2, left*2)))

        return results

    def _recognize_fallback(self, frame):
        """OpenCV 폴백 — 감지만 (식별 불가)"""
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self._cascade.detectMultiScale(gray, 1.3, 5)
        results = []
        for (x, y, w, h) in faces:
            results.append(("Detected", (y, x+w, y+h, x)))
        return results

    # ──────────────────────────────────
    #  화면 렌더링
    # ──────────────────────────────────
    def draw_faces(self, frame):
        """프레임에 얼굴 박스와 이름 표시"""
        for name, (top, right, bottom, left) in self.last_results:
            if name == "Unknown":
                color = (0, 0, 255)      # 빨강
                label_bg = (0, 0, 180)
            elif name == "Detected":
                color = (200, 200, 0)    # 노랑
                label_bg = (150, 150, 0)
            else:
                color = (0, 220, 0)      # 초록
                label_bg = (0, 160, 0)

            # 얼굴 박스
            cv2.rectangle(frame, (left, top), (right, bottom), color, 2)

            # 이름 라벨 배경
            label = name
            label_w = len(label) * 12 + 10
            cv2.rectangle(frame, (left, bottom),
                          (left + label_w, bottom + 25), label_bg, -1)
            cv2.putText(frame, label,
                        (left + 5, bottom + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                        (255, 255, 255), 1)

            # 상태 아이콘
            if name == "Unknown":
                cv2.putText(frame, "!",
                            (right - 20, top + 20),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                            (0, 0, 255), 2)

        return frame

    # ──────────────────────────────────
    #  저장/로드
    # ──────────────────────────────────
    def _save_encodings(self):
        # numpy npz — 임의 객체 역직렬화 없음. 이름은 고정폭 유니코드 배열.
        names = np.array(self._known_names, dtype="<U64")
        if self._known_encodings:
            encs = np.stack(self._known_encodings).astype(np.float64)
        else:
            encs = np.empty((0, 128), dtype=np.float64)
        np.savez_compressed(ENCODINGS_FILE, names=names, encodings=encs)
        self._log("OK", f"인코딩 저장 ({len(self._known_names)}명)")

    def _load_encodings(self):
        if ENCODINGS_FILE.exists():
            try:
                with np.load(ENCODINGS_FILE, allow_pickle=False) as data:
                    self._known_names = [str(n) for n in data["names"].tolist()]
                    enc_arr = data["encodings"]
                    self._known_encodings = [enc_arr[i] for i in range(enc_arr.shape[0])]
                self._log("OK", f"등록된 얼굴 {len(self._known_names)}명 로드")
            except Exception as e:
                self._log("WARN", f"인코딩 로드 실패: {e}")
        elif (FACES_DIR / "encodings.pkl").exists():
            # 마이그레이션되지 않은 레거시 .pkl — pickle 로드 거부
            self._log("WARN", "레거시 encodings.pkl 발견 — 보안상 자동 로드하지 않음. "
                              "필요 시 수동 마이그레이션 후 .npz로 재저장하세요.")

    @property
    def registered_count(self):
        return len(self._known_names)


# ──────────────────────────────────────────────
#  LLM 도구 정의
# ──────────────────────────────────────────────
FACE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "register_face",
            "description": "현재 카메라 화면에 보이는 얼굴을 등록합니다. 등록 후 해당 사용자를 자동 인식합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": f"등록할 사람의 이름 (예: {USER_DISPLAY_NAME}, Owner, Boss)",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remove_face",
            "description": "등록된 얼굴을 삭제합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "삭제할 사람의 이름",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_faces",
            "description": "등록된 얼굴 목록을 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

# ──────────────────────────────────────────────
#  시선 기반 상황 인지 (Gaze-Triggered Context)
# ──────────────────────────────────────────────
#  주의: 본 모듈은 얼굴 '인식'(bounding box)만 수행하며 정밀 시선(eye/iris) 추적기는
#  포함하지 않는다. GazeTracker 는 시선 좌표(screen_x, screen_y)를 외부 입력으로 받아
#  gaze_context 의 3초 dwell 디바운스 → current_focused_ticker 갱신/이벤트 발행으로
#  연결하는 어댑터다. 전용 아이트래커가 있으면 그 (x, y)를 update_gaze 로 직접 넣고,
#  없으면 estimate_gaze_point() 의 coarse proxy(얼굴 중심 위치)를 임시로 쓸 수 있다.
from gaze_context import get_gaze_context


class GazeTracker:
    """화면 UI 영역(종목 HUD 패널 등)에 대한 시선 dwell 추적 어댑터."""

    def __init__(self, context=None):
        self.ctx = context or get_gaze_context()

    # ─ UI 영역(Bounding Box) 등록 — HUD/오버레이가 패널 위치를 알려준다 ─
    def register_ticker_region(self, region_id, bbox, ticker, label=""):
        self.ctx.register_region(region_id, bbox, ticker, label)

    def clear_regions(self):
        self.ctx.clear_regions()

    # ─ 시선 좌표 주입 → 3초 dwell 디바운스 (확정 시 ticker 반환) ─
    def update_gaze(self, screen_x, screen_y):
        return self.ctx.update_gaze(screen_x, screen_y)

    def current_focused_ticker(self):
        return self.ctx.get_focused_ticker()

    def on_focus_changed(self, callback):
        """GazeFocusedTickerChanged 이벤트 구독."""
        self.ctx.subscribe(callback)

    @staticmethod
    def estimate_gaze_point(face_bbox, frame_size, screen_size):
        """[근사/coarse proxy] 아이트래커가 없을 때 얼굴 중심 위치를 화면 좌표로 매핑.

        정밀 시선 추적이 아니다(머리 위치 ≈ 응시 방향이라는 가정). 데모/폴백용이며
        실제 배포 시에는 전용 아이트래커의 (x, y)를 update_gaze 에 직접 넣는 것을 권장.
        카메라 좌우 반전을 고려해 x 를 미러링한다.
        """
        top, right, bottom, left = face_bbox
        fw, fh = frame_size
        sw, sh = screen_size
        cx = (left + right) / 2.0
        cy = (top + bottom) / 2.0
        gx = (1.0 - cx / max(fw, 1)) * sw
        gy = (cy / max(fh, 1)) * sh
        return (gx, gy)


# 전역 GazeTracker 싱글톤
_gaze_tracker = None


def get_gaze_tracker():
    global _gaze_tracker
    if _gaze_tracker is None:
        _gaze_tracker = GazeTracker()
    return _gaze_tracker


# 전역 인스턴스 & 프레임 참조
_face_instance = None
_current_frame = None

def set_face_instance(instance):
    global _face_instance
    _face_instance = instance

def set_current_frame(frame):
    global _current_frame
    _current_frame = frame

def execute_face_tool(tool_name, arguments):
    global _face_instance, _current_frame
    if not _face_instance:
        return {"success": False, "message": "얼굴 인식 모듈 없음"}

    if isinstance(arguments, str):
        arguments = json.loads(arguments)

    if tool_name == "register_face":
        if _current_frame is None:
            return {"success": False, "message": "카메라 프레임 없음"}
        return _face_instance.register_face(_current_frame, arguments.get("name", "Owner"))
    elif tool_name == "remove_face":
        return _face_instance.remove_face(arguments.get("name", ""))
    elif tool_name == "list_faces":
        return _face_instance.list_faces()

    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}
