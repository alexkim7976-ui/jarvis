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


REQUIRED_KEYS: tuple[str, ...] = ()  # 출시 기본: 무료(Ollama) — API 키 선택
OPTIONAL_KEYS = (
    "GEMINI_API_KEY",
    "TAVILY_API_KEY",
    "OPENAI_API_KEY",
    "X_BEARER_TOKEN",
    "FINNHUB_API_KEY",
)

PERF_CHOICES = (
    ("balanced", "균형 (출시 권장)"),
    ("eco", "절전"),
    ("quality", "고품질"),
)


def _default_data_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "JARVIS"


def _ensure_dirs(data: Path) -> None:
    for name in (
        ".logs", ".cache", ".matplotlib", "audio_cache",
        "data", "faces", "voice_profile",
    ):
        (data / name).mkdir(parents=True, exist_ok=True)


def _release_template(code: Path) -> Path | None:
    for candidate in (
        code / "scripts" / "release.env.example",
        code.parent / "scripts" / "release.env.example",
    ):
        if candidate.is_file():
            return candidate
    return None


def _write_env(data: Path, values: dict[str, str], example: Path | None) -> Path:
    env_path = data / ".env"
    lines: list[str] = []
    if example and example.is_file():
        lines = example.read_text(encoding="utf-8", errors="replace").splitlines()
    else:
        lines = [
            "# J.A.R.V.I.S — 앱 설정",
            "LLM_PROVIDER=ollama",
            "JARVIS_FREE_ONLY=1",
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

    upsert("JARVIS_APP_LAUNCH", "1")
    if values.get("JARVIS_FREE_ONLY", "").strip() in ("1", "true", "yes"):
        upsert("JARVIS_FREE_ONLY", "1")
        upsert("LLM_PROVIDER", "ollama")
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


def _apply_perf_and_ollama_log(data: Path, code: Path, perf: str, log_fn=print) -> None:
    import sys

    root_pkg = code if (code / "jarvis_v2.py").is_file() else code.parent
    if str(root_pkg) not in sys.path:
        sys.path.insert(0, str(root_pkg))
    scripts_dir = root_pkg / "scripts"
    if scripts_dir.is_dir() and str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    try:
        from performance_presets import apply_preset

        apply_preset(perf, data / ".env")
        log_fn(f"✓ 성능 프로필: {perf}")
    except Exception as e:
        log_fn(f"⚠ 성능 프로필 적용: {e}")

    try:
        from ollama_setup import diagnose_ollama, pull_instructions, try_open_ollama_app
    except ImportError:
        log_fn("⚠ ollama_setup 모듈을 찾을 수 없습니다.")
        return

    if not diagnose_ollama()["api_ok"]:
        log_fn("Ollama.app 기동 시도…")
        try_open_ollama_app()
    diag = diagnose_ollama()
    for line in diag["lines"]:
        log_fn(line)
    model = "qwen2.5:7b-instruct"
    env_f = data / ".env"
    if env_f.is_file():
        for ln in env_f.read_text(encoding="utf-8", errors="replace").splitlines():
            if ln.startswith("OLLAMA_MODEL="):
                model = ln.split("=", 1)[1].strip() or model
                break
    have = " ".join(diag.get("models") or [])
    if diag["api_ok"] and model not in have and model.split(":")[0] not in have:
        log_fn("\n권장 모델 다운로드:")
        log_fn(pull_instructions(model))


def _cli_wizard(data: Path, code: Path, skip_deps: bool) -> int:
    print("\n╔══════════════════════════════════════╗")
    print("║   J.A.R.V.I.S  첫 실행 설정          ║")
    print("╚══════════════════════════════════════╝\n")
    print(f"데이터 폴더: {data}")
    _ensure_dirs(data)

    values: dict[str, str] = {"JARVIS_FREE_ONLY": "1"}
    print("출시 앱 — 기본은 무료(Ollama) 모드입니다. API 키는 선택(Enter=건너뛰기).\n")
    perf_in = input("  성능 프로필 [1=절전 2=균형 3=고품질] (기본 2): ").strip() or "2"
    perf_map = {"1": "eco", "2": "balanced", "3": "quality"}
    perf = perf_map.get(perf_in, "balanced")

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

    if values.get("GEMINI_API_KEY", "").strip():
        values["LLM_PROVIDER"] = "gemini"
        values["JARVIS_FREE_ONLY"] = "0"

    template = _release_template(code) or (code / ".env.example")
    _write_env(data, values, template if template.is_file() else None)
    print(f"✓ .env 저장: {data / '.env'}")
    _apply_perf_and_ollama_log(data, code, perf)

    if not skip_deps:
        try:
            ensure_venv(data, code)
        except Exception as e:
            print(f"✗ 의존성 설치 실패: {e}")
            return 1
    print("\n설정 완료. JARVIS.app 을 다시 실행하세요.\n")
    return 0


_FIELD_LABELS = {
    "GEMINI_API_KEY": "Gemini API",
    "TAVILY_API_KEY": "Tavily (웹 검색)",
    "OPENAI_API_KEY": "OpenAI (STT·API)",
    "X_BEARER_TOKEN": "X / Twitter",
    "FINNHUB_API_KEY": "Finnhub",
}


def _gui_wizard(data: Path, code: Path, skip_deps: bool) -> int:
    try:
        import tkinter as tk
        from tkinter import messagebox, scrolledtext
    except Exception:
        return _cli_wizard(data, code, skip_deps)

    code_str = str(code.resolve())
    if code_str not in sys.path:
        sys.path.insert(0, code_str)

    from login_glass_style import (
        UI_BG,
        UI_FG,
        UI_LABEL,
        UI_MUTED,
        attach_window_gradient,
        create_glass_button,
        create_glass_card_host,
        create_glass_entry,
        default_icon_path,
        focus_tk_window,
        glass_log_panel_image,
        glass_panel_image,
        icon_in_liquid_glass,
        pil_available,
        tk_korean_font,
    )

    _ensure_dirs(data)
    template = _release_template(code) or (code / ".env.example")
    if not (data / ".env").is_file() and template.is_file():
        _write_env(data, {"JARVIS_FREE_ONLY": "1"}, template)

    root = tk.Tk()
    root.title("J.A.R.V.I.S 설정")
    root.geometry("580x720")
    root.minsize(540, 600)
    refs: list = []
    shell = tk.Frame(root, bg=UI_BG)
    shell.pack(fill="both", expand=True)
    attach_window_gradient(root, shell, refs)

    pad = tk.Frame(shell, bg=UI_BG, padx=22, pady=16)
    pad.pack(fill="both", expand=True)

    head = tk.Frame(pad, bg=UI_BG)
    head.pack(fill="x", pady=(0, 12))
    if pil_available():
        from login_glass_style import ImageTk

        icon_img = icon_in_liquid_glass(default_icon_path(), size=64)
        if icon_img is not None:
            refs.append(ImageTk.PhotoImage(icon_img))
            tk.Label(head, image=refs[-1], bg=UI_BG, bd=0).pack(side="left", padx=(0, 14))
    title_col = tk.Frame(head, bg=UI_BG)
    title_col.pack(side="left", fill="x", expand=True)
    tk.Label(
        title_col, text="J.A.R.V.I.S", bg=UI_BG, fg=UI_FG,
        font=("AppleSDGothicNeo", 24, "bold"),
    ).pack(anchor="w")
    tk.Label(
        title_col,
        text="Apple Liquid Glass · 무료(Ollama) 기본 · API 키는 선택",
        bg=UI_BG, fg=UI_MUTED, font=("AppleSDGothicNeo", 12), justify="left",
    ).pack(anchor="w", pady=(4, 0))

    card_outer, _ = create_glass_card_host(pad, refs, width=520, height=480, bg=UI_BG)
    card_outer.pack(fill="both", expand=True, pady=(0, 10))
    card = tk.Frame(card_outer, bg=UI_BG)
    card.place(relx=0.055, rely=0.035, relwidth=0.89, relheight=0.93)

    tk.Label(
        card, text="성능 프로필", bg=UI_BG, fg=UI_LABEL,
        font=("AppleSDGothicNeo", 12), anchor="w",
    ).pack(fill="x", pady=(4, 4))
    perf_var = tk.StringVar(value=PERF_CHOICES[0][1])
    if pil_available():
        from login_glass_style import ImageTk

        _pp = glass_panel_image(480, 44, radius=18)
        perf_host = tk.Label(card, bd=0, bg=UI_BG)
        if _pp is not None:
            refs.append(ImageTk.PhotoImage(_pp))
            perf_host.configure(image=refs[-1])
    else:
        perf_host = tk.Frame(card, bg=UI_BG)
    perf_host.pack(fill="x", pady=(0, 8))
    perf_menu = tk.OptionMenu(perf_host, perf_var, *[label for _, label in PERF_CHOICES])
    perf_menu.configure(
        bg="#eef6fc", fg="#1e5474", activebackground="#8ec8ec",
        highlightthickness=0, bd=0, font=("AppleSDGothicNeo", 12),
    )
    perf_menu["menu"].configure(bg="#eef6fc", fg="#1e5474")
    perf_menu.place(relx=0.04, rely=0.12, relwidth=0.92, relheight=0.76)

    fields: dict[str, tk.Entry] = {}
    for key in REQUIRED_KEYS + OPTIONAL_KEYS:
        lbl = _FIELD_LABELS.get(key, key) + (" *" if key in REQUIRED_KEYS else " (선택)")
        tk.Label(
            card, text=lbl, bg=UI_BG, fg=UI_LABEL,
            font=("AppleSDGothicNeo", 11), anchor="w",
        ).pack(fill="x", pady=(8, 3))
        secret = "KEY" in key or "TOKEN" in key
        row_host, ent = create_glass_entry(card, refs, bg=UI_BG, width=480, secret=secret)
        row_host.pack(fill="x")
        fields[key] = ent

    free_var = tk.BooleanVar(value=True)
    tk.Checkbutton(
        card, text="무료 모드 (Ollama) — 출시 권장",
        variable=free_var, bg=UI_BG, fg=UI_FG, selectcolor=UI_BG,
        activebackground=UI_BG, activeforeground=UI_FG,
        font=("AppleSDGothicNeo", 12),
    ).pack(anchor="w", pady=(12, 4))

    log_host = tk.Label(card, bg=UI_BG, bd=0)
    if pil_available():
        from login_glass_style import ImageTk

        log_img = glass_log_panel_image(480, 100, radius=14)
        if log_img is not None:
            refs.append(ImageTk.PhotoImage(log_img))
            log_host.configure(image=refs[-1])
    log_host.pack(fill="x", pady=(8, 0))
    log_box = scrolledtext.ScrolledText(
        log_host, height=5, state="disabled", wrap="word",
        bg="#141c26", fg="#c5dce8", font=("Menlo", 10),
        relief="flat", highlightthickness=0, bd=0,
    )
    log_box.place(relx=0.03, rely=0.08, relwidth=0.94, relheight=0.84)

    tk.Label(
        pad, text=f"저장: {data}", bg=UI_BG, fg=UI_MUTED,
        font=("AppleSDGothicNeo", 10), anchor="w",
    ).pack(fill="x")

    def log(msg: str):
        log_box.configure(state="normal")
        log_box.insert("end", msg + "\n")
        log_box.see("end")
        log_box.configure(state="disabled")
        root.update_idletasks()

    btn_row = tk.Frame(pad, bg=UI_BG)
    btn_row.pack(fill="x", pady=(12, 0))

    def _mark_done():
        try:
            (data / ".setup_done").write_text("1\n", encoding="utf-8")
        except OSError:
            pass

    def on_save_and_install():
        values = {k: e.get().strip() for k, e in fields.items()}
        if free_var.get():
            values["JARVIS_FREE_ONLY"] = "1"
            values["LLM_PROVIDER"] = "ollama"
        elif values.get("GEMINI_API_KEY", "").strip():
            values["JARVIS_FREE_ONLY"] = "0"
            values["LLM_PROVIDER"] = "gemini"
        else:
            if not messagebox.askyesno(
                "확인",
                "API 키 없이 무료(Ollama) 모드로 계속할까요?",
            ):
                return
            values["JARVIS_FREE_ONLY"] = "1"
            values["LLM_PROVIDER"] = "ollama"

        perf_label = perf_var.get()
        perf_key = "balanced"
        for k, lbl in PERF_CHOICES:
            if lbl == perf_label:
                perf_key = k
                break

        try:
            _write_env(data, values, template if template.is_file() else None)
            log(f"✓ .env 저장 → {data / '.env'}")
            _apply_perf_and_ollama_log(data, code, perf_key, log_fn=log)
        except Exception as e:
            messagebox.showerror("오류", f".env 저장 실패: {e}")
            return

        _mark_done()
        if skip_deps and (data / ".venv" / "bin" / "python").is_file():
            messagebox.showinfo("완료", "설정이 저장되었습니다. 앱을 다시 실행하세요.")
            root.destroy()
            return

        def worker():
            try:
                ensure_venv(data, code, log_cb=log)
                _apply_perf_and_ollama_log(data, code, perf_key, log_fn=log)
                root.after(0, lambda: (
                    messagebox.showinfo(
                        "완료",
                        "설치가 끝났습니다.\nOllama pull 안내는 로그를 확인하세요.\nJARVIS를 다시 실행하세요.",
                    ),
                    root.destroy(),
                ))
            except Exception as e:
                root.after(0, lambda: messagebox.showerror("설치 실패", str(e)))

        for child in btn_row.winfo_children():
            child.configure(state="disabled")
        threading.Thread(target=worker, daemon=True).start()

    def on_skip():
        _mark_done()
        log("기본 설정으로 건너뜀 — JARVIS 시작 가능")
        root.destroy()

    create_glass_button(
        btn_row, "건너뛰기", on_skip, refs, bg=UI_BG, tone="mist", outline=True,
    ).pack(side="right", padx=(0, 10))
    create_glass_button(
        btn_row, "저장 후 계속", on_save_and_install, refs, bg=UI_BG, tone="deep",
    ).pack(side="right")

    def _focus():
        first = next(iter(fields.values()), None)
        focus_tk_window(root, first, topmost_ms=300)

    # 기존 .env 프리필
    env_path = data / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                if k in fields and v:
                    fields[k].insert(0, v)

    root.update_idletasks()
    _focus()
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
    # scripts/app_setup_wizard.py 로 실행 시 import 경로가 code 루트여야 함
    code_str = str(code)
    if code_str not in sys.path:
        sys.path.insert(0, code_str)

    if args.cli or os.environ.get("JARVIS_SETUP_CLI") == "1":
        return _cli_wizard(data, code, args.skip_deps)
    return _gui_wizard(data, code, args.skip_deps)


if __name__ == "__main__":
    sys.exit(main())
