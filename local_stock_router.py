"""
Mac 로컬 주식 분석 라우터 — Gemini 0
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
음성/텍스트에서 종목·지표 의도를 감지하면
주식_입력받고_확인_py / local_intelligence 로 즉시 분석 (LLM API 불필요).
"""
from __future__ import annotations

import re
from typing import Any

# 종목명 추출용 (한글 종목 + 티커)
_TICKER_PAT = re.compile(
    r"(005930\.KS|000660\.KS|035420\.KS|035720\.KS|005380\.KS|"
    r"AAPL|TSLA|NVDA|MSFT|GOOGL|AMZN|META|005930|000660|"
    r"삼성전자|SK하이닉스|하이닉스|네이버|카카오|현대차|테슬라|애플|엔비디아|"
    r"마이크로소프트|구글|아마존|메타)",
    re.I,
)

_STOCK_INTENT = re.compile(
    r"주가|현재가|시세|주식|종목|투자|RSI|MACD|볼린저|지표|분석|"
    r"매수|매도|점수|얼마|등락|PER|PBR|기술적|종합\s*분석|알아봐|"
    r"어때|전망|사도|팔아|봐줘|찾아봐|조회",
    re.I,
)

# 종목명 뒤에 붙는 한국어 조사 (예: "키움증권에", "삼성전자는") — 경계 판정용
_KR_PARTICLES = set("은는이가을를에의도만과와랑로으께서")


def _is_hangul_ch(c: str) -> bool:
    return bool(c) and "\uac00" <= c <= "\ud7a3"


def _scan_known_name(text: str) -> str | None:
    """문장 속에 박힌 '알려진' 종목명을 찾는다.
    '키움증권에 대해 분석해줘'처럼 조사/군더더기가 붙어도 인식되도록 부분일치+경계검사."""
    t = text or ""
    try:
        from stock_quant import _KR_TICKER_MAP, _KR_STT_ALIASES
    except Exception:
        return None

    # STT 오인식 별칭 먼저 정규화 (예: '기융중권' → '키움증권')
    for wrong, right in _KR_STT_ALIASES.items():
        if wrong in t:
            t = t.replace(wrong, right)

    names = set(_KR_TICKER_MAP.keys()) | set(_KR_STT_ALIASES.values())
    # 긴 이름 우선 매칭 (부분 충돌 방지: '삼성전자' vs '삼성')
    for name in sorted(names, key=len, reverse=True):
        start = 0
        while True:
            idx = t.find(name, start)
            if idx == -1:
                break
            before = t[idx - 1] if idx > 0 else ""
            after = t[idx + len(name)] if idx + len(name) < len(t) else ""
            if name.isascii():
                ok_before = not before.isalnum()
                ok_after = not after.isalnum()
            else:
                # 한글 이름: 앞은 비한글/시작, 뒤는 비한글이거나 조사여야 함
                ok_before = not _is_hangul_ch(before)
                ok_after = (not _is_hangul_ch(after)) or (after in _KR_PARTICLES)
            if ok_before and ok_after:
                return name
            start = idx + 1
    return None


def detect_stock_query(text: str) -> str | None:
    """주식 분석 의도 + 종목명/티커 추출. 없으면 None."""
    t = (text or "").strip()
    if not t or not _STOCK_INTENT.search(t):
        return None
    m = _TICKER_PAT.search(t)
    if m:
        return m.group(1)
    # stock_quant 한글/영문 해석 (확장)
    try:
        from stock_quant import resolve_alert_query, _resolve_ticker
        r = resolve_alert_query(t)
        if r.get("confident") and r.get("ticker"):
            return r.get("name") or r["ticker"]
    except Exception:
        pass
    # 문장 속 알려진 종목명 스캔 (조사·군더더기에 강함: "키움증권에 대해 분석해줘")
    known = _scan_known_name(t)
    if known:
        return known
    # 마지막 폴백 — 퍼지 해석
    try:
        from stock_quant import _resolve_ticker
        resolved = _resolve_ticker(t)
        if resolved and resolved != t:
            return resolved
    except Exception:
        pass
    return None


def run_local_stock_analysis(query: str) -> dict[str, Any]:
    """
    로컬 정밀 분석 — verify_stock + local_intelligence narrative.
    Gemini API 호출 없음.
    """
    q = (query or "").strip()
    if not q:
        return {"success": False, "message": "종목을 알려주세요."}

    from 주식_입력받고_확인_py import verify_stock

    verify = verify_stock(q)
    if not verify.get("success"):
        return verify

    narrative = verify.get("briefing", "")
    try:
        from local_intelligence.engine import refresh_local_analysis_report
        rep = refresh_local_analysis_report(q)
        if rep.narrative:
            narrative = rep.narrative
        verify["local_intel"] = rep.to_dict()
        verify["ollama_used"] = rep.ollama_used
    except Exception:
        verify["ollama_used"] = False

    verify["briefing"] = narrative
    verify["gemini_used"] = False
    verify["backend"] = "mac_local"
    return verify


def try_local_stock_response(text: str) -> dict[str, Any] | None:
    """의도 감지 시 분석 dict 반환, 아니면 None."""
    target = detect_stock_query(text)
    if not target:
        return None
    return run_local_stock_analysis(target)
