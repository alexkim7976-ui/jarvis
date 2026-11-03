"""
J.A.R.V.I.S — 로컬 로그인 (5개 언어 선택 포함: English / 中文 / 한국어 / 日本語 / Español)

계정과 개인 정보는 이 맥의 data/accounts.json 에만 저장한다.
선택한 언어는 data/language.json 및 .env 에 저장되어 다음 실행에도 유지된다.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from pathlib import Path

from i18n import (
    LANGUAGE_META,
    SUPPORTED_LANGS,
    get_lang,
    get_lang_meta,
    normalize_lang,
    set_lang,
    tr,
)


def _runtime_root() -> Path:
    """JARVIS_DATA_DIR 우선 (출시 앱의 ~/Library/Application Support/JARVIS), 없으면 프로젝트 루트."""
    dd = (os.getenv("JARVIS_DATA_DIR") or "").strip()
    if dd:
        p = Path(dd).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    root = Path(os.getenv("JARVIS_PROJECT_ROOT") or Path(__file__).resolve().parent)
    return root


def accounts_path() -> Path:
    path = _runtime_root() / "data" / "accounts.json"
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
    lang_code = normalize_lang(profile.get("lang") or get_lang())
    user_id = (user_id or "").strip()
    if not user_id:
        return tr("login_err_userid_empty", lang_code)
    if len(user_id) < 2:
        return tr("login_err_id_short", lang_code)
    if not (password or ""):
        return tr("login_err_pw_empty", lang_code)
    if len(password or "") < 4:
        return tr("login_err_pw_short", lang_code)
    name = (profile.get("display_name") or "").strip()
    if not name:
        return tr("login_err_name_empty", lang_code)
    data = _load()
    if user_id in data["users"]:
        return tr("login_err_id_exists", lang_code)
    salt, digest = _hash_password(password)
    data["users"][user_id] = {
        "salt": salt,
        "password_hash": digest,
        "display_name": name,
        "email": (profile.get("email") or "").strip(),
        "campus_email": (profile.get("campus_email") or "").strip(),
        "phone": (profile.get("phone") or "").strip(),
        "city": (profile.get("city") or "").strip(),
        "lang": lang_code,
    }
    _save(data)
    return None


def register_and_login(user_id: str, password: str, password2: str, profile: dict) -> tuple[dict | None, str | None]:
    """Validate signup fields, create hashed account, and immediately authenticate + apply profile."""
    lang_code = normalize_lang(profile.get("lang") or get_lang())
    uid = (user_id or "").strip()
    if not uid:
        return None, tr("login_err_userid_empty", lang_code)
    if not (password or ""):
        return None, tr("login_err_pw_empty", lang_code)
    if password != (password2 or ""):
        return None, tr("login_err_pw_mismatch", lang_code)
    err = register_account(uid, password, {**profile, "lang": lang_code})
    if err:
        return None, err
    user = authenticate(uid, password)
    if user is None:
        return None, tr("login_err_invalid", lang_code)
    apply_profile(user, selected_lang=lang_code)
    return user, None


def authenticate(user_id: str, password: str) -> dict | None:
    user = _load()["users"].get((user_id or "").strip())
    if not user:
        return None
    if not _verify(password or "", user.get("salt", ""), user.get("password_hash", "")):
        return None
    return user


def apply_profile(profile: dict, *, selected_lang: str | None = None) -> None:
    lang_code = normalize_lang(selected_lang or profile.get("lang") or get_lang())
    set_lang(lang_code, persist=True, update_env_file=True)

    meta = get_lang_meta(lang_code)
    raw_name = (profile.get("display_name") or "").strip()
    default_titles = {m["default_honorific"] for m in LANGUAGE_META.values()} | {"사용자", "User", ""}
    name = meta["default_honorific"] if (not raw_name or raw_name in default_titles) else raw_name

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
    """로그인 창 — Bloomie Sign-in glass 카드 + 5개 언어 선택기 (EN / 中文 / 한국어 / 日本語 / Español)."""
    try:
        import tkinter as tk
    except Exception as exc:
        sys.stderr.write(f"Could not open sign-in window: {exc}\n")
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

    CARD_W = 384
    result: dict = {}
    refs: list = []
    fields: dict[str, tk.Entry] = {}

    # 기본 언어: 저장된 언어가 없으면 영어(en)
    initial_lang = get_lang()

    root = tk.Tk()
    root.title(tr("login_window_title", initial_lang))
    root.geometry("470x740")
    root.minsize(430, 620)
    root.configure(bg=UI_BG)

    shell = tk.Frame(root, bg=UI_BG)
    shell.pack(fill="both", expand=True)
    attach_window_gradient(root, shell, refs)

    outer = tk.Frame(shell, bg=UI_BG)
    outer.pack(expand=True, fill="both", padx=16, pady=16)

    hero = tk.Frame(outer, bg=UI_BG)
    hero.pack(pady=(0, 12))
    if pil_available():
        from login_glass_style import ImageTk

        icon_img = icon_in_liquid_glass(default_icon_path(), size=50)
        if icon_img is not None:
            refs.append(ImageTk.PhotoImage(icon_img))
            tk.Label(hero, image=refs[-1], bg=UI_BG, bd=0).pack()

    card_slot = tk.Frame(outer, bg=UI_BG)
    card_slot.pack()

    status = tk.Label(outer, text="", bg=UI_BG, fg="#ffb020", font=tk_korean_font(11))
    status.pack(pady=(8, 0))

    mode = tk.StringVar(value="login" if has_accounts() else "register")
    selected_lang = tk.StringVar(value=initial_lang)
    switch_lbl: tk.Label | None = None

    def _card_h() -> int:
        return 450 if mode.get() == "login" else 640

    def _select_language(code: str) -> None:
        saved_vals = {k: ent.get() for k, ent in fields.items()}
        selected_lang.set(code)
        set_lang(code, persist=True, update_env_file=True)
        root.title(tr("login_window_title", code))
        rebuild(restore=saved_vals)

    def _build_lang_selector(parent: tk.Widget, cur_lang: str) -> None:
        tk.Label(
            parent,
            text=tr("login_language_label", cur_lang),
            bg=SIGNIN_INNER,
            fg=SIGNIN_MUTED,
            font=tk_korean_font(10),
            anchor="w",
        ).pack(fill="x", pady=(6, 4))
        bar = tk.Frame(parent, bg=SIGNIN_INNER)
        bar.pack(fill="x", pady=(0, 4))
        for code in SUPPORTED_LANGS:
            label = LANGUAGE_META[code]["label"]
            active = code == cur_lang
            bg_col = "#2f86bc" if active else "#3a4452"
            fg_col = "#ffffff" if active else "#cbd5e1"
            btn = tk.Label(
                bar,
                text=label,
                bg=bg_col,
                fg=fg_col,
                font=tk_korean_font(10, "bold" if active else None),
                padx=8,
                pady=5,
                cursor="hand2",
                relief="flat",
            )
            btn.pack(side="left", expand=True, fill="x", padx=2)
            btn.bind("<Button-1>", lambda _e, c=code: _select_language(c))

    def _field(parent, key: str, placeholder: str, secret: bool = False, initial: str = "") -> None:
        tk.Label(
            parent, text=placeholder, bg=SIGNIN_INNER, fg=SIGNIN_MUTED,
            font=tk_korean_font(11), anchor="w",
        ).pack(fill="x", pady=(10, 3))
        row, ent = create_signin_field(parent, refs, secret=secret, width=CARD_W - 56)
        row.pack(fill="x")
        if initial:
            ent.insert(0, initial)
        fields[key] = ent

    def rebuild(restore: dict[str, str] | None = None) -> None:
        nonlocal switch_lbl
        restore = restore or {}
        cur_lang = selected_lang.get()
        for child in card_slot.winfo_children():
            child.destroy()
        fields.clear()

        card_host, body = create_signin_card_host(
            card_slot, refs, width=CARD_W, height=_card_h(),
        )
        card_host.pack()

        title = tr("login_title_signin", cur_lang) if mode.get() == "login" else tr("login_title_register", cur_lang)
        _title_f = tk_korean_font(20)
        tk.Label(
            body, text=title, bg=SIGNIN_INNER, fg=SIGNIN_FG,
            font=(_title_f[0], _title_f[1], "bold"),
        ).pack(pady=(6, 2))
        subtitle_txt = (
            tr("login_first_run_banner", cur_lang)
            if (mode.get() == "register" and not has_accounts())
            else tr("login_subtitle", cur_lang)
        )
        subtitle_col = "#7dd3fc" if (mode.get() == "register" and not has_accounts()) else SIGNIN_MUTED
        tk.Label(
            body,
            text=subtitle_txt,
            bg=SIGNIN_INNER, fg=subtitle_col, font=tk_korean_font(10),
            wraplength=CARD_W - 40, justify="center",
        ).pack(pady=(0, 4))

        pad = tk.Frame(body, bg=SIGNIN_INNER, padx=28)
        pad.pack(fill="both", expand=True)

        # 5-Language Selector (English / 中文 / 한국어 / 日本語 / Español)
        _build_lang_selector(pad, cur_lang)

        if mode.get() == "register":
            _field(pad, "user_id", tr("login_field_userid", cur_lang), initial=restore.get("user_id", ""))
            _field(pad, "password", tr("login_field_password", cur_lang), secret=True, initial=restore.get("password", ""))
            _field(pad, "password2", tr("login_field_password2", cur_lang), secret=True, initial=restore.get("password2", ""))
            _field(pad, "display_name", tr("login_field_display_name", cur_lang), initial=restore.get("display_name", ""))
            _field(pad, "email", tr("login_field_email", cur_lang), initial=restore.get("email", ""))
            _field(pad, "campus_email", tr("login_field_campus_email", cur_lang), initial=restore.get("campus_email", ""))
            _field(pad, "phone", tr("login_field_phone", cur_lang), initial=restore.get("phone", ""))
            _field(pad, "city", tr("login_field_city", cur_lang), initial=restore.get("city", ""))
        else:
            _field(pad, "user_id", tr("login_field_userid", cur_lang), initial=restore.get("user_id", ""))
            _field(pad, "password", tr("login_field_password", cur_lang), secret=True, initial=restore.get("password", ""))

        btn_wrap = tk.Frame(body, bg=SIGNIN_INNER, padx=28)
        btn_wrap.pack(fill="x", pady=(8, 4))
        create_primary_button(
            btn_wrap,
            tr("login_title_signin", cur_lang) if mode.get() == "login" else tr("login_title_register", cur_lang),
            submit, refs, width=CARD_W - 56,
        ).pack(fill="x")

        foot = tk.Frame(body, bg=SIGNIN_INNER, padx=28)
        foot.pack(fill="x", pady=(6, 12))
        hint = (
            tr("login_switch_to_signin", cur_lang)
            if mode.get() == "register"
            else tr("login_switch_to_register", cur_lang)
        )
        switch_lbl = tk.Label(
            foot,
            text=hint,
            bg="#354152",
            fg="#7dd3fc",
            font=tk_korean_font(11, "bold"),
            padx=12,
            pady=6,
            cursor="hand2",
            relief="flat",
        )
        switch_lbl.pack(fill="x")
        switch_lbl.bind("<Button-1>", lambda _e: toggle())

        status.config(text="")
        root.update_idletasks()
        focus_tk_window(root, fields.get("user_id"), topmost_ms=300)

    def submit(_event=None) -> None:
        cur_lang = selected_lang.get()
        user_id = fields["user_id"].get().strip()
        password = fields["password"].get()
        if mode.get() == "register":
            profile, err = register_and_login(
                user_id,
                password,
                fields["password2"].get(),
                {
                    "display_name": fields["display_name"].get(),
                    "email": fields["email"].get(),
                    "campus_email": fields["campus_email"].get(),
                    "phone": fields["phone"].get(),
                    "city": fields["city"].get(),
                    "lang": cur_lang,
                },
            )
            if err or profile is None:
                status.config(text=err or tr("login_err_invalid", cur_lang))
                return
            result["ok"] = True
            root.destroy()
            return

        if not user_id or not password:
            status.config(text=tr("login_err_empty_login", cur_lang))
            return
        profile = authenticate(user_id, password)
        if profile is None:
            status.config(text=tr("login_err_invalid", cur_lang))
            return
        try:
            data = _load()
            if user_id in data.get("users", {}):
                data["users"][user_id]["lang"] = cur_lang
                _save(data)
        except Exception:
            pass
        apply_profile(profile, selected_lang=cur_lang)
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
