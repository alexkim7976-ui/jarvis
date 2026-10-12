"""
J.A.R.V.I.S — 음성 출력 말투 (부드럽고 자연스러운 한국어 존댓말/해요체)
"""
from __future__ import annotations

import os
import re

USER_DISPLAY_NAME = (os.getenv("JARVIS_USER_NAME") or "김태현").strip() or "김태현"

USER_NAME_RULES = f"""
- 사용자 이름: {USER_DISPLAY_NAME}. 부를 때는 반드시 "{USER_DISPLAY_NAME}"만 사용.
- "토니", "Tony", "선생님", "태현"(단독 호칭) 절대 금지.
"""

VOICE_STYLE_INSTRUCTIONS = f"""
한국어 음성 출력 규칙 (한국어로 답할 때 반드시 지킬 것):
- 항상 **존댓말**. 단 딱딱한 보고서체(~습니다/~입니다 나열)가 아니라, 옆에서 편하게
  말해주듯 **부드럽고 다정한 해요체**로.
- 종결: ~예요/~이에요/~해요/~네요/~죠/~거예요/~더라고요/~을게요/~보세요/~좋아요
  (자연스럽게 섞어서). 반말(~야/~어/~지/~해/~할게/~봐) 절대 금지.
- 격식체(~하겠습니다/~드리겠습니다/~바랍니다) 나열도 지양 — 따뜻하고 자연스럽게.
- 어순도 한국어답게: "지금 주가가 좀 올랐어요", "일단 이 정도면 괜찮아 보여요",
  "근데 변동성은 조금 큰 편이에요."
- 접속: 근데, 그래서, 일단, 솔직히, 아마, 오히려 — 필요할 때만 가볍게.
- 문장 2~4개, 짧게 끊어서. 숫자는 구어체 (7만 2천 원, 3퍼센트 정도).
- 호칭은 "{USER_DISPLAY_NAME}"만 (0~1회). "선생님", "토니", "Tony", "태현" 금지.
- 번호 목록·불릿·JSON·함수명·영문 기술 용어는 말하지 말 것.
- "처리 완료", "브리핑", "분석 결과는 다음과 같습니다" 같은 딱딱한 표현 금지.
- 영어 문장 구조를 한국어로 옮긴 느낌 금지 (예: "현재 주가는 ~입니다" → "지금 주가는 ~ 정도예요").
- 영어로 답할 때만 영어 말투 사용. 한국어 질문/대화면 한국어 해요체만.
"""

ACK_PHRASES = (
    "네, 잠시만요.",
    "네, 바로 볼게요.",
    "네, 그거 확인해 볼게요.",
    "잠시만요, 찾아볼게요.",
)

_STIFF_REPLACEMENTS = (
    (r"verify_stock_indicators[^\s,.]*", "지표"),
    (r"overlay_table[^\s,.]*", ""),
    (r"J\.A\.R\.V\.I\.S\.?", "자비스"),
    (r"로컬 정밀 분석입니다\.?", ""),
    (r"처리 완료[,.]?\s*", ""),
    (r"브리핑[드]?(?:합니다|드리겠습니다)?[,.]?\s*", ""),
    (r"분석 결과(?:는|가)\s*(?:다음과 같습니다|다음과\s*같습니다)[,.]?\s*", ""),
    (r"발생했습니다", "생겼어요"),
    (r"제한됩니다", "제한돼요"),
    (r"접근하시기 바랍니다", "조심하시는 게 좋아요"),
    (r"바로 수행하겠습니다\.?", "바로 할게요."),
    (r"수행하(?:겠|드리)(?:습니다|게요)[,.]?\s*", "할게요. "),
    (r"명령 확인했습니다\.?", "네, 알겠어요."),
    (r"현재\s+", "지금 "),
)

