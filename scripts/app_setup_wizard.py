#!/usr/bin/env python3
"""
J.A.R.V.I.S — 출시 앱 첫 실행 설정 마법사
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Application Support 에 .env / .venv 를 준비하고 API 키를 받는다.

사용:
  python scripts/app_setup_wizard.py --data-dir "~/Library/Application Support/JARVIS" \\
      --code-dir "/path/to/JARVIS.app/Contents/Resources/app"
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path


REQUIRED_KEYS = ("GEMINI_API_KEY",)
OPTIONAL_KEYS = (
    "TAVILY_API_KEY",
    "OPENAI_API_KEY",
    "X_BEARER_TOKEN",
    "FINNHUB_API_KEY",
)


def _default_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "JARVIS"


def _ensure_dirs(data: Path) -> None:
    for name in (
        ".logs", ".cache", ".matplotlib", "audio_cache",
        "data", "faces", "voice_profile",
    ):
        (data / name).mkdir(parents=True, exist_ok=True)


def _write_env(data: Path, values: dict[str, str], example: Path | None) -> Path:
    env_path = data / ".env"
    lines: list[str] = []
    if example and example.is_file():
        lines = example.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines = [
            "# J.A.R.V.I.S — 앱 설정",
            "LLM_PROVIDER=gemini",
            "JARVIS_USER_NAME=사용자",
            "GEMINI_FLASH_MODEL=gemini-3.5-flash",
            "GEMINI_PRO_MODEL=gemini-1.5-pro",
        ]

    def upsert(key: str, val: str) -> None:
        nonlocal lines
        prefix = f"{key}="
        for i, line in enumerate(lines):
            if line.startswith(prefix) or line.startswith(f"# {prefix}"):
                lines[i] = f"{key}={val}"
                return
        lines.append(f"{key}={val}")

    for k, v in values.items():
        if v.strip():
            upsert(k, v.strip())

    # 앱 모드 기본값
    upsert("JARVIS_APP_LAUNCH", "1")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    try:
        os.chmod(env_path, 0o600)
    except OSError:
        pass
    return env_path


def _python_candidates() -> list[str]:
    cands = []
    for name in ("python3.11", "python3.12", "python3.10", "python3"):
        p = shutil.which(name)
        if p:
            cands.append(p)
    return cands or [sys.executable]


def ensure_venv(data: Path, code: Path, log_cb=None) -> Path:
    """DATA/.venv 생성 + requirements 설치. 반환: python 경로."""
    venv = data / ".venv"
    py = venv / "bin" / "python"
    req = code / "requirements.txt"
    log = log_cb or (lambda m: print(m, flush=True))

    if not py.is_file():
        log("가상환경 생성 중…")
        created = False
        for base in _python_candidates():
            try:
                subprocess.check_call([base, "-m", "venv", str(venv)])
                created = True
                break
            except Exception as e:
                log(f"venv 실패 ({base}): {e}")
        if not created:
            raise RuntimeError("Python venv 생성 실패. Python 3.10+ 를 설치하세요.")

    pip = [str(py), "-m", "pip"]
    log("pip 업그레이드…")
    subprocess.check_call(pip + ["install", "--upgrade", "pip", "wheel", "setuptools"])
    if req.is_file():
        log("의존성 설치 중 (수 분 소요)…")
        subprocess.check_call(pip + ["install", "-r", str(req)])
    log("설치 완료")
    return py


def _cli_wizard(data: Path, code: Path, skip_deps: bool) -> int:
    print("\n╔══════════════════════════════════════╗")
    print("║   J.A.R.V.I.S  첫 실행 설정          ║")
    print("╚══════════════════════════════════════╝\n")
    print(f"데이터 폴더: {data}")
    _ensure_dirs(data)

    values: dict[str, str] = {}
    print("필수 API 키를 입력하세요 (Enter=건너뛰기, Gemini 권장).\n")
    for key in REQUIRED_KEYS + OPTIONAL_KEYS:
        cur = ""
        env_existing = data / ".env"
        if env_existing.is_file():
            for line in env_existing.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.startswith(f"{key}="):
                    cur = line.split("=", 1)[1]
                    break
        hint = f" [{cur[:8]}…]" if cur and len(cur) > 8 else (f" [{cur}]" if cur else "")
        val = input(f"  {key}{hint}: ").strip()
        values[key] = val or cur

    if not any(values.get(k) for k in REQUIRED_KEYS) and not (data / ".env").is_file():
        print("⚠ Gemini 키 없이도 무료(Ollama) 모드로 시작할 수 있습니다.")
        values.setdefault("JARVIS_FREE_ONLY", "1")

    example = code / ".env.example"
    _write_env(data, values, example if example.is_file() else None)
    print(f"✓ .env 저장: {data / '.env'}")

    if not skip_deps:
        try:
            ensure_venv(data, code)
        except Exception as e:
            print(f"✗ 의존성 설치 실패: {e}")
            return 1
    print("\n설정 완료. JARVIS.app 을 다시 실행하세요.\n")
    return 0


def _gui_wizard(data: Path, code: Path, skip_deps: bool) -> int:
    try:
        import tkinter as tk
        from tkinter import ttk, messagebox, scrolledtext
    except Exception:
        return _cli_wizard(data, code, skip_deps)

    _ensure_dirs(data)
    example = code / ".env.example"

    root = tk.Tk()
    root.title("J.A.R.V.I.S 설정")
    root.geometry("520x560")
    root.resizable(False, False)

    frm = ttk.Frame(root, padding=16)
    frm.pack(fill="both", expand=True)

    ttk.Label(frm, text="J.A.R.V.I.S", font=("Helvetica", 22, "bold")).pack(anchor="w")
    ttk.Label(
        frm,
        text="출시 앱 첫 설정 — API 키를 입력한 뒤 설치를 완료하세요.",
        wraplength=480,
    ).pack(anchor="w", pady=(4, 12))

    fields: dict[str, tk.Entry] = {}
    for key in REQUIRED_KEYS + OPTIONAL_KEYS:
        row = ttk.Frame(frm)
        row.pack(fill="x", pady=3)
        label = key + (" *" if key in REQUIRED_KEYS else "")
        ttk.Label(row, text=label, width=22).pack(side="left")
        ent = ttk.Entry(row, show="•", width=40)
        ent.pack(side="left", fill="x", expand=True)
        fields[key] = ent

    free_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        frm,
        text="API 키 없이 무료 모드(Ollama)로 시작",
        variable=free_var,
    ).pack(anchor="w", pady=(8, 4))

    log_box = scrolledtext.ScrolledText(frm, height=10, state="disabled", wrap="word")
    log_box.pack(fill="both", expand=True, pady=8)

    status = ttk.Label(frm, text=f"저장 위치: {data}")
    status.pack(anchor="w")

    def log(msg: str):
        log_box.configure(state="normal")
        log_box.insert("end", msg + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")
        root.update_idletasks()

    btn_row = ttk.Frame(frm)
    btn_row.pack(fill="x", pady=8)

    def on_save_and_install():
        values = {k: e.get().strip() for k, e in fields.items()}
        if free_var.get():
            values["JARVIS_FREE_ONLY"] = "1"
        elif not any(values.get(k) for k in REQUIRED_KEYS):
            if not messagebox.askyesno(
                "확인",
                "Gemini API 키가 없습니다. 무료 모드로 계속할까요?",
            ):
                return
            values["JARVIS_FREE_ONLY"] = "1"

        try:
            _write_env(data, values, example if example.is_file() else None)
            log(f"✓ .env 저장 → {data / '.env'}")
        except Exception as e:
            messagebox.showerror("오류", f".env 저장 실패: {e}")
            return

        if skip_deps and (data / ".venv" / "bin" / "python").is_file():
            messagebox.showinfo("완료", "설정이 저장되었습니다. 앱을 다시 실행하세요.")
            root.destroy()
            return

        def worker():
            try:
                ensure_venv(data, code, log_cb=log)
                root.after(0, lambda: (
                    messagebox.showinfo("완료", "설치가 끝났습니다. JARVIS를 다시 실행하세요."),
                    root.destroy(),
                ))
            except Exception as e:
                root.after(0, lambda: messagebox.showerror("설치 실패", str(e)))

        for child in btn_row.winfo_children():
            child.configure(state="disabled")
        threading.Thread(target=worker, daemon=True).start()

    ttk.Button(btn_row, text="저장 후 설치", command=on_save_and_install).pack(side="right")
    ttk.Button(btn_row, text="취소", command=root.destroy).pack(side="right", padx=8)

    # 기존 .env 프리필
    env_path = data / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                if k in fields and v:
                    fields[k].insert(0, v)

    root.mainloop()
    return 0 if (data / ".env").is_file() else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="JARVIS app first-run setup")
    p.add_argument("--data-dir", default=str(_default_data_dir()))
    p.add_argument("--code-dir", default=".")
    p.add_argument("--cli", action="store_true", help="GUI 대신 터미널 입력")
    p.add_argument("--skip-deps", action="store_true", help="venv/pip 생략 (.env만)")
    args = p.parse_args(argv)

    data = Path(args.data_dir).expanduser().resolve()
    code = Path(args.code_dir).expanduser().resolve()
    data.mkdir(parents=True, exist_ok=True)

    if args.cli or os.environ.get("JARVIS_SETUP_CLI") == "1":
        return _cli_wizard(data, code, args.skip_deps)
    return _gui_wizard(data, code, args.skip_deps)


if __name__ == "__main__":
    sys.exit(main())
