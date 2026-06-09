@echo off
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo 가상환경 없음. 먼저 Install JARVIS.bat 을 실행하세요.
  pause
  exit /b 1
)

if not exist ".env" (
  echo .env 없음. copy .env.example .env 후 API 키를 입력하세요.
  pause
  exit /b 1
)

call ".venv\Scripts\activate.bat"
python jarvis_v2.py
set EXITCODE=%ERRORLEVEL%
if %EXITCODE% neq 0 (
  echo.
  echo 종료 코드: %EXITCODE%
  pause
)
exit /b %EXITCODE%
