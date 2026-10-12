#!/usr/bin/env osascript
-- ZFoldMacBridge Mac 테스트 — JARVIS 실행 중 더블클릭으로 알림 전송
set root to do shell script "cd " & quoted form of (POSIX path of (path to me as text)) & "/.. && pwd"
set py to root & "/.venv/bin/python"
do shell script quoted form of py & " " & quoted form of (root & "/scripts/zfold_bridge_notify.py") & " --test"
