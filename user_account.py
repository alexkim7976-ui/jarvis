"""
J.A.R.V.I.S — 로컬 로그인

계정과 개인 정보는 이 맥의 data/accounts.json 에만 저장한다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path


def accounts_path() -> Path:
    root = Path(os.getenv("JARVIS_PROJECT_ROOT") or Path(__file__).resolve().parent)
    path = root / "data" / "accounts.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load() -> dict:
    path = accounts_path()
    if not path.is_file():
        return {"users": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError):
        return {"users": {}}
    if not isinstance(data, dict) or not isinstance(data.get("users"), dict):
        return {"users": {}}
    return data


def _save(data: dict) -> None:
    path = accounts_path()
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return salt.hex(), digest.hex()


def _verify(password: str, salt_hex: str, digest_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    _, digest = _hash_password(password, salt)
    return hmac.compare_digest(digest, digest_hex)


def has_accounts() -> bool:
    return bool(_load().get("users"))


def register_account(user_id: str, password: str, profile: dict) -> str | None:
    user_id = (user_id or "").strip()
    if len(user_id) < 2:
        return "User ID must be at least 2 characters."
    if len(password or "") < 4:
        return "Password must be at least 4 characters."
    name = (profile.get("display_name") or "").strip()
    if not name:
        return "Please enter a display name."
    data = _load()
    if user_id in data["users"]:
        return "This User ID already exists."
    salt, digest = _hash_password(password)
    data["users"][user_id] = {
        "salt": salt,
        "password_hash": digest,
        "display_name": name,
        "email": (profile.get("email") or "").strip(),
        "campus_email": (profile.get("campus_email") or "").strip(),
        "phone": (profile.get("phone") or "").strip(),
        "city": (profile.get("city") or "").strip(),
    }
    _save(data)
    return None


def authenticate(user_id: str, password: str) -> dict | None:
    user = _load()["users"].get((user_id or "").strip())
    if not user:
        return None
    if not _verify(password or "", user.get("salt", ""), user.get("password_hash", "")):
        return None
    return user


def apply_profile(profile: dict) -> None:
    raw_name = (profile.get("display_name") or "").strip()
    name = "Sir" if (not raw_name or raw_name in ("사용자", "김태현")) else raw_name
    email = (profile.get("email") or "").strip()
    campus = (profile.get("campus_email") or "").strip() or email
    phone = (profile.get("phone") or "").strip()
    city = (profile.get("city") or "").strip()
    os.environ["JARVIS_USER_NAME"] = name
    if campus:
        os.environ["CAMPUS_GMAIL_ACCOUNT"] = campus
        os.environ["CAMPUS_COURSE_SENDER"] = campus
    else:
        os.environ.pop("CAMPUS_GMAIL_ACCOUNT", None)
        os.environ.pop("CAMPUS_COURSE_SENDER", None)
    if phone:
        os.environ["CAMPUS_CALL_TO"] = phone
    else:
        os.environ.pop("CAMPUS_CALL_TO", None)
    if city:
        os.environ["JARVIS_DEVICE_LOCATION"] = city
    else:
        os.environ.pop("JARVIS_DEVICE_LOCATION", None)


def ensure_login() -> None:
    """로그인 창 — Bloomie Sign-in glass 카드."""
    try:
        import tkinter as tk
    except Exception as exc:
        sys.stderr.write(f"로그인 창을 열 수 없습니다: {exc}\n")
        raise SystemExit(1)

    from login_glass_style import (
        SIGNIN_FG,
        SIGNIN_INNER,
        SIGNIN_MUTED,
        UI_BG,
        attach_window_gradient,
        create_primary_button,
        create_signin_card_host,
        create_signin_field,
        default_icon_path,
        focus_tk_window,
        icon_in_liquid_glass,
        pil_available,
        tk_korean_font,
    )

    CARD_W = 368
    result: dict = {}
    refs: list = []
    fields: dict[str, tk.Entry] = {}

    root = tk.Tk()
    root.title("J.A.R.V.I.S — Sign In")
    root.geometry("460x680")
    root.minsize(420, 560)
    root.configure(bg=UI_BG)

    shell = tk.Frame(root, bg=UI_BG)
    shell.pack(fill="both", expand=True)
    attach_window_gradient(root, shell, refs)

    outer = tk.Frame(shell, bg=UI_BG)
    outer.pack(expand=True, fill="both", padx=16, pady=20)

    hero = tk.Frame(outer, bg=UI_BG)
    hero.pack(pady=(0, 16))
    if pil_available():
        from login_glass_style import ImageTk

        icon_img = icon_in_liquid_glass(default_icon_path(), size=52)
        if icon_img is not None:
            refs.append(ImageTk.PhotoImage(icon_img))
            tk.Label(hero, image=refs[-1], bg=UI_BG, bd=0).pack()

    card_slot = tk.Frame(outer, bg=UI_BG)
    card_slot.pack()

    status = tk.Label(outer, text="", bg=UI_BG, fg="#ffb020", font=tk_korean_font(11))
    status.pack(pady=(10, 0))

    mode = tk.StringVar(value="login" if has_accounts() else "register")
    switch_lbl: tk.Label | None = None

    def _card_h() -> int:
        return 360 if mode.get() == "login" else 540

    def _field(parent, key: str, placeholder: str, secret: bool = False) -> None:
        tk.Label(
            parent, text=placeholder, bg=SIGNIN_INNER, fg=SIGNIN_MUTED,
            font=tk_korean_font(11), anchor="w",
        ).pack(fill="x", pady=(12, 4))
        row, ent = create_signin_field(parent, refs, secret=secret, width=CARD_W - 56)
        row.pack(fill="x")
        fields[key] = ent

    def rebuild() -> None:
        nonlocal switch_lbl
        for child in card_slot.winfo_children():
            child.destroy()
        fields.clear()

        card_host, body = create_signin_card_host(
            card_slot, refs, width=CARD_W, height=_card_h(),
        )
        card_host.pack()

        title = "Sign In" if mode.get() == "login" else "Create Account"
        _title_f = tk_korean_font(20)
        tk.Label(
            body, text=title, bg=SIGNIN_INNER, fg=SIGNIN_FG,
            font=(_title_f[0], _title_f[1], "bold"),
        ).pack(pady=(8, 4))
        tk.Label(
            body,
            text="Stored locally in accounts.json on this Mac only.",
            bg=SIGNIN_INNER, fg=SIGNIN_MUTED, font=tk_korean_font(10),
        ).pack(pady=(0, 8))

        pad = tk.Frame(body, bg=SIGNIN_INNER, padx=28)
        pad.pack(fill="both", expand=True)

        if mode.get() == "register":
            _field(pad, "user_id", "User ID")
            _field(pad, "password", "Password", secret=True)
            _field(pad, "password2", "Confirm Password", secret=True)
            _field(pad, "display_name", "Display Name")
            _field(pad, "email", "Email")
            _field(pad, "campus_email", "Campus Email (Optional)")
            _field(pad, "phone", "Phone Number (Optional)")
            _field(pad, "city", "City (Optional)")
        else:
            _field(pad, "user_id", "User ID")
            _field(pad, "password", "Password", secret=True)

        btn_wrap = tk.Frame(body, bg=SIGNIN_INNER, padx=28)
        btn_wrap.pack(fill="x", pady=(8, 6))
        create_primary_button(
            btn_wrap, "Sign In" if mode.get() == "login" else "Create Account",
            submit, refs, width=CARD_W - 56,
        ).pack(fill="x")

        foot = tk.Frame(body, bg=SIGNIN_INNER)
        foot.pack(pady=(4, 12))
        hint = (
            "Already have an account? Sign In"
            if mode.get() == "register"
            else "New here? Create Account"
        )
        switch_lbl = tk.Label(
            foot, text=hint, bg=SIGNIN_INNER, fg=SIGNIN_MUTED,
            font=tk_korean_font(11), cursor="hand2",
        )
        switch_lbl.pack()
        switch_lbl.bind("<Button-1>", lambda _e: toggle())

        status.config(text="")
        root.update_idletasks()
        focus_tk_window(root, fields.get("user_id"), topmost_ms=300)

    def submit(_event=None) -> None:
        user_id = fields["user_id"].get().strip()
        password = fields["password"].get()
        if mode.get() == "register":
            if password != fields["password2"].get():
                status.config(text="Passwords do not match.")
                return
            err = register_account(user_id, password, {
                "display_name": fields["display_name"].get(),
                "email": fields["email"].get(),
                "campus_email": fields["campus_email"].get(),
                "phone": fields["phone"].get(),
                "city": fields["city"].get(),
            })
            if err:
                status.config(text=err)
                return
            profile = authenticate(user_id, password)
        else:
            profile = authenticate(user_id, password)
            if profile is None:
                status.config(text="Invalid User ID or password.")
                return
        if profile:
            apply_profile(profile)
            result["ok"] = True
            root.destroy()

    def toggle() -> None:
        mode.set("login" if mode.get() == "register" else "register")
        rebuild()

    rebuild()
    root.bind("<Return>", submit)
    root.protocol("WM_DELETE_WINDOW", lambda: (result.clear(), root.destroy()))
    root.mainloop()
    if not result.get("ok"):
        raise SystemExit(0)
