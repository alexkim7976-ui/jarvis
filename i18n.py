"""
J.A.R.V.I.S — Multi-Language Support (English, 中文, 한국어, 日本語, Español)
──────────────────────────────────────────────────────────────────────────────
Central i18n configuration, persistence, voice/STT/TTS locale mapping,
and translation table for HUD, console, voice greetings, and LLM prompts.

Default language: English ("en").
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SUPPORTED_LANGS: tuple[str, ...] = ("en", "zh", "ko", "ja", "es")
DEFAULT_LANG = "en"

LANGUAGE_META: dict[str, dict[str, Any]] = {
    "en": {
        "code": "en",
        "label": "English",
        "native_name": "English",
        "llm_language": "English",
        "stt_lang": "en",
        "tts_locale": "en-GB",
        "tts_voice": "Daniel",
        "tts_fallbacks": ["Daniel", "Samantha", "Alex", "Karen", "Moira"],
        "default_honorific": "Sir",
        "wake_words": "jarvis,hey jarvis,자비스야,자비스",
    },
    "zh": {
        "code": "zh",
        "label": "中文",
        "native_name": "Simplified Chinese (简体中文)",
        "llm_language": "Simplified Chinese (简体中文)",
        "stt_lang": "zh",
        "tts_locale": "zh-CN",
        "tts_voice": "Tingting",
        "tts_fallbacks": ["Tingting", "Meijia", "Sinji", "Eddy", "Flo"],
        "default_honorific": "先生",
        "wake_words": "jarvis,hey jarvis,贾维斯,你好贾维斯,자비스",
    },
    "ko": {
        "code": "ko",
        "label": "한국어",
        "native_name": "Korean (한국어)",
        "llm_language": "Korean (자연스러운 한국어 존댓말)",
        "stt_lang": "ko",
        "tts_locale": "ko-KR",
        "tts_voice": "Yuna",
        "tts_fallbacks": ["Yuna", "Suhyun", "Eddy", "Flo"],
        "default_honorific": "대표님",
        "wake_words": "자비스야,자비스,jarvis,hey jarvis,쟈비스,자바스,저비스",
    },
    "ja": {
        "code": "ja",
        "label": "日本語",
        "native_name": "Japanese (日本語)",
        "llm_language": "Japanese (丁寧で自然な日本語)",
        "stt_lang": "ja",
        "tts_locale": "ja-JP",
        "tts_voice": "Kyoko",
        "tts_fallbacks": ["Kyoko", "Otoya", "Eddy", "Flo"],
        "default_honorific": "サー",
        "wake_words": "jarvis,hey jarvis,ジャービス,ねえジャービス,자비스",
    },
    "es": {
        "code": "es",
        "label": "Español",
        "native_name": "Spanish (Español)",
        "llm_language": "Spanish (Español natural y cortés)",
        "stt_lang": "es",
        "tts_locale": "es-ES",
        "tts_voice": "Mónica",
        "tts_fallbacks": ["Mónica", "Monica", "Paulina", "Jorge", "Eddy"],
        "default_honorific": "Señor",
        "wake_words": "jarvis,hey jarvis,oye jarvis,자비스",
    },
}


def normalize_lang(raw: str | None) -> str:
    if not raw:
        return DEFAULT_LANG
    s = str(raw).strip().lower()
    if not s:
        return DEFAULT_LANG
    for code in SUPPORTED_LANGS:
        if s == code or s.startswith(f"{code}-") or s.startswith(f"{code}_"):
            return code
    alias_map = {
        "english": "en", "eng": "en",
        "chinese": "zh", "zh-cn": "zh", "zh-tw": "zh", "中文": "zh", "汉语": "zh", "중국어": "zh",
        "korean": "ko", "kor": "ko", "kr": "ko", "한국어": "ko", "한글": "ko",
        "japanese": "ja", "jp": "ja", "jpn": "ja", "日本語": "ja", "일본어": "ja",
        "spanish": "es", "esp": "es", "español": "es", "espanol": "es", "스페인어": "es",
    }
    return alias_map.get(s, DEFAULT_LANG)


def _settings_path() -> Path:
    data_dir = (os.getenv("JARVIS_DATA_DIR") or "").strip()
    if data_dir:
        base = Path(data_dir).expanduser() / "data"
    else:
        root = Path(os.getenv("JARVIS_PROJECT_ROOT") or Path(__file__).resolve().parent)
        base = root / "data"
    base.mkdir(parents=True, exist_ok=True)
    return base / "language.json"


def load_saved_lang() -> str | None:
    """Return saved language code from data/language.json if present, else None."""
    p = _settings_path()
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("lang"):
                return normalize_lang(str(data["lang"]))
        except Exception:
            pass
    return None


def get_lang() -> str:
    """Resolve active language code. Priority: JARVIS_LANG env -> data/language.json -> 'en'."""
    env_lang = (os.getenv("JARVIS_LANG") or "").strip()
    if env_lang:
        return normalize_lang(env_lang)
    saved = load_saved_lang()
    if saved:
        return saved
    return DEFAULT_LANG


def get_lang_meta(lang: str | None = None) -> dict[str, Any]:
    code = normalize_lang(lang or get_lang())
    return LANGUAGE_META.get(code, LANGUAGE_META[DEFAULT_LANG])


def set_lang(lang: str, *, persist: bool = True, update_env_file: bool = True) -> str:
    """Apply language immediately to process environment and optionally persist to disk."""
    code = normalize_lang(lang)
    meta = LANGUAGE_META[code]

    os.environ["JARVIS_LANG"] = code
    os.environ["JARVIS_STT_LANG"] = meta["stt_lang"]
    os.environ["JARVIS_TTS_LANG"] = meta["tts_locale"]
    os.environ["JARVIS_MAC_VOICE"] = meta["tts_voice"]

    cur_name = (os.getenv("JARVIS_USER_NAME") or "").strip()
    default_titles = {m["default_honorific"] for m in LANGUAGE_META.values()} | {"사용자", "User", ""}
    if cur_name in default_titles:
        os.environ["JARVIS_USER_NAME"] = meta["default_honorific"]

    if persist:
        try:
            p = _settings_path()
            p.write_text(
                json.dumps({"lang": code, "label": meta["label"]}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass
        except Exception:
            pass

    if update_env_file:
        try:
            from performance_presets import upsert_env_keys
            upsert_env_keys({
                "JARVIS_LANG": code,
                "JARVIS_STT_LANG": meta["stt_lang"],
                "JARVIS_TTS_LANG": meta["tts_locale"],
                "JARVIS_MAC_VOICE": meta["tts_voice"],
            })
        except Exception:
            pass

    try:
        import sys as _sys
        if "speech_style" in _sys.modules:
            _ss = _sys.modules["speech_style"]
            if hasattr(_ss, "refresh_language_style"):
                _ss.refresh_language_style()
    except Exception:
        pass

    return code


def cycle_lang(step: int = 1) -> str:
    """Cycle to the next supported language and return the new language code."""
    cur = get_lang()
    idx = SUPPORTED_LANGS.index(cur) if cur in SUPPORTED_LANGS else 0
    nxt = SUPPORTED_LANGS[(idx + step) % len(SUPPORTED_LANGS)]
    return set_lang(nxt, persist=True, update_env_file=True)


def interpret_language_command(text: str) -> str | None:
    """Detect voice/typed commands to switch language (EN / 中文 / 한국어 / 日本語 / Español)."""
    import re
    raw = (text or "").strip()
    if not raw:
        return None
    patterns = (
        ("en", r"(?:switch|change|set)\s+(?:the\s+)?(?:language\s+)?to\s+english|영어로\s*(?:바꿔|변경|설정|말해)|영어\s*모드|切换(?:为|到)?英语|英語に(?:切り替え|変更)|cambiar\s+a\s+ingl[eé]s"),
        ("zh", r"(?:switch|change|set)\s+(?:the\s+)?(?:language\s+)?to\s+chinese|중국어로\s*(?:바꿔|변경|설정|말해)|중국어\s*모드|切换(?:为|到)?中文|用中文(?:说|回答)|中国語に(?:切り替え|変更)|cambiar\s+a\s+chino"),
        ("ko", r"(?:switch|change|set)\s+(?:the\s+)?(?:language\s+)?to\s+korean|한국어로\s*(?:바꿔|변경|설정|말해)|한국어\s*모드|한글로\s*(?:바꿔|변경)|切换(?:为|到)?韩语|韓国語に(?:切り替え|変更)|cambiar\s+a\s+coreano"),
        ("ja", r"(?:switch|change|set)\s+(?:the\s+)?(?:language\s+)?to\s+japanese|일본어로\s*(?:바꿔|변경|설정|말해)|일본어\s*모드|切换(?:为|到)?日语|日本語に(?:切り替え|変更)|日本語モード|cambiar\s+a\s+japon[eé]s"),
        ("es", r"(?:switch|change|set)\s+(?:the\s+)?(?:language\s+)?to\s+spanish|스페인어로\s*(?:바꿔|변경|설정|말해)|스페인어\s*모드|切换(?:为|到)?西班牙语|スペイン語に(?:切り替え|変更)|cambiar\s+a\s+espa[ñn]ol"),
    )
    for code, pat in patterns:
        if re.search(pat, raw, re.I):
            set_lang(code, persist=True, update_env_file=True)
            return tr("lang_switched_msg", code)
    return None


def apply_saved_or_default_lang() -> str:
    """Ensure process env variables match saved language (or default 'en' if none saved)."""
    saved = load_saved_lang()
    code = saved or normalize_lang(os.getenv("JARVIS_LANG") or DEFAULT_LANG)
    return set_lang(code, persist=False, update_env_file=False)


# ──────────────────────────────────────────────────────────────────────────────
#  Translation Table (5 Languages: en, zh, ko, ja, es)
# ──────────────────────────────────────────────────────────────────────────────
TRANSLATIONS: dict[str, dict[str, str]] = {
    # ── Login Window ──
    "login_window_title": {
        "en": "J.A.R.V.I.S — Sign In",
        "zh": "J.A.R.V.I.S — 登录",
        "ko": "J.A.R.V.I.S — 로그인",
        "ja": "J.A.R.V.I.S — サインイン",
        "es": "J.A.R.V.I.S — Iniciar sesión",
    },
    "login_title_signin": {
        "en": "Sign In",
        "zh": "登录",
        "ko": "로그인",
        "ja": "サインイン",
        "es": "Iniciar sesión",
    },
    "login_title_register": {
        "en": "Create Account",
        "zh": "创建账户",
        "ko": "계정 만들기",
        "ja": "アカウント作成",
        "es": "Crear cuenta",
    },
    "login_subtitle": {
        "en": "Stored locally in accounts.json on this Mac only.",
        "zh": "仅保存在此 Mac 本地的 accounts.json 中。",
        "ko": "계정 정보는 이 맥의 accounts.json 에만 저장됩니다.",
        "ja": "この Mac 内の accounts.json にのみローカル保存されます。",
        "es": "Guardado localmente en accounts.json solo en esta Mac.",
    },
    "login_language_label": {
        "en": "Language / 语言 / 언어 / 言語 / Idioma",
        "zh": "语言 / Language",
        "ko": "언어 / Language",
        "ja": "言語 / Language",
        "es": "Idioma / Language",
    },
    "login_field_userid": {
        "en": "User ID",
        "zh": "用户 ID",
        "ko": "아이디",
        "ja": "ユーザー ID",
        "es": "ID de usuario",
    },
    "login_field_password": {
        "en": "Password",
        "zh": "密码",
        "ko": "비밀번호",
        "ja": "パスワード",
        "es": "Contraseña",
    },
    "login_field_password2": {
        "en": "Confirm Password",
        "zh": "确认密码",
        "ko": "비밀번호 확인",
        "ja": "パスワード確認",
        "es": "Confirmar contraseña",
    },
    "login_field_display_name": {
        "en": "Display Name",
        "zh": "称呼 / 姓名",
        "ko": "부를 이름 (호칭)",
        "ja": "表示名（呼び名）",
        "es": "Nombre para mostrar",
    },
    "login_field_email": {
        "en": "Email",
        "zh": "电子邮箱",
        "ko": "이메일",
        "ja": "メールアドレス",
        "es": "Correo electrónico",
    },
    "login_field_campus_email": {
        "en": "Campus Email (Optional)",
        "zh": "学校邮箱（可选）",
        "ko": "학교 이메일 (선택)",
        "ja": "大学メール（任意）",
        "es": "Correo universitario (Opcional)",
    },
    "login_field_phone": {
        "en": "Phone Number (Optional)",
        "zh": "电话号码（可选）",
        "ko": "전화번호 (선택)",
        "ja": "電話番号（任意）",
        "es": "Número de teléfono (Opcional)",
    },
    "login_field_city": {
        "en": "City (Optional)",
        "zh": "所在城市（可选）",
        "ko": "도시 (선택)",
        "ja": "都市（任意）",
        "es": "Ciudad (Opcional)",
    },
    "login_switch_to_signin": {
        "en": "Already have an account? Sign In →",
        "zh": "已有账户？点击登录 →",
        "ko": "이미 계정이 있으신가요? 로그인 →",
        "ja": "アカウントをお持ちですか？サインイン →",
        "es": "¿Ya tienes cuenta? Inicia sesión →",
    },
    "login_switch_to_register": {
        "en": "Don't have an account? Sign Up →",
        "zh": "没有账户？注册新账户 →",
        "ko": "계정이 없나요? 회원가입 →",
        "ja": "アカウントがありませんか？新規登録 →",
        "es": "¿No tienes cuenta? Regístrate →",
    },
    "login_first_run_banner": {
        "en": "Welcome! No account found — please create your account to get started.",
        "zh": "欢迎！未检测到账户，请先注册一个新账户以开始使用。",
        "ko": "환영합니다! 등록된 계정이 없습니다. 시작하려면 회원가입을 진행해 주세요.",
        "ja": "ようこそ！アカウントが見つかりません。新規登録して開始してください。",
        "es": "¡Bienvenido! No hay cuentas registradas. Crea una cuenta para comenzar.",
    },
    "login_err_empty_login": {
        "en": "Please enter both User ID and password.",
        "zh": "请输入用户 ID 和密码。",
        "ko": "아이디와 비밀번호를 모두 입력해 주세요.",
        "ja": "ユーザー ID とパスワードの両方を入力してください。",
        "es": "Por favor, introduce tu ID de usuario y contraseña.",
    },
    "login_err_userid_empty": {
        "en": "Please enter a User ID.",
        "zh": "请输入用户 ID。",
        "ko": "아이디를 입력해 주세요.",
        "ja": "ユーザー ID を入力してください。",
        "es": "Por favor, introduce un ID de usuario.",
    },
    "login_err_pw_empty": {
        "en": "Please enter a password.",
        "zh": "请输入密码。",
        "ko": "비밀번호를 입력해 주세요.",
        "ja": "パスワードを入力してください。",
        "es": "Por favor, introduce una contraseña.",
    },
    "login_err_pw_mismatch": {
        "en": "Passwords do not match.",
        "zh": "两次输入的密码不一致。",
        "ko": "비밀번호가 일치하지 않습니다.",
        "ja": "パスワードが一致しません。",
        "es": "Las contraseñas no coinciden.",
    },
    "login_err_invalid": {
        "en": "Invalid User ID or password.",
        "zh": "用户 ID 或密码无效。",
        "ko": "아이디 또는 비밀번호가 올바르지 않습니다.",
        "ja": "ユーザー ID またはパスワードが正しくありません。",
        "es": "ID de usuario o contraseña no válidos.",
    },
    "login_err_id_short": {
        "en": "User ID must be at least 2 characters.",
        "zh": "用户 ID 至少需要 2 个字符。",
        "ko": "아이디는 2글자 이상으로 해 주세요.",
        "ja": "ユーザー ID は2文字以上で入力してください。",
        "es": "El ID de usuario debe tener al menos 2 caracteres.",
    },
    "login_err_pw_short": {
        "en": "Password must be at least 4 characters.",
        "zh": "密码至少需要 4 个字符。",
        "ko": "비밀번호는 4글자 이상으로 해 주세요.",
        "ja": "パスワードは4文字以上で入力してください。",
        "es": "La contraseña debe tener al menos 4 caracteres.",
    },
    "login_err_name_empty": {
        "en": "Please enter a display name.",
        "zh": "请输入称呼或姓名。",
        "ko": "부를 이름을 입력해 주세요.",
        "ja": "表示名を入力してください。",
        "es": "Por favor, introduce un nombre.",
    },
    "login_err_id_exists": {
        "en": "This User ID already exists.",
        "zh": "该用户 ID 已存在。",
        "ko": "이미 존재하는 아이디입니다.",
        "ja": "このユーザー ID は既に使用されています。",
        "es": "Este ID de usuario ya existe.",
    },

    # ── HUD & Banners ──
    "hud_analyzing": {
        "en": "Analyzing{dots}",
        "zh": "正在分析{dots}",
        "ko": "분석 처리 중{dots}",
        "ja": "分析中{dots}",
        "es": "Analizando{dots}",
    },
    "hud_elapsed_wait": {
        "en": "Elapsed {sec}s · Please wait a moment",
        "zh": "已用时 {sec}秒 · 请稍候",
        "ko": "경과 {sec}초 · 잠시만 기다려 주세요",
        "ja": "経過 {sec}秒 · 少々お待ちください",
        "es": "Transcurrido {sec}s · Un momento por favor",
    },
    "hud_system_status": {
        "en": "SYSTEM STATUS",
        "zh": "系统状态",
        "ko": "시스템 상태",
        "ja": "システム状態",
        "es": "ESTADO DEL SISTEMA",
    },
    "hud_uptime": {
        "en": "UPTIME",
        "zh": "运行时间",
        "ko": "가동시간",
        "ja": "稼働時間",
        "es": "TIEMPO ACTIVO",
    },
    "hud_faces": {
        "en": "FACES",
        "zh": "人脸",
        "ko": "얼굴인식",
        "ja": "顔認識",
        "es": "ROSTROS",
    },
    "hud_frame": {
        "en": "FRAME",
        "zh": "帧数",
        "ko": "프레임",
        "ja": "フレーム",
        "es": "CUADRO",
    },
    "hud_bottom_hint": {
        "en": "Enter:Type  Space:Talk  K:Lang({lang})  Z:PowerSave  H:Help  Q:Quit",
        "zh": "Enter:输入  Space:说话  K:语言({lang})  Z:省电  H:帮助  Q:退出",
        "ko": "Enter:입력  Space:말하기  K:언어({lang})  Z:절전  H:도움말  Q:종료",
        "ja": "Enter:入力  Space:話す  K:言語({lang})  Z:省電力  H:ヘルプ  Q:終了",
        "es": "Enter:Escribir  Space:Hablar  K:Idioma({lang})  Z:Ahorro  H:Ayuda  Q:Salir",
    },
    "kb_idle_hint": {
        "en": "Enter: Type Command  ·  K: Language ({lang})  ·  Hold Index=Talk",
        "zh": "Enter: 输入指令  ·  K: 切换语言 ({lang})  ·  竖起食指=说话",
        "ko": "Enter: 명령 입력  ·  K: 언어 변경 ({lang})  ·  검지 유지=말하기",
        "ja": "Enter: コマンド入力  ·  K: 言語切替 ({lang})  ·  人差し指=話す",
        "es": "Enter: Escribir comando  ·  K: Idioma ({lang})  ·  Índice=Hablar",
    },
    "kb_placeholder": {
        "en": "Type a command",
        "zh": "输入指令",
        "ko": "명령을 입력하세요",
        "ja": "コマンドを入力",
        "es": "Escribe un comando",
    },
    "kb_active_hint": {
        "en": "Tab EN/KO · Enter Send · Esc Cancel",
        "zh": "Tab 切换输入 · Enter 发送 · Esc 取消",
        "ko": "Tab 한/영 · Enter 전송 · Esc 취소",
        "ja": "Tab 入力切替 · Enter 送信 · Esc キャンセル",
        "es": "Tab EN/KO · Enter Enviar · Esc Cancelar",
    },
    "help_button": {
        "en": "H  Help",
        "zh": "H  帮助",
        "ko": "H  도움말",
        "ja": "H  ヘルプ",
        "es": "H  Ayuda",
    },
    "help_close": {
        "en": "H Close",
        "zh": "H 关闭",
        "ko": "H 닫기",
        "ja": "H 閉じる",
        "es": "H Cerrar",
    },
    "help_title": {
        "en": "Controls & Help",
        "zh": "操作与帮助",
        "ko": "조작 및 도움말",
        "ja": "操作とヘルプ",
        "es": "Controles y Ayuda",
    },
    "help_summary_suffix": {
        "en": " · Summary",
        "zh": " · 简略",
        "ko": " · 요약",
        "ja": " · 要約",
        "es": " · Resumen",
    },
    "help_body": {
        "en": (
            "Voice & Keyboard Commands\n"
            "Say \"Jarvis\" or press Space to start voice recording.\n"
            "Press K to switch language (EN / 中文 / 한국어 / 日本語 / ES).\n"
            "Press V to enroll your voice profile (5 samples).\n\n"
            "On-Screen Keyboard\n"
            "Press Enter or / to open the bottom input bar, or T for popup.\n\n"
            "Panels & Power Save\n"
            "B: Background Tasks · P: Performance · Z: Power Save · D: Dismiss · Q: Quit"
        ),
        "zh": (
            "语音与键盘指令\n"
            "呼叫“Jarvis”或按空格键开始语音录音。\n"
            "按 K 键切换语言（English / 中文 / 한국어 / 日本語 / Español）。\n"
            "按 V 键录入您的声纹（5次样本）。\n\n"
            "屏幕键盘与弹窗\n"
            "按 Enter 或 / 打开底部输入框，或按 T 打开输入窗口。\n\n"
            "面板与省电模式\n"
            "B: 后台任务 · P: 性能模式 · Z: 省电模式 · D: 关闭图表 · Q: 退出"
        ),
        "ko": (
            "음성 및 키보드 명령\n"
            "\"자비스\"라고 부르거나 Space 키를 눌러 녹음을 시작하세요.\n"
            "K 키를 눌러 언어를 전환할 수 있습니다 (EN/中文/한국어/日本語/ES).\n"
            "V 키로 본인 목소리를 등록(5회)할 수 있습니다.\n\n"
            "화면 키보드 및 팝업 입력\n"
            "Enter 또는 / 키로 하단 입력창을 열거나 T 키로 팝업창을 여세요.\n\n"
            "패널 및 절전 모드\n"
            "B: 백그라운드 · P: 성능 설정 · Z: 절전 모드 · D: 표 닫기 · Q: 종료"
        ),
        "ja": (
            "音声＆キーボード操作\n"
            "「Jarvis」と呼ぶか Space キーで音声録音を開始します。\n"
            "K キーで表示・音声言語を切り替えます（EN/中文/한국어/日本語/ES）。\n"
            "V キーで声紋プロファイルを登録（5回録音）できます。\n\n"
            "画面キーボード\n"
            "Enter または / で下部入力バー、T キーでポップアップ入力を開きます。\n\n"
            "パネル＆省電力\n"
            "B: バックグラウンド · P: 性能 · Z: 省電力 · D: オーバーレイ消去 · Q: 終了"
        ),
        "es": (
            "Comandos de Voz y Teclado\n"
            "Di \"Jarvis\" o pulsa Espacio para grabar tu voz.\n"
            "Pulsa K para cambiar de idioma (EN / 中文 / 한국어 / 日本語 / ES).\n"
            "Pulsa V para registrar tu perfil de voz (5 muestras).\n\n"
            "Teclado en Pantalla\n"
            "Pulsa Enter o / para abrir la barra inferior, o T para ventana emergente.\n\n"
            "Paneles y Ahorro de Energía\n"
            "B: Tareas · P: Rendimiento · Z: Ahorro · D: Cerrar panel · Q: Salir"
        ),
    },
    "help_compact": {
        "en": (
            "Voice: \"Jarvis\" · Space to Talk · K: Lang ({lang})\n"
            "Keyboard: Enter or / · T Popup · Esc Cancel\n"
            "Z: Power Save · B: Tasks · P: Perf · H: Help · D: Close"
        ),
        "zh": (
            "语音: “Jarvis” · Space 说话 · K: 语言 ({lang})\n"
            "键盘: Enter 或 / · T 弹窗 · Esc 取消\n"
            "Z: 省电 · B: 后台 · P: 性能 · H: 帮助 · D: 关闭"
        ),
        "ko": (
            "음성: \"자비스\" · Space 말하기 · K: 언어 ({lang})\n"
            "키보드: Enter 또는 / · T 팝업 · Esc 취소\n"
            "Z: 절전 · B: 백그라운드 · P: 성능 · H: 도움말 · D: 닫기"
        ),
        "ja": (
            "音声: \"Jarvis\" · Space 話す · K: 言語 ({lang})\n"
            "入力: Enter または / · T ポップアップ · Esc 取消\n"
            "Z: 省電力 · B: タスク · P: 性能 · H: ヘルプ · D: 閉じる"
        ),
        "es": (
            "Voz: \"Jarvis\" · Espacio Hablar · K: Idioma ({lang})\n"
            "Teclado: Enter o / · T Ventana · Esc Cancelar\n"
            "Z: Ahorro · B: Tareas · P: Rendimiento · H: Ayuda · D: Cerrar"
        ),
    },
    "perf_button": {
        "en": "P  Perf",
        "zh": "P  性能",
        "ko": "P  성능",
        "ja": "P  性能",
        "es": "P  Rend.",
    },
    "perf_title": {
        "en": "Performance Profile",
        "zh": "性能配置",
        "ko": "성능 프로필",
        "ja": "パフォーマンス設定",
        "es": "Perfil de Rendimiento",
    },
    "perf_hint": {
        "en": "1 Eco   2 Balanced   3 High Quality",
        "zh": "1 节能   2 均衡   3 高画质",
        "ko": "1 절전   2 균형   3 고품질",
        "ja": "1 省電力   2 バランス   3 高品質",
        "es": "1 Eco   2 Equilibrado   3 Alta Calidad",
    },
    "perf_restart_hint": {
        "en": "Restart JARVIS to fully apply cam/STT",
        "zh": "重启 JARVIS 以完全应用摄像头/语音设置",
        "ko": "카메라/STT 설정은 재시작 후 완전 반영됩니다",
        "ja": "カメラ/STT設定の完全適用には再起動が必要です",
        "es": "Reinicia JARVIS para aplicar cámara/STT",
    },
    "bg_button": {
        "en": "B  Background",
        "zh": "B  后台",
        "ko": "B  백그라운드",
        "ja": "B  バックグラウンド",
        "es": "B  Tareas",
    },
    "bg_title": {
        "en": "Background Tasks",
        "zh": "后台任务",
        "ko": "백그라운드 작업",
        "ja": "バックグラウンドタスク",
        "es": "Tareas en Segundo Plano",
    },
    "powersave_on_btn": {
        "en": "PowerSave ON",
        "zh": "省电模式 ON",
        "ko": "절전 모드 ON",
        "ja": "省電力 ON",
        "es": "Ahorro ON",
    },
    "powersave_off_btn": {
        "en": "Z PowerSave",
        "zh": "Z 省电模式",
        "ko": "Z 절전모드",
        "ja": "Z 省電力",
        "es": "Z Ahorro",
    },
    "powersave_cam_off_title": {
        "en": "Power Save Mode — Webcam OFF",
        "zh": "省电模式 — 摄像头已关闭",
        "ko": "절전 모드 — 웹캠 꺼짐",
        "ja": "省電力モード — カメラ OFF",
        "es": "Modo Ahorro — Cámara Desactivada",
    },
    "powersave_cam_off_sub": {
        "en": "Enter / T : Text Command  ·  K : Language  ·  Z : Exit Power Save",
        "zh": "Enter / T : 文字指令  ·  K : 切换语言  ·  Z : 退出省电模式",
        "ko": "Enter / T : 텍스트 명령  ·  K : 언어 변경  ·  Z : 절전 해제",
        "ja": "Enter / T : テキスト入力  ·  K : 言語切替  ·  Z : 省電力解除",
        "es": "Enter / T : Comando de texto  ·  K : Idioma  ·  Z : Salir de ahorro",
    },

    # ── Spoken & Chat System Messages ──
    "lang_switched_msg": {
        "en": "Language switched to English.",
        "zh": "语言已切换为中文。",
        "ko": "언어가 한국어로 변경되었습니다.",
        "ja": "言語を日本語に切り替えました。",
        "es": "Idioma cambiado a español.",
    },
    "one_moment": {
        "en": "One moment, please.",
        "zh": "请稍等。",
        "ko": "잠시만요.",
        "ja": "少々お待ちください。",
        "es": "Un momento, por favor.",
    },
    "greet_morning": {
        "en": "Good morning",
        "zh": "早上好",
        "ko": "좋은 아침입니다",
        "ja": "おはようございます",
        "es": "Buenos días",
    },
    "greet_afternoon": {
        "en": "Good afternoon",
        "zh": "下午好",
        "ko": "안녕하세요",
        "ja": "こんにちは",
        "es": "Buenas tardes",
    },
    "greet_evening": {
        "en": "Good evening",
        "zh": "晚上好",
        "ko": "좋은 저녁입니다",
        "ja": "こんばんは",
        "es": "Buenas noches",
    },
    "fast_greeting_tpl": {
        "en": "{greet}. The local time is {time_str}.",
        "zh": "{greet}。现在当地时间是 {time_str}。",
        "ko": "{greet}. 현재 시각은 {time_str}입니다.",
        "ja": "{greet}。現在の現地時刻は {time_str} です。",
        "es": "{greet}. La hora local es {time_str}.",
    },
    "location_followup_tpl": {
        "en": "Your current location is {where}. The local time is {time_str}.",
        "zh": "您当前的位置是 {where}，当地时间是 {time_str}。",
        "ko": "현재 위치는 {where}이며, 현지 시각은 {time_str}입니다.",
        "ja": "現在地は {where}、現地時刻は {time_str} です。",
        "es": "Tu ubicación actual es {where}. La hora local es {time_str}.",
    },
    "location_unknown_tpl": {
        "en": "I could not automatically determine your location. The local time is {time_str}.",
        "zh": "无法自动确定您的位置。现在时间是 {time_str}。",
        "ko": "위치를 자동으로 확인하지 못했습니다. 현재 시각은 {time_str}입니다.",
        "ja": "現在地を自動取得できませんでした。現在時刻は {time_str} です。",
        "es": "No pude determinar tu ubicación automáticamente. La hora local es {time_str}.",
    },
    "fx_briefing_tpl": {
        "en": "The USD to KRW exchange rate is {rate} won.{chg} {tone}",
        "zh": "当前美元兑韩元汇率为 {rate} 韩元。{chg}{tone}",
        "ko": "현재 원달러 환율은 {rate}원입니다.{chg} {tone}",
        "ja": "現在の米ドル・ウォン為替レートは {rate} ウォンです。{chg}{tone}",
        "es": "El tipo de cambio de dólar a won es de {rate} wones.{chg} {tone}",
    },
    "fx_chg_tpl": {
        "en": " That is {chg} over the past month.",
        "zh": "较上月变动 {chg}。",
        "ko": " 최근 한 달 대비 {chg}입니다.",
        "ja": "先月比 {chg} です。",
        "es": " Eso es {chg} respecto al mes pasado.",
    },
    "fx_tone_stable": {
        "en": "It remains relatively stable.",
        "zh": "目前走势保持平稳。",
        "ko": "비교적 안정적인 흐름입니다.",
        "ja": "比較的安定した推移です。",
        "es": "Se mantiene relativamente estable.",
    },
    "fx_tone_warn": {
        "en": "The US dollar is currently in a strong range.",
        "zh": "美元目前处于走强区间。",
        "ko": "달러 강세 구간에 진입해 있습니다.",
        "ja": "現在ドル高圏内で推移しています。",
        "es": "El dólar estadounidense se encuentra en un rango fuerte.",
    },
    "fx_tone_danger": {
        "en": "It has crossed 1,400 won, indicating an elevated risk zone.",
        "zh": "已突破 1400 韩元，处于高风险区间。",
        "ko": "1,400원을 돌파하여 고위험 구간으로 판단됩니다.",
        "ja": "1,400ウォンを超え、警戒水準に達しています。",
        "es": "Ha superado los 1.400 wones, indicando una zona de riesgo elevado.",
    },
    "fx_unavailable": {
        "en": "I was unable to retrieve the USD to KRW exchange rate at the moment.",
        "zh": "暂时无法获取美元兑韩元汇率数据。",
        "ko": "지금은 원달러 환율 정보를 불러오지 못했습니다.",
        "ja": "現在、米ドル・ウォン為替レートを取得できませんでした。",
        "es": "No pude obtener el tipo de cambio de dólar a won en este momento.",
    },
    "fx_title": {
        "en": "USD/KRW Exchange Rate",
        "zh": "美元/韩元 汇率",
        "ko": "원/달러 환율",
        "ja": "米ドル/ウォン 為替レート",
        "es": "Tipo de Cambio USD/KRW",
    },
    "resource_line_tpl": {
        "en": "Mac CPU usage is at {cpu} percent, and memory usage is at {mem} percent.",
        "zh": "Mac CPU 使用率为 {cpu}%，内存使用率为 {mem}%。",
        "ko": "맥 CPU 사용률은 {cpu}%, 메모리 사용률은 {mem}%입니다.",
        "ja": "Mac の CPU 使用率は {cpu}%、メモリ使用率は {mem}% です。",
        "es": "El uso de CPU del Mac está al {cpu} por ciento y la memoria al {mem} por ciento.",
    },
    "face_registered_msg": {
        "en": "Face registration complete, {user}.",
        "zh": "人脸注册完成，{user}。",
        "ko": "얼굴 등록이 완료되었습니다, {user}.",
        "ja": "顔の登録が完了しました、{user}。",
        "es": "Registro facial completado, {user}.",
    },
    "face_unregistered_warn": {
        "en": "{user}, an unregistered person has been detected. Please be advised.",
        "zh": "{user}，检测到未注册人员，请注意。",
        "ko": "{user}, 등록되지 않은 인물이 감지되었습니다. 주의하시기 바랍니다.",
        "ja": "{user}、未登録の人物が検出されました。ご注意ください。",
        "es": "{user}, se ha detectado a una persona no registrada.",
    },
    "voice_enroll_start": {
        "en": "Starting voice enrollment. Please say Jarvis five times.",
        "zh": "开始声纹录入。请说五次 Jarvis。",
        "ko": "음성 등록을 시작합니다. 자비스라고 다섯 번 말씀해 주세요.",
        "ja": "声紋登録を開始します。ジャービスと5回話してください。",
        "es": "Iniciando registro de voz. Por favor, di Jarvis cinco veces.",
    },
    "voice_enroll_done": {
        "en": "Voice enrollment complete. I will now recognize your voice more accurately.",
        "zh": "声纹学习完成。现在我可以更准确地识别您的声音。",
        "ko": "음성 학습이 완료되었습니다. 이제 목소리를 더 정확히 인식합니다.",
        "ja": "声紋の学習が完了しました。これよりあなたの声を正確に認識します。",
        "es": "Registro de voz completado. Ahora reconoceré tu voz con mayor precisión.",
    },
    "security_blocked_msg": {
        "en": "I'm sorry, {user}. That request was blocked by security policy.",
        "zh": "抱歉，{user}。该请求已被安全策略拦截。",
        "ko": "죄송합니다, {user}. 보안 정책에 의해 차단된 명령입니다.",
        "ja": "申し訳ありません、{user}。そのリクエストはセキュリティポリシーによりブロックされました。",
        "es": "Lo siento, {user}. Esa solicitud fue bloqueada por la política de seguridad.",
    },
    "ollama_not_running_msg": {
        "en": "Local AI (Ollama) is not running. Please run 'ollama serve' in Terminal, {user}.",
        "zh": "本地 AI (Ollama) 未运行。请在终端运行 ollama serve，{user}。",
        "ko": "로컬 AI(Ollama)가 실행 중이 아닙니다. 터미널에서 ollama serve를 실행해 주세요, {user}.",
        "ja": "ローカル AI (Ollama) が起動していません。ターミナルで ollama serve を実行してください、{user}。",
        "es": "La IA local (Ollama) no está activa. Ejecuta 'ollama serve' en la Terminal, {user}.",
    },
    "offline_fallback_msg": {
        "en": "Could not connect to the local AI engine. Time, system status, files, calendar, and volume controls remain available offline.",
        "zh": "无法连接到本地 AI 引擎。时间、系统状态、文件、日程和音量控制仍可离线使用。",
        "ko": "로컬 AI에 연결하지 못했습니다. 시간, 시스템 상태, 파일, 일정, 볼륨 조절은 오프라인으로 계속 사용 가능합니다.",
        "ja": "ローカル AI に接続できませんでした。時刻、システム状態、ファイル、予定、音量調整はオフラインで利用可能です。",
        "es": "No se pudo conectar al motor de IA local. Hora, estado del sistema, archivos, calendario y volumen siguen disponibles sin conexión.",
    },
}

ACK_PHRASES_BY_LANG: dict[str, tuple[str, ...]] = {
    "en": (
        "Right away, Sir.",
        "One moment, please.",
        "Checking on that now.",
        "Certainly. Let me take a look.",
    ),
    "zh": (
        "好的，马上为您处理。",
        "请稍候，正在为您查询。",
        "收到，我立刻看一下。",
        "明白，请稍等片刻。",
    ),
    "ko": (
        "네, 바로 확인해 드릴게요.",
        "잠시만 기다려 주세요.",
        "알겠습니다, 지금 살펴볼게요.",
        "명령 확인했습니다. 바로 처리할게요.",
    ),
    "ja": (
        "かしこまりました。ただちに確認いたします。",
        "少々お待ちください。",
        "承知いたしました。今すぐお調べします。",
        "了解しました。処理を開始します。",
    ),
    "es": (
        "Enseguida, Señor.",
        "Un momento, por favor.",
        "Verificando eso ahora mismo.",
        "Por supuesto. Permítame revisar.",
    ),
}


def tr(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Translate `key` into active (or specified) language and format with `kwargs`."""
    code = normalize_lang(lang or get_lang())
    entry = TRANSLATIONS.get(key)
    if not entry:
        text = key
    else:
        text = entry.get(code) or entry.get(DEFAULT_LANG) or key
    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            pass
    return text


