"""
J.A.R.V.I.S — 로컬 로그인

계정과 개인 정보는 이 맥의 data/accounts.json 에만 저장한다.
소스 코드와 기본 설정에는 넣지 않는다.
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
        data = json.loads(path.read_text(encoding="utf-8"))
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
        return "아이디는 2글자 이상으로 해 주세요."
    if len(password or "") < 4:
        return "비밀번호는 4글자 이상으로 해 주세요."
    name = (profile.get("display_name") or "").strip()
    if not name:
        return "부를 이름을 입력해 주세요."
    data = _load()
    if user_id in data["users"]:
        return "이미 있는 아이디입니다."
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
    """로그인한 프로필을 이번 실행의 환경 변수로만 올린다."""
    name = (profile.get("display_name") or "").strip() or "사용자"
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
    """로그인 창. 취소하면 앱을 시작하지 않는다."""
    try:
        import tkinter as tk
    except Exception as exc:
        sys.stderr.write(f"로그인 창을 열 수 없습니다: {exc}\n")
        raise SystemExit(1)

    result: dict = {}

    root = tk.Tk()
    root.title("J.A.R.V.I.S — 로그인")
    root.geometry("520x640")
    root.minsize(460, 560)
    bg = "#0b0f14"
    fg = "#e6f1ff"
    muted = "#8aa0b4"
    root.configure(bg=bg)

    header = tk.Frame(root, bg=bg)
    header.pack(fill="x")
    tk.Label(
        header, text="J.A.R.V.I.S", bg=bg, fg=fg,
        font=("AppleSDGothicNeo", 22, "bold"),
    ).pack(anchor="w", padx=22, pady=(18, 0))
    tk.Label(
        header,
        text="개인 정보는 이 로그인에만 저장됩니다. 앱 기본값에는 넣지 않습니다.",
        bg=bg, fg=muted, font=("AppleSDGothicNeo", 12),
    ).pack(anchor="w", padx=22, pady=(4, 12))

    mode = tk.StringVar(value="login" if has_accounts() else "register")
    body = tk.Frame(root, bg=bg)
    body.pack(fill="both", expand=True, padx=22)

    fields: dict[str, tk.Entry] = {}

    def add_field(key: str, label: str, secret: bool = False) -> None:
        tk.Label(body, text=label, bg=bg, fg=fg, anchor="w",
                 font=("AppleSDGothicNeo", 12)).pack(fill="x", pady=(8, 2))
        entry = tk.Entry(
            body, show="•" if secret else "",
            font=("AppleSDGothicNeo", 14),
            bg="white", fg="black", insertbackground="black",
            relief="flat", highlightthickness=1,
        )
        entry.pack(fill="x", ipady=6)
        fields[key] = entry

    status = tk.Label(root, text="", bg=bg, fg="#ffb020", anchor="w",
                      font=("AppleSDGothicNeo", 12))

    def rebuild() -> None:
        for child in body.winfo_children():
            child.destroy()
        fields.clear()
        registering = mode.get() == "register"
        add_field("user_id", "아이디")
        add_field("password", "비밀번호", secret=True)
        if registering:
            add_field("password2", "비밀번호 확인", secret=True)
            add_field("display_name", "부를 이름")
            add_field("email", "이메일")
            add_field("campus_email", "학교 메일 (선택)")
            add_field("phone", "전화번호 (선택)")
            add_field("city", "도시 (선택)")
        status.config(text="")
        fields["user_id"].focus_set()

    def submit(_event=None) -> None:
        user_id = fields["user_id"].get().strip()
        password = fields["password"].get()
        if mode.get() == "register":
            if password != fields["password2"].get():
                status.config(text="비밀번호가 서로 다릅니다.")
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
                status.config(text="아이디 또는 비밀번호가 맞지 않습니다.")
                return
        if profile:
            apply_profile(profile)
            result["ok"] = True
            root.destroy()

    def toggle() -> None:
        mode.set("login" if mode.get() == "register" else "register")
        rebuild()
        switch.config(text="계정이 있으면 로그인" if mode.get() == "register" else "처음이면 계정 만들기")

    rebuild()
    status.pack(fill="x", padx=22, pady=(8, 0))

    buttons = tk.Frame(root, bg=bg)
    buttons.pack(fill="x", padx=22, pady=16)
    tk.Button(
        buttons, text="들어가기", command=submit,
        font=("AppleSDGothicNeo", 13, "bold"), padx=16, pady=6,
    ).pack(side="right")
    switch = tk.Button(
        buttons,
        text="계정이 있으면 로그인" if mode.get() == "register" else "처음이면 계정 만들기",
        command=toggle, font=("AppleSDGothicNeo", 12), padx=10, pady=6,
    )
    switch.pack(side="left")
    root.bind("<Return>", submit)
    root.protocol("WM_DELETE_WINDOW", lambda: (result.clear(), root.destroy()))

    def _focus() -> None:
        try:
            root.lift()
            root.attributes("-topmost", True)
            root.after(400, lambda: root.attributes("-topmost", False))
            root.focus_force()
        except Exception:
            pass

    _focus()
    root.after(150, _focus)
    root.mainloop()
    if not result.get("ok"):
        raise SystemExit(0)
