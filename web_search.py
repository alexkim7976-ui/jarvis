
from __future__ import annotations

import os
import json
import requests
from datetime import datetime
try:
    import net  # 연결 재사용·재시도·적응형 타임아웃 공용 세션
except Exception:
    net = requests


TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

try:
    from free_mode import free_only_enabled, use_paid_search
    from free_search import duckduckgo_search, duckduckgo_news_search
except ImportError:
    def free_only_enabled():  # type: ignore
        return False
    def use_paid_search():  # type: ignore
        return bool(TAVILY_API_KEY)
    duckduckgo_search = None  # type: ignore
    duckduckgo_news_search = None  # type: ignore


# ──────────────────────────────────────────────
#  Tavily / DuckDuckGo 검색 함수
# ──────────────────────────────────────────────

def _web_search_live(query, search_depth="basic", max_results=5):
    """
    웹 검색 — 무료 모드: DuckDuckGo / 유료: Tavily API
    - query: 검색어
    - search_depth: "basic" (빠름) 또는 "advanced" (정밀)
    - max_results: 최대 결과 수
    """
    if free_only_enabled() or not use_paid_search():
        if duckduckgo_search:
            return duckduckgo_search(query, max_results=max_results)
        return {"success": False, "message": "무료 검색 모듈을 불러올 수 없습니다."}

    if not TAVILY_API_KEY:
        if duckduckgo_search:
            return duckduckgo_search(query, max_results=max_results)
        return {"success": False, "message": "TAVILY_API_KEY가 .env에 설정되지 않았습니다."}

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": search_depth,
        "max_results": int(max_results),
        "include_answer": True,
        "include_raw_content": False,
    }

    try:
        resp = net.post(url, json=payload, timeout=(5, 15))
        if resp.status_code != 200:
            return {
                "success": False,
                "message": f"Tavily API 오류 {resp.status_code}: {resp.text[:200]}",
            }

        data = resp.json()

        results = []
        for r in data.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:300],
                "score": round(r.get("score", 0), 2),
            })

        return {
            "success": True,
            "query": query,
            "answer": data.get("answer", ""),
            "results": results,
            "result_count": len(results),
        }

    except requests.Timeout:
        return {"success": False, "message": "검색 시간 초과"}
    except Exception as e:
        return {"success": False, "message": f"검색 오류: {e}"}


def _news_search_live(query, max_results=5):
    """
    뉴스 검색 — 무료 모드: DuckDuckGo / 유료: Tavily topic=news
    """
    if free_only_enabled() or not use_paid_search():
        if duckduckgo_news_search:
            return duckduckgo_news_search(query, max_results=max_results)
        return {"success": False, "message": "무료 검색 모듈을 불러올 수 없습니다."}

    if not TAVILY_API_KEY:
        if duckduckgo_news_search:
            return duckduckgo_news_search(query, max_results=max_results)
        return {"success": False, "message": "TAVILY_API_KEY가 .env에 설정되지 않았습니다."}

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "basic",
        "max_results": int(max_results),
        "include_answer": True,
        "topic": "news",
    }

    try:
        resp = net.post(url, json=payload, timeout=(5, 15))
        if resp.status_code != 200:
            return {"success": False, "message": f"API 오류: {resp.status_code}"}

        data = resp.json()
        results = []
        for r in data.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "content": r.get("content", "")[:300],
                "published_date": r.get("published_date", ""),
            })

        return {
            "success": True,
            "query": query,
            "answer": data.get("answer", ""),
            "results": results,
            "result_count": len(results),
        }

    except Exception as e:
        return {"success": False, "message": f"뉴스 검색 오류: {e}"}


def _cached_or_live(query, kind, live, force=False):
    if not force:
        try:
            from interest_prefetch import cached_search
            hit = cached_search(query, kind)
            if hit:
                return hit
        except Exception:
            pass
    result = live()
    try:
        from interest_prefetch import remember
        if isinstance(result, dict):
            remember(query, kind, result)
    except Exception:
        pass
    return result


def web_search(query, search_depth="basic", max_results=5, force=False):
    """관심 주제가 미리 받아져 있으면 그 결과를 바로 쓰고, 아니면 인터넷에서 가져온다."""
    return _cached_or_live(
        query, "web",
        lambda: _web_search_live(query, search_depth=search_depth, max_results=max_results),
        force=force,
    )


def news_search(query, max_results=5, force=False):
    """관심 주제 뉴스가 미리 받아져 있으면 그 결과를 바로 쓰고, 아니면 인터넷에서 가져온다."""
    return _cached_or_live(
        query, "news",
        lambda: _news_search_live(query, max_results=max_results),
        force=force,
    )


# ──────────────────────────────────────────────
#  도구 정의 (LLM에 전달)
# ──────────────────────────────────────────────

SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "인터넷에서 최신 정보를 실시간으로 검색합니다. 현재 날씨, 뉴스, 가격, 스포츠 결과, 최신 기술 등 학습 데이터에 없는 최신 정보가 필요할 때 사용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색어 (한국어 또는 영어)",
                    },
                    "search_depth": {
                        "type": "string",
                        "description": "검색 깊이: basic(빠름) 또는 advanced(정밀)",
                        "enum": ["basic", "advanced"],
                        "default": "basic",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "최대 결과 수 (1-10)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "news_search",
            "description": "최신 뉴스를 검색합니다. 오늘 뉴스, 속보, 최근 사건 등을 찾을 때 사용합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "뉴스 검색어",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "최대 결과 수",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
        },
    },
]


SEARCH_TOOL_MAP = {
    "web_search": web_search,
    "news_search": news_search,
}


def execute_search_tool(tool_name, arguments):
    """검색 도구 실행"""
    func = SEARCH_TOOL_MAP.get(tool_name)
    if not func:
        return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return func(**arguments)
    except Exception as e:
        return {"success": False, "message": f"실행 오류: {e}"}
