"""
J.A.R.V.I.S — 타이핑 명령 팝업 입력창 (Tkinter)
──────────────────────────────────────────
jarvis_v2.py가 별도 프로세스로 띄운다. macOS에서 Tkinter는 메인 스레드를 요구해
OpenCV(cv2.imshow) 루프와 같은 프로세스에서 함께 돌리면 충돌하므로, 독립 프로세스로
실행해 한글 IME 입력을 안정적으로 받는다.

동작:
  · 입력 후 Enter(또는 '전송') → 한 줄을 stdout으로 출력(flush)하고 입력칸 비움 → 창 유지
  · 부모(jarvis_v2.py)는 stdout 라인을 읽어 명령 큐에 적재 → 음성과 동일 파이프라인 처리
  · '닫기'/Esc/창 닫기 → 프로세스 종료

macOS 가시성 핵심:
  · tk.Entry/Label 은 Aqua 테마에서 커스텀 색을 무시해 "흰 바탕에 흰 글자"로 안 보이는
    문제가 잦다. → 입력 영역을 큰 흰색 tk.Text(검은 글자·검은 커서)로 두어 타이핑한
    글자(한글 IME 포함)가 흰 화면에 그대로 실시간으로 보이게 한다.
"""
from __future__ import annotations

import sys


def main() -> int:
    try:
        import tkinter as tk
    except Exception as e:  # tkinter 미설치 환경
        sys.stderr.write(f"tkinter 사용 불가: {e}\n")
        return 2

    root = tk.Tk()
    root.title("J.A.R.V.I.S — 명령 입력")
    root.geometry("640x300")
    root.minsize(480, 240)

    bar_bg = "#0b0f14"
    bar_fg = "#e6f1ff"
    accent = "#1f7aec"

    # ── 상단 안내 바 ──
    header = tk.Frame(root, bg=bar_bg)
    header.pack(fill="x")
    tk.Label(
        header,
        text="J.A.R.V.I.S에게 보낼 명령을 입력하고 Enter (여러 번 가능)",
        bg=bar_bg, fg=bar_fg, anchor="w", font=("AppleSDGothicNeo", 13),
    ).pack(fill="x", padx=16, pady=10)

    # ── 입력 영역: 큰 흰색 박스 + 검은 글자/커서 (타이핑이 항상 보임) ──
    text = tk.Text(
        root, height=5, wrap="word",
        font=("AppleSDGothicNeo", 20),
        background="white", foreground="black",
        insertbackground="black",   # 커서(캐럿)도 검정 → 위치가 보임
        insertwidth=3,
        relief="flat", highlightthickness=2,
        highlightbackground="#c8d2dc", highlightcolor=accent,
        padx=14, pady=12,
    )
    text.pack(fill="both", expand=True, padx=16, pady=(12, 6))

    status = tk.Label(root, text="입력 대기 중…", bg=bar_bg, fg="#7f93a6",
                      anchor="w", font=("AppleSDGothicNeo", 11))
    status.pack(fill="x")

    def send(_event=None):
        content = text.get("1.0", "end").strip()
        if not content:
            return "break"
        # 명령은 한 줄로 정규화 (개행/연속 공백 → 단일 공백)
        line = " ".join(content.split())
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:
            root.destroy()
            return "break"
        text.delete("1.0", "end")
        preview = line if len(line) <= 50 else line[:50] + "…"
        status.config(text=f"✓ 전송됨: {preview}", fg=accent)
        return "break"   # Enter 기본 줄바꿈 억제 → 전송만

    def close(_event=None):
        root.destroy()

    # Enter=전송, Shift+Enter=줄바꿈
    text.bind("<Return>", send)
    text.bind("<Shift-Return>", lambda e: None)
    root.bind("<Escape>", close)

    btns = tk.Frame(root, bg=bar_bg)
    btns.pack(fill="x")
    tk.Button(btns, text="전송", command=send, default="active",
              highlightbackground=bar_bg, font=("AppleSDGothicNeo", 13, "bold"),
              padx=18, pady=4).pack(side="right", padx=(0, 16), pady=12)
    tk.Button(btns, text="닫기", command=close,
              highlightbackground=bar_bg, font=("AppleSDGothicNeo", 13),
              padx=14, pady=4).pack(side="right", padx=(0, 8), pady=12)

    root.protocol("WM_DELETE_WINDOW", close)

    # macOS: 창을 확실히 앞으로 + 입력 박스에 키보드 포커스. 생성 직후/지연 둘 다 시도.
    def _focus():
        try:
            root.lift()
            root.attributes("-topmost", True)
            root.after(500, lambda: root.attributes("-topmost", False))
            root.focus_force()
            text.focus_set()
        except Exception:
            pass
    _focus()
    root.after(200, _focus)

    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
