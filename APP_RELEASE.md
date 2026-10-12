# J.A.R.V.I.S — macOS 앱 출시 가이드

## 한 줄 요약

`Build Release App.command` 를 더블클릭하면 **JARVIS.app** 과 **DMG** 가 만들어집니다.

---

## 사용자 설치 (배포 받는 사람)

1. `JARVIS-3.2.0-mac.dmg` 를 연다  
2. `JARVIS.app` 을 **응용 프로그램** 으로 드래그  
3. 앱을 실행한다  
4. 첫 실행 설정 창에서 **Gemini API 키**(권장)를 입력한다  
5. 카메라·마이크 권한을 허용한다  

설정·로그 위치: `~/Library/Application Support/JARVIS/`

### Gatekeeper 경고가 뜰 때

시스템 설정 → 개인정보 보호 및 보안 → **그래도 열기**  
또는:

```bash
xattr -cr "/Applications/JARVIS.app"
```

---

## 개발자 — 출시 빌드

```bash
# FULL (권장): 현재 .venv 동봉 → 받는 사람이 바로 실행
./scripts/build_release_app.sh --full

# LITE: 소스만 (첫 실행 시 pip 설치, 인터넷 필요)
./scripts/build_release_app.sh --lite

# DMG만 만들고 Desktop 설치는 생략
./scripts/build_release_app.sh --full --no-install
```

결과물:

| 경로 | 설명 |
|------|------|
| `dist/JARVIS.app` | 배포용 앱 번들 |
| `dist/JARVIS-3.2.0-mac.dmg` | 드래그 설치 DMG |
| `~/Desktop/JARVIS.app` | 로컬 테스트용 복사본 |

### 앱 구조

```
JARVIS.app/Contents/
  MacOS/jarvis              ← 네이티브 진입점
  Resources/
    release_mode            ← 출시 모드 마커
    jarvis_launcher.sh
    app_setup_wizard.py     ← 첫 실행 API 키 UI
    app/                    ← 소스 코드
    venv/                   ← FULL 패키지 Python 환경
    bootstrap_venv.sh
```

실행 시 사용자 데이터는 앱 밖:

```
~/Library/Application Support/JARVIS/
  .env
  .venv/          ← FULL이면 동봉 venv를 여기로 복사
  .logs/
  audio_cache/
```

---

## 개발용 앱 (기존)

소스 폴더에 `.venv` 가 있는 상태에서의 로컬 런처:

```bash
./scripts/build_jarvis_app.sh --all
# 또는 Install JARVIS App.command
```

이 모드는 `project_root` 절대경로를 쓰며, **다른 Mac에 배포하면 깨집니다.**  
배포는 반드시 `build_release_app.sh` 를 사용하세요.

---

## Apple 공증 (선택, App Store/광역 배포)

Developer ID 인증서가 있으면:

```bash
codesign --force --deep --options runtime \
  --sign "Developer ID Application: YOUR NAME" \
  dist/JARVIS.app

xcrun notarytool submit dist/JARVIS-3.2.0-mac.dmg \
  --apple-id YOU@email.com --team-id TEAMID --password app-specific-password \
  --wait
```

현재 스크립트는 **ad-hoc 서명**만 합니다 (같은 Mac·직접 배포용).

---

## Windows

Windows는 아직 `.exe` 설치 파일이 없습니다.  
소스 ZIP + `Install JARVIS.bat` / `Start JARVIS.bat` 경로를 사용하세요.

```bash
./scripts/package-release.sh   # 소스 ZIP
```