def get_ack_phrases(lang: str | None = None) -> tuple[str, ...]:
    code = normalize_lang(lang or get_lang())
    return ACK_PHRASES_BY_LANG.get(code, ACK_PHRASES_BY_LANG[DEFAULT_LANG])


def build_language_directive(lang: str | None = None, user_name: str | None = None) -> str:
    """Return strong LLM system prompt instructions enforcing the selected output language."""
    code = normalize_lang(lang or get_lang())
    meta = LANGUAGE_META[code]
    honorific = (user_name or os.getenv("JARVIS_USER_NAME") or meta["default_honorific"]).strip() or meta["default_honorific"]
    lang_name = meta["llm_language"]

    per_lang_style = {
        "en": (
            f"- ALWAYS respond in natural, calm, articulate **English** in the style of J.A.R.V.I.S., regardless of the language of tool outputs.\n"
            f"- Never output Korean, Chinese, Japanese, or Spanish sentences unless explicitly asked to translate. Translate any non-English company names or tool results into natural English.\n"
            f"- Address the user as \"{honorific}\" at most once per response."
        ),
        "zh": (
            f"- 无论工具返回什么语言的数据，始终使用自然、沉稳、得体的**简体中文（中文）**回答，保持 J.A.R.V.I.S. 管家风格。\n"
            f"- 严禁直接输出韩语或英语长句；如果工具返回了韩文或英文消息，请将其自然地翻译并概括为中文口语（2~4句话）。\n"
            f"- 每次回复最多称呼用户一次“{honorific}”。"
        ),
        "ko": (
            f"- 도구나 데이터의 원래 언어와 무관하게 항상 자연스럽고 정중한 **한국어(존댓말/해요체)**로 답변하세요.\n"
            f"- 영어 문장을 그대로 읽지 말고 자연스러운 한국어로 풀어서 2~4문장 이내로 간결하게 말하세요.\n"
            f"- 사용자 호칭(\"{honorific}\")은 답변당 최대 한 번만 사용하세요."
        ),
        "ja": (
            f"- ツールの出力言語に関わらず、常にJ.A.R.V.I.S.らしい落ち着いた丁寧な**日本語**で回答してください。\n"
            f"- 韓国語や英語の文章をそのまま出力せず、自然な日本語の話し言葉（2〜4文）に要約・翻訳して伝えてください。\n"
            f"- ユーザーの呼び名（「{honorific}」）は1回の返答につき最大1回までにしてください。"
        ),
        "es": (
            f"- Responde SIEMPRE en **Español** natural, calmado y articulado al estilo de J.A.R.V.I.S., sin importar el idioma de los datos o herramientas.\n"
            f"- Nunca respondas en coreano o inglés; traduce cualquier resultado de herramienta al español hablado natural (de 2 a 4 frases cortas).\n"
            f"- Dirígete al usuario como \"{honorific}\" como máximo una vez por respuesta."
        ),
    }[code]

    return (
        f"\n[CRITICAL LANGUAGE REQUIREMENT — ACTIVE LANGUAGE: {lang_name} ({code.upper()})]\n"
        f"- Preferred user address: {honorific}.\n"
        f"{per_lang_style}\n"
        f"- Keep responses concise and conversational (2 to 4 short sentences suitable for voice TTS).\n"
        f"- Do NOT read out markdown symbols, bullet points, raw JSON, or function names.\n"
    )
