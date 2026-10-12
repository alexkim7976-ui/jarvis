"""관심 주제의 웹·뉴스를 백그라운드에서 미리 받아 둔다.

실시간 검색이 같은 주제면 유효 시간 안의 결과를 바로 돌려준다.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_running = False
_thread: threading.Thread | None = None


def data_dir() -> Path:
    root = Path(os.getenv("JARVIS_PROJECT_ROOT") or Path(__file__).resolve().parent)
    folder = root / "data"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def ttl_sec() -> float:
    try:
        return max(60.0, float(os.getenv("JARVIS_PREFETCH_TTL_SEC", "600")))
    except ValueError:
        return 600.0


def interval_sec() -> float:
    try:
        return max(30.0, float(os.getenv("JARVIS_PREFETCH_SEC", "120")))
    except ValueError:
        return 120.0


def _norm(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _interests_path() -> Path:
    return data_dir() / "interests.json"


def _cache_path() -> Path:
    return data_dir() / "interest_cache.json"


def _write_json(path: Path, payload) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _seed_topics() -> list[str]:
    seen: set[str] = set()
    topics: list[str] = []
    for key in ("JARVIS_INTERESTS", "NEWS_WATCHLIST", "WATCH_LIST"):
        for part in os.getenv(key, "").replace(";", ",").split(","):
            topic = part.strip()
            key_norm = _norm(topic)
            if len(key_norm) < 2 or key_norm in seen:
                continue
            seen.add(key_norm)
            topics.append(topic)
    return topics


def list_interests() -> list[str]:
    path = _interests_path()
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            items = data.get("topics") if isinstance(data, dict) else data
            if isinstance(items, list):
                return [str(item).strip() for item in items if str(item).strip()]
        except (OSError, json.JSONDecodeError):
            pass
    seeded = _seed_topics()
    _write_json(path, {"topics": seeded})
    return seeded


def add_interest(topic: str) -> str:
    topic = (topic or "").strip()
    if len(_norm(topic)) < 2:
        return "관심 주제가 너무 짧습니다."
    with _lock:
        topics = list_interests()
        if any(_norm(item) == _norm(topic) for item in topics):
            return f"이미 관심에 있습니다. {topic}"
        topics.append(topic)
        _write_json(_interests_path(), {"topics": topics})
    return f"관심을 추가했습니다. {topic}. 백그라운드에서 미리 가져옵니다."


def remove_interest(topic: str) -> str:
    wanted = _norm(topic)
    if len(wanted) < 2:
        return "뺄 관심 주제를 말해 주세요."
    with _lock:
        topics = list_interests()
        kept = [item for item in topics if _norm(item) != wanted]
        if len(kept) == len(topics):
            return f"관심 목록에 없습니다. {topic}"
        _write_json(_interests_path(), {"topics": kept})
    return f"관심을 뺐습니다. {topic}"


def match_topic(query: str) -> str | None:
    q = _norm(query)
    if len(q) < 2:
        return None
    best = None
    best_len = 0
    for topic in list_interests():
        t = _norm(topic)
        if not t:
            continue
        if q == t or t in q or q in t:
            if len(t) > best_len:
                best = topic
                best_len = len(t)
    return best


def _load_cache() -> dict:
    path = _cache_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def remember(query: str, kind: str, result: dict) -> None:
    if not isinstance(result, dict) or not result.get("success"):
        return
    topic = match_topic(query)
    if not topic:
        return
    with _lock:
        cache = _load_cache()
        entry = cache.get(_norm(topic))
        if not isinstance(entry, dict):
            entry = {"topic": topic}
        entry["topic"] = topic
        entry[kind] = {"fetched_at": time.time(), "result": result}
        cache[_norm(topic)] = entry
        _write_json(_cache_path(), cache)


def cached_search(query: str, kind: str) -> dict | None:
    topic = match_topic(query)
    if not topic:
        return None
    with _lock:
        entry = _load_cache().get(_norm(topic)) or {}
    item = entry.get(kind) if isinstance(entry, dict) else None
    if not isinstance(item, dict):
        return None
    fetched = float(item.get("fetched_at") or 0)
    age = time.time() - fetched
    if age > ttl_sec():
        return None
    result = item.get("result")
    if not isinstance(result, dict) or not result.get("success"):
        return None
    out = dict(result)
    out["cached"] = True
    out["cached_topic"] = topic
    out["cache_age_sec"] = int(age)
    return out


def prefetch_running() -> bool:
    return _running


def start_prefetch() -> None:
    global _running, _thread
    if _thread and _thread.is_alive():
        _running = True
        return
    _running = True
    _thread = threading.Thread(target=_loop, daemon=True, name="interest-prefetch")
    _thread.start()


def stop_prefetch() -> None:
    global _running
    _running = False


def _sleep(seconds: float) -> None:
    end = time.time() + seconds
    while _running and time.time() < end:
        time.sleep(min(1.0, end - time.time()))


def _refresh(topic: str) -> None:
    from web_search import news_search, web_search

    web = web_search(topic, force=True)
    remember(topic, "web", web if isinstance(web, dict) else {})
    news = news_search(topic, force=True)
    remember(topic, "news", news if isinstance(news, dict) else {})


def _loop() -> None:
    _sleep(8)
    index = 0
    while _running:
        try:
            from background_control import is_enabled
            if not is_enabled("prefetch"):
                _sleep(5)
                continue
        except Exception:
            pass
        topics = list_interests()
        if not topics:
            _sleep(interval_sec())
            continue
        topic = topics[index % len(topics)]
        index += 1
        try:
            _refresh(topic)
        except Exception:
            pass
        _sleep(interval_sec())