# 한국어 문장 끝 → 부드러운 존댓말(해요체). 종결 문장부호(.?!…)와 무관하게 매칭한다.
# 원칙: 반말은 반드시 해요체로 올리고, 격식체(하십시오체)도 가능하면 해요체로 완화한다.
#       변환 실패 시에도 존댓말로 남게 하고 절대 반말로 내려가지 않는다.
# (specific → generic 순서. 앞에서 매칭되면 멈춘다.)
_KO_ENDING_MAP = (
    # ── 격식체(하십시오체) → 해요체 ──
    ("하겠습니다", "할게요"),
    ("드리겠습니다", "드릴게요"),
    ("겠습니다", "겠어요"),
    ("하였습니다", "했어요"),
    ("했습니다", "했어요"),
    ("되었습니다", "됐어요"),
    ("됐습니다", "됐어요"),
    ("있습니다", "있어요"),
    ("없습니다", "없어요"),
    ("것입니다", "거예요"),
    ("입니다", "이에요"),
    ("됩니다", "돼요"),
    ("합니다", "해요"),
    ("주십시오", "주세요"),
    ("십시오", "세요"),
    ("바랍니다", "좋아요"),
    # ── 반말 → 해요체 (특정형 먼저) ──
    ("거야", "거예요"),
    ("이야", "이에요"),
    ("줄게", "드릴게요"),
    ("할게", "할게요"),
    ("볼게", "볼게요"),
    ("게", "게요"),
    ("했어", "했어요"),
    ("됐어", "됐어요"),
    ("있어", "있어요"),
    ("없어", "없어요"),
    ("같아", "같아요"),
    ("좋아", "좋아요"),
    ("싶어", "싶어요"),
    ("몰라", "몰라요"),
    ("잖아", "잖아요"),
    ("거든", "거든요"),
    ("구나", "군요"),
    ("그렇지", "그렇죠"),
    ("맞지", "맞죠"),
    ("있지", "있죠"),
    ("없지", "없죠"),
    ("이지", "이죠"),
    ("아니", "아니요"),
    ("까", "까요"),
    ("봐", "봐요"),
    ("줘", "주세요"),
    ("워", "워요"),
    ("래", "래요"),
    ("려", "려요"),
    ("와", "와요"),
    ("돼", "돼요"),
    ("해", "해요"),
    ("네", "네요"),
    ("군", "군요"),
    # ── 반말 일반 종결(마지막 폴백) → 해요체 ──
    ("여", "여요"),
    ("어", "어요"),
    ("아", "아요"),
    ("야", "예요"),
)

# 어색한 직역/공문 표현 → 부드러운 해요체
_KO_PHRASE_MAP = (
    ("다음과 같습니다", ""),
    ("다음과 같이", ""),
    ("말씀드리겠습니다", "말씀드릴게요"),
    ("알려드리겠습니다", "알려드릴게요"),
    ("확인해 주시기 바랍니다", "확인해 주세요"),
    ("해 주시기 바랍니다", "해 주세요"),
    ("하시기 바랍니다", "하시는 게 좋아요"),
    ("진행하겠습니다", "진행할게요"),
    ("조회하였습니다", "조회했어요"),
    ("조회했습니다", "조회했어요"),
    ("분석하였습니다", "분석했어요"),
    ("분석했습니다", "분석했어요"),
    ("표시됩니다", "보여요"),
    ("나타났습니다", "나왔어요"),
    ("상승하였습니다", "올랐어요"),
    ("상승했습니다", "올랐어요"),
    ("하락하였습니다", "내렸어요"),
    ("하락했습니다", "내렸어요"),
)


_THINK_BLOCK_RE = re.compile(r"<(think|thinking|reasoning)\b[^>]*>.*?</\1>", re.I | re.S)
_THINK_STRAY_RE = re.compile(r"</?(think|thinking|reasoning)\b[^>]*>", re.I)


