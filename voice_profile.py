"""
J.A.R.V.I.S — 음성 프로필 & 학습 모듈
──────────────────────────────────────────
사용자가 "자비스"라고 말하는 샘플을 수집하고,
음성 특징(MFCC)을 학습하여 로컬에서 웨이크워드를 감지합니다.

장점:
  - Whisper API 호출 없이 빠른 감지 (0.1초 이내)
  - API 비용 절감
  - 사용자 목소리에 최적화

사용법:
  V키 → 샘플 수집 모드 (5회 녹음)
  수집 완료 후 자동으로 학습
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import uuid
import time
import wave
import struct
import threading
import numpy as np
from pathlib import Path
from datetime import datetime

import pyaudio

# ──────────────────────────────────────────────
#  설정 — 프로젝트 루트 기준 절대 경로 (앱/터미널 cwd 무관)
# ──────────────────────────────────────────────
_PROJECT_ROOT = Path(__file__).resolve().parent
VOICE_DIR = Path(os.getenv("JARVIS_VOICE_PROFILE_DIR", str(_PROJECT_ROOT / "voice_profile")))
VOICE_DIR.mkdir(parents=True, exist_ok=True)
try:
    os.chmod(str(VOICE_DIR), 0o700)
except OSError:
    pass
# 안전한 저장 포맷 — pickle 역직렬화 RCE 방지 위해 numpy npz 사용 (allow_pickle=False)
PROFILE_FILE = VOICE_DIR / "profile.npz"
_DEVICE_FILE = VOICE_DIR / "device.json"
_LEGACY_PROFILE = VOICE_DIR / "profile.pkl"
SAMPLES_DIR = VOICE_DIR / "samples"
SAMPLES_DIR.mkdir(exist_ok=True)

SAMPLE_RATE = 16_000
CHANNELS = 1
CHUNK = 1_024
FORMAT = pyaudio.paInt16

MIN_SAMPLES = 3       # 최소 학습 샘플 수
TARGET_SAMPLES = 5    # 권장 샘플 수
SAMPLE_DURATION = 2.0 # 각 샘플 녹음 시간(초)

# MFCC 설정
N_MFCC = 13
N_FFT = 512
HOP_LENGTH = 160
N_MELS = 26


def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 🎤 [음성학습] {msg}")


def device_fingerprint() -> str:
    """이 Mac 식별자 — 호스트명·아키텍처·MAC 기반 (민감정보 미포함)."""
    raw = "|".join([
        platform.node() or "",
        platform.machine() or "",
        str(uuid.getnode()),
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def is_known_device() -> bool:
    """이 기기에서 과거에 음성 등록을 완료한 적이 있는지."""
    if not _DEVICE_FILE.is_file():
        return False
    try:
        data = json.loads(_DEVICE_FILE.read_text(encoding="utf-8"))
        return data.get("device_id") == device_fingerprint()
    except (OSError, json.JSONDecodeError, KeyError):
        return False


def mark_device_enrolled(*, sample_count: int = 0) -> None:
    """음성 학습 완료 시 기기 등록 기록."""
    payload = {
        "device_id": device_fingerprint(),
        "enrolled_at": datetime.now().isoformat(timespec="seconds"),
        "sample_count": sample_count,
        "profile": PROFILE_FILE.name,
    }
    _DEVICE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(str(_DEVICE_FILE), 0o600)
    except OSError:
        pass


def voice_setup_required(profile: "VoiceProfile | None" = None) -> bool:
    """5회 수집(V키)이 필요한지 — 학습 완료 또는 등록 기기 자동 복구면 False."""
    if profile is None:
        profile = VoiceProfile()
    return not profile.trained


# ──────────────────────────────────────────────
#  MFCC 특징 추출 (numpy 전용, 외부 라이브러리 불필요)
# ──────────────────────────────────────────────
def _hz_to_mel(hz):
    return 2595 * np.log10(1 + hz / 700)

def _mel_to_hz(mel):
    return 700 * (10 ** (mel / 2595) - 1)

def _mel_filter_bank(n_mels, n_fft, sr):
    """멜 필터 뱅크 생성"""
    low_mel = _hz_to_mel(0)
    high_mel = _hz_to_mel(sr / 2)
    mel_points = np.linspace(low_mel, high_mel, n_mels + 2)
    hz_points = _mel_to_hz(mel_points)
    bins = np.floor((n_fft + 1) * hz_points / sr).astype(int)

    filters = np.zeros((n_mels, n_fft // 2 + 1))
    for i in range(n_mels):
        for j in range(bins[i], bins[i + 1]):
            if bins[i + 1] - bins[i] > 0:
                filters[i, j] = (j - bins[i]) / (bins[i + 1] - bins[i])
        for j in range(bins[i + 1], bins[i + 2]):
            if bins[i + 2] - bins[i + 1] > 0:
                filters[i, j] = (bins[i + 2] - j) / (bins[i + 2] - bins[i + 1])
    return filters

def extract_mfcc(audio_data, sr=SAMPLE_RATE, n_mfcc=N_MFCC):
    """오디오 데이터에서 MFCC 특징 추출"""
    # float 변환
    if isinstance(audio_data, bytes):
        count = len(audio_data) // 2
        audio = np.array(struct.unpack(f"{count}h", audio_data), dtype=np.float32)
    elif isinstance(audio_data, np.ndarray):
        audio = audio_data.astype(np.float32)
    else:
        return None

    if len(audio) < N_FFT:
        return None

    # 정규화
    max_val = np.max(np.abs(audio))
    if max_val > 0:
        audio = audio / max_val

    # 프리엠퍼시스
    audio = np.append(audio[0], audio[1:] - 0.97 * audio[:-1])

    # 프레임 분할
    n_frames = max(1, (len(audio) - N_FFT) // HOP_LENGTH + 1)
    frames = np.zeros((n_frames, N_FFT))
    for i in range(n_frames):
        start = i * HOP_LENGTH
        end = start + N_FFT
        if end <= len(audio):
            frames[i] = audio[start:end]

    # 해밍 윈도우
    window = np.hamming(N_FFT)
    frames *= window

    # FFT → 파워 스펙트럼
    mag = np.abs(np.fft.rfft(frames, N_FFT))
    power = (mag ** 2) / N_FFT

    # 멜 필터 뱅크 적용
    mel_bank = _mel_filter_bank(N_MELS, N_FFT, sr)
    mel_spec = np.dot(power, mel_bank.T)
    mel_spec = np.maximum(mel_spec, 1e-10)
    log_mel = np.log(mel_spec)

    # DCT → MFCC
    n_coeff = log_mel.shape[1]
    dct_matrix = np.zeros((n_mfcc, n_coeff))
    for i in range(n_mfcc):
        for j in range(n_coeff):
            dct_matrix[i, j] = np.cos(np.pi * i * (2 * j + 1) / (2 * n_coeff))
    mfcc = np.dot(log_mel, dct_matrix.T)

    return mfcc


def _dtw_distance(a, b):
    """Dynamic Time Warping 거리 계산"""
    n, m = len(a), len(b)
    if n == 0 or m == 0:
        return float("inf")

    # 빠른 계산을 위해 축소
    max_len = 50
    if n > max_len:
        idx = np.linspace(0, n - 1, max_len).astype(int)
        a = a[idx]
        n = max_len
    if m > max_len:
        idx = np.linspace(0, m - 1, max_len).astype(int)
        b = b[idx]
        m = max_len

    dtw = np.full((n + 1, m + 1), float("inf"))
    dtw[0, 0] = 0

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost = np.sqrt(np.sum((a[i - 1] - b[j - 1]) ** 2))
            dtw[i, j] = cost + min(dtw[i - 1, j], dtw[i, j - 1], dtw[i - 1, j - 1])

    return dtw[n, m] / (n + m)


# ──────────────────────────────────────────────
#  음성 프로필 클래스
# ──────────────────────────────────────────────
class VoiceProfile:
    """사용자 음성 샘플 수집 및 매칭"""

    def __init__(self):
        self._templates = []       # 학습된 MFCC 템플릿들
        self._sample_count = 0
        self._threshold = 0.0      # 매칭 임계값 (학습 후 설정)
        self.trained = False
        self.collecting = False    # 샘플 수집 중 여부
        self._collect_count = 0
        self._collect_target = TARGET_SAMPLES

        self._load_profile()
        if not self.trained:
            self._try_retrain_from_samples()
        if self.trained:
            if not is_known_device():
                mark_device_enrolled(sample_count=self._sample_count)
                _log("등록 기기로 기록 — 이후 5회 수집 생략")
        elif is_known_device():
            _log("등록된 기기 — 저장 샘플에서 프로필 복구를 시도했으나 실패. V키로 재등록하세요.")

    def start_collection(self, target=TARGET_SAMPLES):
        """샘플 수집 모드 시작 — 이미 학습됐으면 스킵."""
        if self.trained:
            return {
                "success": True,
                "message": f"이 기기는 이미 음성 등록됨 ({self._sample_count}샘플). 추가 등록 불필요.",
                "remaining": 0,
                "trained": True,
                "skipped": True,
            }
        if is_known_device() and self._try_retrain_from_samples():
            return {
                "success": True,
                "message": f"등록된 기기 — 저장된 샘플 {self._sample_count}개로 자동 복구 완료.",
                "remaining": 0,
                "trained": True,
                "skipped": True,
            }
        self.collecting = True
        self._collect_count = 0
        self._collect_target = target
        _log(f"샘플 수집 시작 ({target}회)")
        _log("준비되면 '자비스'라고 말씀해주세요...")
        return {
            "success": True,
            "message": f"음성 수집 모드 시작. '자비스'라고 {target}번 말씀해주세요.",
            "remaining": target,
        }

    def collect_sample(self, pa_instance):
        """한 개의 음성 샘플 녹음"""
        if not self.collecting:
            return None

        _log(f"샘플 {self._collect_count + 1}/{self._collect_target} 녹음 중...")

        # 녹음
        stream = pa_instance.open(
            format=FORMAT, channels=CHANNELS,
            rate=SAMPLE_RATE, input=True,
            frames_per_buffer=CHUNK,
        )

        frames = []
        for _ in range(int(SAMPLE_RATE / CHUNK * SAMPLE_DURATION)):
            data = stream.read(CHUNK, exception_on_overflow=False)
            frames.append(data)

        stream.stop_stream()
        stream.close()

        audio_data = b"".join(frames)

        # 음성 활동 확인 (무음이면 건너뛰기)
        count = len(audio_data) // 2
        shorts = struct.unpack(f"{count}h", audio_data)
        rms = int((sum(s * s for s in shorts) / count) ** 0.5)

        if rms < 300:
            _log("무음 감지 — 다시 시도해주세요")
            return {"success": False, "message": "소리가 너무 작습니다. 다시 말씀해주세요."}

        # WAV 저장
        self._collect_count += 1
        wav_path = str(SAMPLES_DIR / f"sample_{self._collect_count}.wav")
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(pa_instance.get_sample_size(FORMAT))
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(audio_data)

        # MFCC 추출 & 저장
        mfcc = extract_mfcc(audio_data)
        if mfcc is not None:
            self._templates.append(mfcc)

        remaining = self._collect_target - self._collect_count
        _log(f"샘플 {self._collect_count} 저장 완료! (남은: {remaining})")

        # 수집 완료 확인
        if self._collect_count >= self._collect_target:
            self.collecting = False
            self._train()
            return {
                "success": True,
                "message": f"모든 샘플 수집 완료! {self._collect_count}개로 학습 완료.",
                "remaining": 0,
                "trained": True,
            }

        return {
            "success": True,
            "message": f"샘플 {self._collect_count}/{self._collect_target} 저장.",
            "remaining": remaining,
        }

    def match(self, audio_data):
        """
        입력 오디오가 학습된 "자비스"와 일치하는지 확인
        Returns: (is_match: bool, confidence: float)
        """
        if not self.trained or not self._templates:
            return False, 0.0

        mfcc = extract_mfcc(audio_data)
        if mfcc is None:
            return False, 0.0

        # 모든 템플릿과 DTW 거리 계산
        distances = []
        for tmpl in self._templates:
            dist = _dtw_distance(mfcc, tmpl)
            distances.append(dist)

        min_dist = min(distances)
        avg_dist = sum(distances) / len(distances)

        # 거리 → 유사도 (0~1)
        confidence = max(0, 1 - (min_dist / (self._threshold * 2))) if self._threshold > 0 else 0

        is_match = min_dist < self._threshold
        return is_match, round(confidence, 2)

    def _train(self):
        """수집된 샘플로 학습 (임계값 설정)"""
        if len(self._templates) < MIN_SAMPLES:
            _log(f"샘플 부족 ({len(self._templates)}/{MIN_SAMPLES})")
            return

        # 템플릿 간 평균 거리 계산 → 임계값 설정
        dists = []
        for i in range(len(self._templates)):
            for j in range(i + 1, len(self._templates)):
                d = _dtw_distance(self._templates[i], self._templates[j])
                dists.append(d)

        if dists:
            avg = sum(dists) / len(dists)
            std = (sum((d - avg) ** 2 for d in dists) / len(dists)) ** 0.5
            self._threshold = avg + std * 2.0  # 평균 + 2σ
        else:
            self._threshold = 15.0  # 기본값

        self._sample_count = len(self._templates)
        self.trained = True
        self._save_profile()
        mark_device_enrolled(sample_count=self._sample_count)

        _log(f"학습 완료! 샘플={self._sample_count}, 임계값={self._threshold:.2f}")

    def _try_retrain_from_samples(self) -> bool:
        """voice_profile/samples/*.wav 에서 MFCC 재학습 — 등록 기기 재방문용."""
        wavs = sorted(SAMPLES_DIR.glob("sample_*.wav"))
        if len(wavs) < MIN_SAMPLES:
            return False
        templates = []
        for wav_path in wavs:
            try:
                with wave.open(str(wav_path), "rb") as wf:
                    frames = wf.readframes(wf.getnframes())
                mfcc = extract_mfcc(frames)
                if mfcc is not None:
                    templates.append(mfcc)
            except Exception:
                continue
        if len(templates) < MIN_SAMPLES:
            return False
        self._templates = templates
        self._train()
        if self.trained:
            _log(f"저장 샘플에서 프로필 자동 복구 ({self._sample_count}개)")
        return self.trained

    def _save_profile(self):
        """프로필 저장 (npz, allow_pickle 미사용)"""
        if self._templates:
            try:
                tmpls = np.stack(self._templates).astype(np.float64)
            except ValueError:
                # 템플릿 모양이 일정치 않으면 최소 길이로 자름 (저장 일관성)
                m = min(t.shape[0] for t in self._templates)
                tmpls = np.stack([t[:m] for t in self._templates]).astype(np.float64)
        else:
            tmpls = np.empty((0, 0, 0), dtype=np.float64)
        np.savez_compressed(
            PROFILE_FILE,
            templates=tmpls,
            threshold=np.float64(self._threshold),
            sample_count=np.int64(self._sample_count),
            trained=np.bool_(self.trained),
        )
        _log("프로필 저장 완료")

    def _load_profile(self):
        """프로필 로드 (npz, allow_pickle=False — 임의 객체 역직렬화 차단)"""
        if PROFILE_FILE.exists():
            try:
                with np.load(PROFILE_FILE, allow_pickle=False) as data:
                    t = data["templates"]
                    self._templates = [t[i] for i in range(t.shape[0])] if t.size else []
                    self._threshold = float(data["threshold"])
                    self._sample_count = int(data["sample_count"])
                    self.trained = bool(data["trained"])
                if self.trained:
                    _log(f"프로필 로드: {self._sample_count}개 샘플, 임계값={self._threshold:.2f}"
                         + (" (등록 기기)" if is_known_device() else ""))
            except Exception as e:
                _log(f"프로필 로드 실패: {e}")
        elif _LEGACY_PROFILE.exists():
            _log("레거시 profile.pkl 발견 — 보안상 자동 로드하지 않음. "
                 "필요 시 수동 마이그레이션 후 .npz로 재저장하세요.")

    def reset(self):
        """프로필 초기화"""
        self._templates = []
        self._threshold = 0
        self._sample_count = 0
        self.trained = False
        # 파일 삭제
        if PROFILE_FILE.exists():
            PROFILE_FILE.unlink()
        if _DEVICE_FILE.exists():
            _DEVICE_FILE.unlink()
        for f in SAMPLES_DIR.glob("*.wav"):
            f.unlink()
        _log("프로필 초기화 완료")
        return {"success": True, "message": "음성 프로필이 초기화되었습니다."}

    @property
    def status(self):
        return {
            "trained": self.trained,
            "samples": self._sample_count,
            "threshold": round(self._threshold, 2),
            "collecting": self.collecting,
        }
