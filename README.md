# J.A.R.V.I.S — 설치 및 실행 가이드

```
╔══════════════════════════════════════════════════════════════╗
║     Just A Rather Very Intelligent System  ·  v3.2          ║
║     MediaPipe · Whisper · Gemini / Ollama · Iron Man HUD     ║
╚══════════════════════════════════════════════════════════════╝
```

---

## 🖥 macOS 앱으로 쓰기 (권장)

### 사용자
1. `JARVIS-3.2.0-mac.dmg` 열기 → `JARVIS.app` 을 응용 프로그램으로 드래그  
2. 더블클릭 실행 → 첫 설정에서 Gemini API 키 입력  
3. 카메라·마이크 권한 허용  

자세한 출시/배포: [APP_RELEASE.md](APP_RELEASE.md)

### 개발자 — 출시 앱·DMG 만들기
```bash
# 더블클릭
open "Build Release App.command"

# 또는 터미널
./scripts/build_release_app.sh --full
# → dist/JARVIS.app , dist/JARVIS-3.2.0-mac.dmg
```

로컬 개발용 런처만 필요할 때: `Install JARVIS App.command`

---

## 📁 프로젝트 구조

```
jarvis/
├── jarvis_v2.py       ← 메인 애플리케이션 (HUD + 도구)
├── Build Release App.command  ← 출시용 .app + DMG 빌드
├── requirements.txt   ← Python 의존성
├── .env.example       ← 환경 변수 템플릿
├── .env               ← 실제 API 키 (절대 공유 금지)
├── APP_RELEASE.md     ← 앱 출시 가이드
└── README.md
```

---

## ⚡ 1단계 — 시스템 사전 요구사항

### macOS
```bash
brew install portaudio ffmpeg
```

### Ubuntu / Debian
```bash
sudo apt update
sudo apt install -y python3-dev portaudio19-dev \
    libportaudio2 ffmpeg mpg123
```

### Windows
- Python 3.11+ 설치 (python.org)
- [PortAudio](http://www.portaudio.com/) 또는 pipwin 사용:
  ```bash
  pip install pipwin
  pipwin install pyaudio
  ```
- ffmpeg 설치: [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)

---

## 🐍 2단계 — 가상환경 & 패키지 설치

```bash
# 프로젝트 폴더로 이동
cd jarvis

# 가상환경 생성 (Python 3.10~3.12 권장)
python3 -m venv .venv

# 활성화
source .venv/bin/activate        # macOS / Linux
# .venv\Scripts\activate         # Windows

# 의존성 설치
pip install -r requirements.txt
```

> ⚠️ **Apple Silicon (M1/M2/M3)** 사용자:
> MediaPipe는 Python 3.12까지 지원합니다. 3.11 권장.

---

## 🔑 3단계 — API 키 설정

```bash
# 템플릿 복사
cp .env.example .env

# 편집기로 열기
nano .env       # 또는 code .env, vim .env
```

### API 키 발급 위치

| 서비스 | 발급 URL |
|--------|----------|
| OpenAI API Key | https://platform.openai.com/api-keys |
| Anthropic API Key | https://console.anthropic.com/ |
| Fish Audio API Key | https://fish.audio → Settings → API Keys |
| Fish Audio Voice ID | 아래 보이스 탐색 참고 |

### Fish Audio 보이스 탐색

```bash
# JARVIS 느낌의 남성 목소리 검색
python find_voice.py --query "male british calm"

# 한국어 남성 목소리 검색
python find_voice.py --query "korean male"

# 결과에서 마음에 드는 ID를 .env의 FISH_VOICE_ID에 붙여넣기
```

---

## 🚀 4단계 — 실행

```bash
# 가상환경이 활성화된 상태에서
python jarvis.py
```

---

## 🖐 제스처 조작법

| 제스처 | 동작 |
|--------|------|
| ✊ **주먹 쥐기** (FIST) | 음성 녹음 시작 |
| 🖐 **손바닥 펼치기** (OPEN_PALM) | 녹음 종료 → STT → LLM → TTS 처리 |
| **Q 키** | 프로그램 종료 |
| **C 키** | 대화 히스토리 초기화 |

> 제스처 인식까지 약 **12프레임(~0.5초)** 유지가 필요합니다 (오작동 방지).

---

## 🔁 시스템 흐름

```
웹캠 실시간 영상
       │
  MediaPipe 손동작 감지
       │  FIST 감지
       ▼
  🎙  PyAudio 녹음 시작
       │  PALM 감지
       ▼
  💾  WAV 파일 저장
       │
  📝  Whisper API (STT)
       │  텍스트 변환
       ▼
  🧠  GPT-4o / Claude API
       │  자비스 페르소나 답변
       ▼
  🔊  Fish Audio TTS
       │  MP3 파일 생성
       ▼
  ▶   오디오 재생
       │
       └──→  IDLE (다음 질문 대기)
```

---

## 🛠 트러블슈팅

### PyAudio 설치 실패
```bash
# macOS: brew install portaudio 후 재시도
pip install pyaudio

# Linux: apt install portaudio19-dev 후 재시도

# Windows: pipwin 사용
pip install pipwin && pipwin install pyaudio
```

### MediaPipe 오류 (Apple Silicon)
```bash
pip install mediapipe==0.10.14
```

### 웹캠이 열리지 않을 때
- macOS: 시스템 환경설정 → 개인 정보 보호 → 카메라 권한 허용
- Linux: `ls /dev/video*` 확인 후 코드의 `cv2.VideoCapture(0)` 인덱스 변경

### Fish Audio 응답 없음
- 네트워크 방화벽 확인
- API Key와 Voice ID가 올바른지 재확인
- `.env` 파일이 jarvis.py와 같은 폴더에 있는지 확인

---

## 📝 LLM 전환 방법

`.env` 파일에서:
```bash
# GPT-4o 사용 (기본)
LLM_PROVIDER=openai

# Claude Opus 사용
LLM_PROVIDER=claude
```

---

## 💡 커스터마이징 팁

- **자비스 성격 변경**: `jarvis.py` → `JARVIS_SYSTEM_PROMPT` 수정
- **최대 녹음 시간 변경**: `MAX_RECORD_SECONDS = 15` 값 수정
- **음성 모델 변경**: `synthesize_speech()` 함수의 `"format": "mp3"` 파라미터 조정
- **GPT 모델 변경**: `ask_jarvis_openai()` 함수의 `model="gpt-4o"` 수정

---

*"I am fully operational and all systems are functioning within normal parameters." — J.A.R.V.I.S*
