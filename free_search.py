"""
J.A.R.V.I.S — DuckDuckGo 무료 웹 검색 (API 키 불필요)
"""
from __future__ import annotations

import re
from urllib.parse import quote_plus

try:
    import net
except Exception:
    import requests as net

try:
    from bs4 import BeautifulSoup
    _BS4 = True
except ImportError:
    _BS4 = False

_DDG_URL = "https://html.duckduckgo.com/html/"
_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) JARVIS/1.0"


def _clean(text: str, limit: int = 300) -> str:
    t = re.sub(r"\s+", " ", (text or "")).strip()
    return t[:limit]


def duckduckgo_search(query: str, max_results: int = 5) -> dict:
    """DuckDuckGo HTML 검색 — Tavily 대체 (무료)."""
    query = (query or "").strip()
    if not query:
        return {"success": False, "message": "검색어가 비어 있습니다."}
    if not _BS4:
        return {"success": False, "message": "beautifulsoup4가 필요합니다 (pip install beautifulsoup4)."}

    max_results = max(1, min(int(max_results), 10))
    try:
        resp = net.post(
            _DDG_URL,
            data={"q": query, "b": "", "kl": "kr-kr"},
            headers={"User-Agent": _UA, "Accept-Language": "ko-KR,ko;q=0.9"},
            timeout=(5, 20),
        )
        if resp.status_code != 200:
            return {
                "success": False,
                "message": f"DuckDuckGo 응답 {resp.status_code}",
            }

        soup = BeautifulSoup(resp.text, "html.parser")
        results = []
        for block in soup.select(".result")[:max_results]:
            title_el = block.select_one(".result__a")
            snippet_el = block.select_one(".result__snippet")
            if not title_el:
                continue
            href = title_el.get("href", "")
            results.append({
                "title": _clean(title_el.get_text()),
                "url": href,
                "content": _clean(snippet_el.get_text() if snippet_el else ""),
                "score": 0.0,
            })

        if not results:
            # 구형/대체 마크업 폴백
            for a in soup.select("a.result__a")[:max_results]:
                results.append({
                    "title": _clean(a.get_text()),
                    "url": a.get("href", ""),
                    "content": "",
                    "score": 0.0,
                })

        answer = results[0]["content"] if results else ""
        return {
            "success": True,
            "query": query,
            "answer": answer,
            "results": results,
            "result_count": len(results),
            "source": "duckduckgo",
        }
    except Exception as e:
        return {"success": False, "message": f"DuckDuckGo 검색 오류: {e}"}


def duckduckgo_news_search(query: str, max_results: int = 5) -> dict:
    """뉴스 키워드를 붙여 DuckDuckGo 검색."""
    q = f"{query.strip()} news"
    out = duckduckgo_search(q, max_results=max_results)
    if out.get("success"):
        out["source"] = "duckduckgo_news"
    return out