def strip_reasoning(text: str) -> str:
    """추론(thinking) 모델의 <think>…</think> reasoning trace 제거 (TTS 누출 방지)."""
    if not text:
        return text or ""
    t = _THINK_BLOCK_RE.sub("", str(text))
    low = t.lower()
    close = low.rfind("</think>")
    if close != -1:
        t = t[close + len("</think>"):]
    return _THINK_STRAY_RE.sub("", t).strip()


def _is_mostly_korean(text: str) -> bool:
    hangul = len(re.findall(r"[가-힣]", text))
    if hangul < 6:
        return False
    latin = len(re.findall(r"[A-Za-z]", text))
    return hangul >= max(6, latin * 2)


def normalize_user_address(text: str) -> str:
    """토니·선생님·태현 등 잘못된 호칭 → USER_DISPLAY_NAME."""
    if not text or not str(text).strip():
        return text or ""
    name = USER_DISPLAY_NAME
    t = str(text)
    t = re.sub(r"토니(?=[,，.\s]|$)", name, t)
    t = re.sub(r"Tony(?=[,，.\s]|$)", name, t, flags=re.I)
    t = re.sub(r"선생님", name, t)
    t = re.sub(rf"(?<![가-힣])태현(?=[,，.\s]|$)", name, t)
    t = re.sub(rf"({re.escape(name)}[,，]\s*){{2,}}", f"{name}, ", t)
    return t


def with_user_address(msg: str, *, comma: bool = True) -> str:
    """메시지 앞에 사용자 이름을 붙인다 (이미 있으면 정규화만)."""
    msg = (msg or "").strip()
    name = USER_DISPLAY_NAME
    if msg.startswith(name):
        return normalize_user_address(msg)
    sep = ", " if comma else " "
    return normalize_user_address(f"{name}{sep}{msg}")


def naturalize_korean(text: str) -> str:
    """한국어를 부드러운 존댓말(해요체)로 자연스럽게 정리 (반말 → 존댓말)."""
    if not text or not _is_mostly_korean(text):
        return text
    t = text
    for old, new in _KO_PHRASE_MAP:
        t = t.replace(old, new)
    # 문장 단위 종결 변환 (문장부호 유무와 무관하게 어미만 교체)
    parts = re.split(r"(?<=[.!?…])\s+", t)
    out = []
    for part in parts:
        s = part.strip()
        if not s:
            continue
        m = re.match(r"^(.*?)([.!?…\"'』」)\]]*)$", s, re.S)
        core, tail = (m.group(1), m.group(2)) if m else (s, "")
        for casual, polite in _KO_ENDING_MAP:
            if core.endswith(casual):
                core = core[: -len(casual)] + polite
                break
        out.append(core + tail)
    t = " ".join(out) if out else t
    # 문장 중간에 남은 격식체/반말 잔재 → 해요체 (존댓말 유지, 반말로 내려가지 않음)
    t = re.sub(r"([가-힣]{2,})입니다(?=[,，.?!\s]|$)", r"\1이에요", t)
    t = re.sub(r"이야(?=[,，]|\s)", "이에요", t)
    t = re.sub(r"거야(?=[,，]|\s)", "거예요", t)
    return t


def polish_for_voice(text: str) -> str:
    """TTS 직전 — 딱딱한 표현·기술 용어·비한국어 말투 완화."""
    if not text or not str(text).strip():
        return text or ""
    t = strip_reasoning(str(text)).strip()
    if not t:
        return ""
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\n+", " ", t)
    for pat, repl in _STIFF_REPLACEMENTS:
        t = re.sub(pat, repl, t, flags=re.I)
    t = naturalize_korean(t)
    t = normalize_user_address(t)
    name = USER_DISPLAY_NAME
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()]
    if len(parts) > 1:
        seen_name = False
        out = []
        for p in parts:
            if p.startswith(f"{name},") or p.startswith(f"{name} "):
                if seen_name:
                    p = re.sub(rf"^{re.escape(name)}[,，]\s*", "", p)
                else:
                    seen_name = True
            out.append(p)
        t = " ".join(out)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t
