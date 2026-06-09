"""
Fish Audio 보이스 탐색 도우미
────────────────────────────
Fish Audio API에서 사용 가능한 목소리를 검색하여
.env 에 넣을 FISH_VOICE_ID 를 찾는 데 사용합니다.

사용법:
  python find_voice.py --query "male british"
  python find_voice.py --query "jarvis calm"
"""

import os
import sys
import argparse
import requests
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("FISH_AUDIO_API_KEY", "")


def search_voices(query: str, page_size: int = 10):
    if not API_KEY:
        print("❌  .env 에 FISH_AUDIO_API_KEY 가 없습니다.")
        sys.exit(1)

    url = "https://api.fish.audio/model"
    headers = {"Authorization": f"Bearer {API_KEY}"}
    params  = {"title": query, "page_size": page_size, "page_number": 1, "sort_by": "task_count"}

    resp = requests.get(url, headers=headers, params=params, timeout=15)
    if resp.status_code != 200:
        print(f"❌  API 오류 {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)

    data  = resp.json()
    items = data.get("items", [])

    if not items:
        print("검색 결과가 없습니다. 다른 검색어를 시도해보세요.")
        return

    print(f"\n{'─'*60}")
    print(f"  검색어: '{query}'  →  결과 {len(items)}개")
    print(f"{'─'*60}")
    for v in items:
        vid   = v.get("_id", "N/A")
        title = v.get("title", "이름 없음")
        lang  = v.get("languages", [])
        tasks = v.get("task_count", 0)
        print(f"  🎙  {title}")
        print(f"      ID      : {vid}")
        print(f"      언어    : {', '.join(lang) if lang else '미정'}")
        print(f"      사용 수 : {tasks:,}회")
        print()

    print("원하는 목소리의 ID 를 .env 의 FISH_VOICE_ID 에 복사하세요.")
    print(f"{'─'*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fish Audio 보이스 검색")
    parser.add_argument("--query", "-q", default="male calm",
                        help="검색어 (기본: 'male calm')")
    parser.add_argument("--count", "-n", type=int, default=10,
                        help="결과 개수 (기본: 10)")
    args = parser.parse_args()
    search_voices(args.query, args.count)
