#!/usr/bin/env python3
"""
ZFoldMacBridge — 폴드/모바일 → Mac JARVIS 알림 전달 (테스트·실사용)
──────────────────────────────────────────────────────────────────
Mac에서 JARVIS가 실행 중일 때, 폴드 알림을 이 스크립트로 전달하면
JARVIS가 읽어주고 voice_command는 명령으로 처리합니다.

사용 (Mac에서 테스트):
  ./.venv/bin/python scripts/zfold_bridge_notify.py --test

폴드(Termux)에서:
  curl -X POST http://<맥IP>:8770/api/mobile/notify \\
    -H "Content-Type: application/json" \\
    -H "X-Link-Token: <토큰>" \\
    -d '{"type":"notification","title":"KakaoTalk","body":"메시지 내용"}'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import requests


def _link_token() -> str:
    tok = os.getenv("JARVIS_LINK_TOKEN", "").strip()
    if tok:
        return tok
    f = ROOT / ".jarvis_link_token"
    if f.exists():
        return f.read_text(encoding="utf-8").strip()
    return ""


def _link_base() -> str:
    host = os.getenv("JARVIS_LINK_HOST", "127.0.0.1")
    port = os.getenv("JARVIS_LINK_PORT", "8770")
    return f"http://{host}:{port}"


def send_notification(title: str, body: str, msg_type: str = "notification") -> dict:
    token = _link_token()
    if not token:
        return {"success": False, "message": ".jarvis_link_token 없음 — JARVIS를 한 번 실행하세요."}
    url = f"{_link_base()}/api/mobile/notify"
    payload = {"type": msg_type, "title": title, "body": body}
    try:
        r = requests.post(
            url,
            json=payload,
            headers={"Content-Type": "application/json", "X-Link-Token": token},
            timeout=5,
        )
        if r.status_code == 401:
            return {"success": False, "message": "토큰 불일치 — .jarvis_link_token 확인"}
        r.raise_for_status()
        return {"success": True, "response": r.json(), "url": url}
    except Exception as e:
        return {"success": False, "message": str(e), "url": url}


def send_voice_command(text: str) -> dict:
    token = _link_token()
    if not token:
        return {"success": False, "message": "토큰 없음"}
    url = f"{_link_base()}/api/mobile/notify"
    try:
        r = requests.post(
            url,
            json={"type": "voice_command", "text": text},
            headers={"Content-Type": "application/json", "X-Link-Token": token},
            timeout=5,
        )
        r.raise_for_status()
        return {"success": True, "response": r.json()}
    except Exception as e:
        return {"success": False, "message": str(e)}


def write_termux_script():
    """폴드 Termux용 forwarder 스크립트 생성."""
    token = _link_token()
    host = os.getenv("JARVIS_LINK_LAN_HOST", "")  # 사용자가 맥 LAN IP 설정
    if not host:
        import socket
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            host = s.getsockname()[0]
            s.close()
        except Exception:
            host = "127.0.0.1"
    port = os.getenv("JARVIS_LINK_PORT", "8770")
    out = ROOT / "zfold_bridge" / "termux_notify.sh"
    out.parent.mkdir(parents=True, exist_ok=True)
    content = f'''#!/data/data/com.termux/files/usr/bin/bash
# ZFoldMacBridge — Termux에서 알림을 Mac JARVIS로 전달
# 사용: termux_notify.sh "앱이름" "알림 내용"
MAC_IP="{host}"
MAC_PORT="{port}"
TOKEN="{token}"
TITLE="${{1:-Notification}}"
BODY="${{2:-}}"
curl -sS -X POST "http://${{MAC_IP}}:${{MAC_PORT}}/api/mobile/notify" \\
  -H "Content-Type: application/json" \\
  -H "X-Link-Token: ${{TOKEN}}" \\
  -d "{{\\"type\\":\\"notification\\",\\"title\\":\\"${{TITLE}}\\",\\"body\\":\\"${{BODY}}\\"}}"
'''
    out.write_text(content, encoding="utf-8")
    out.chmod(0o755)
    return out


def main():
    p = argparse.ArgumentParser(description="ZFoldMacBridge → JARVIS")
    p.add_argument("--test", action="store_true", help="테스트 알림 전송")
    p.add_argument("--voice", metavar="TEXT", help="음성 명령 전송")
    p.add_argument("--title", default="ZFoldMacBridge")
    p.add_argument("--body", default="연동 테스트 — JARVIS가 이 알림을 읽어야 합니다.")
    p.add_argument("--write-termux", action="store_true", help="zfold_bridge/termux_notify.sh 생성")
    args = p.parse_args()

    if args.write_termux:
        path = write_termux_script()
        print(f"✓ Termux 스크립트: {path}")
        txt = path.read_text(encoding="utf-8")
        mac_ip = txt.split('MAC_IP="')[1].split('"')[0]
        print(f"  Mac LAN IP: {mac_ip}")
        return 0

    if args.voice:
        r = send_voice_command(args.voice)
    elif args.test:
        r = send_notification(args.title, args.body)
    else:
        p.print_help()
        return 0

    print(json.dumps(r, ensure_ascii=False, indent=2))
    return 0 if r.get("success") else 1


if __name__ == "__main__":
    raise SystemExit(main())
