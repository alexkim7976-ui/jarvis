"""
J.A.R.V.I.S — 백그라운드 모니터링 데몬
──────────────────────────────────────────
Google Play Console 리뷰/크래시, GitHub 이슈/PR을
주기적으로 감시하고 음성 또는 시스템 알림으로 보고합니다.

.env 설정:
  GITHUB_TOKEN=ghp_xxxxx
  GITHUB_REPOS=username/repo1,username/repo2
  PLAY_STORE_APP_ID=com.example.bloomie
  MONITOR_INTERVAL=300  (초, 기본 5분)

의존성:
  pip install google-play-scraper
"""

from __future__ import annotations

import os
import json
import time
import threading
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from collections import deque

import requests
try:
    import net  # 느린 WiFi: 연결 재사용·재시도·적응형 타임아웃
except Exception:
    net = requests

# ──────────────────────────────────────────────
#  유틸: 무한 증가 방지용 경계 집합 (장시간 가동 시 RAM 누수 차단)
# ──────────────────────────────────────────────
class BoundedSeen:
    """본 적 있는 ID를 최근 max_size개만 유지. 오래된 ID는 자동 폐기."""
    def __init__(self, max_size: int = 500):
        self._set: set = set()
        self._order: deque = deque(maxlen=max_size)

    def __contains__(self, key) -> bool:
        return key in self._set

    def add(self, key):
        if key in self._set:
            return
        if len(self._order) == self._order.maxlen and self._order:
            self._set.discard(self._order[0])
        self._order.append(key)
        self._set.add(key)


# ──────────────────────────────────────────────
#  설정
# ──────────────────────────────────────────────
GITHUB_TOKEN     = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPOS_STR = os.getenv("GITHUB_REPOS", "")          # "user/repo1,user/repo2"
GITHUB_REPOS     = [r.strip() for r in GITHUB_REPOS_STR.split(",") if r.strip()]
PLAY_APP_ID      = os.getenv("PLAY_STORE_APP_ID", "")     # "com.example.bloomie"
MONITOR_INTERVAL = int(os.getenv("MONITOR_INTERVAL", "300"))  # 초 (기본 5분)
MONITOR_LOG_FILE = Path("monitor_log.json")

def _log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] 📡 [모니터] {msg}")


def _system_notify(title, message):
    """OS별 시스템 알림 전송.

    제목/본문은 GitHub 이슈·PR·Play 리뷰 등 외부(공격자 제어) 텍스트이므로
    스크립트 문자열에 절대 보간하지 않는다. argv / 환경변수로만 전달해
    AppleScript·PowerShell·XML 인젝션을 원천 차단한다.
    """
    title = str(title)
    message = str(message)
    try:
        if sys.platform == "darwin":
            # 'on run argv' 로 받아 텍스트를 코드가 아닌 데이터(argv)로만 사용 → 인젝션 불가
            subprocess.run(
                [
                    "osascript",
                    "-e", "on run argv",
                    "-e", 'display notification (item 1 of argv) '
                          'with title "J.A.R.V.I.S" subtitle (item 2 of argv)',
                    "-e", "end run",
                    "--", message, title,
                ],
                capture_output=True, timeout=5,
            )
        elif sys.platform == "win32":
            # 텍스트는 환경변수로 전달하고 PowerShell 내부에서 XML 이스케이프 → 명령/XML 인젝션 차단
            env = os.environ.copy()
            env["JARVIS_NOTIFY_TITLE"] = title
            env["JARVIS_NOTIFY_MSG"] = message
            ps = (
                "[Windows.UI.Notifications.ToastNotificationManager, "
                "Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null; "
                "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
                "ContentType=WindowsRuntime] | Out-Null; "
                "$ti=[System.Security.SecurityElement]::Escape($env:JARVIS_NOTIFY_TITLE); "
                "$ms=[System.Security.SecurityElement]::Escape($env:JARVIS_NOTIFY_MSG); "
                "$t=\"<toast><visual><binding template='ToastText02'>"
                "<text id='1'>$ti</text><text id='2'>$ms</text>"
                "</binding></visual></toast>\"; "
                "$x=New-Object Windows.Data.Xml.Dom.XmlDocument; $x.LoadXml($t); "
                "$n=[Windows.UI.Notifications.ToastNotification]::new($x); "
                "[Windows.UI.Notifications.ToastNotificationManager]::"
                "CreateToastNotifier('J.A.R.V.I.S').Show($n)"
            )
            subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
                env=env, capture_output=True, timeout=8,
            )
        elif sys.platform.startswith("linux"):
            # notify-send 는 argv 전달이라 안전. 단, 옵션 인젝션 방지로 '--' 사용.
            subprocess.run(["notify-send", "--", f"JARVIS: {title}", message],
                          capture_output=True, timeout=5)
    except Exception:
        pass


# ──────────────────────────────────────────────
#  GitHub 모니터
# ──────────────────────────────────────────────
class GitHubMonitor:
    """GitHub 리포 이슈/PR/릴리스 감시"""

    def __init__(self):
        self._seen_issues = BoundedSeen(500)
        self._seen_prs = BoundedSeen(500)
        self._headers = {}
        if GITHUB_TOKEN:
            self._headers["Authorization"] = f"token {GITHUB_TOKEN}"
        self._headers["Accept"] = "application/vnd.github.v3+json"
        self._initialized = False

    def check(self):
        """모든 리포 확인 → 새 이벤트 목록 반환"""
        if not GITHUB_REPOS:
            return []

        events = []
        for repo in GITHUB_REPOS:
            events.extend(self._check_repo(repo))
        return events

    def _check_repo(self, repo):
        events = []
        try:
            # 이슈 확인
            since = (datetime.utcnow() - timedelta(minutes=MONITOR_INTERVAL // 60 + 1)).isoformat() + "Z"
            url = f"https://api.github.com/repos/{repo}/issues"
            params = {"state": "open", "since": since, "per_page": 10, "sort": "created"}
            resp = net.get(url, headers=self._headers, params=params, timeout=(5, 10))

            if resp.status_code == 200:
                for issue in resp.json():
                    iid = issue["number"]
                    is_pr = "pull_request" in issue

                    if is_pr:
                        if iid not in self._seen_prs:
                            if self._initialized:
                                events.append({
                                    "source": "github",
                                    "type": "pr",
                                    "repo": repo.split("/")[-1],
                                    "title": issue["title"],
                                    "number": iid,
                                    "user": issue["user"]["login"],
                                    "url": issue["html_url"],
                                })
                            self._seen_prs.add(iid)
                    else:
                        if iid not in self._seen_issues:
                            if self._initialized:
                                events.append({
                                    "source": "github",
                                    "type": "issue",
                                    "repo": repo.split("/")[-1],
                                    "title": issue["title"],
                                    "number": iid,
                                    "user": issue["user"]["login"],
                                    "url": issue["html_url"],
                                })
                            self._seen_issues.add(iid)
            else:
                _log(f"GitHub API 오류 {resp.status_code}: {repo}")

        except Exception as e:
            _log(f"GitHub 확인 실패 ({repo}): {e}")

        self._initialized = True
        return events


# ──────────────────────────────────────────────
#  Google Play Store 모니터
# ──────────────────────────────────────────────
class PlayStoreMonitor:
    """Google Play 리뷰/평점 감시"""

    def __init__(self):
        self._seen_reviews = BoundedSeen(500)
        self._last_rating = 0
        self._initialized = False

        try:
            from google_play_scraper import app, reviews, Sort
            self._scraper_available = True
            self._Sort = Sort
        except ImportError:
            self._scraper_available = False
            _log("google-play-scraper 미설치 — pip install google-play-scraper")

    def check(self):
        """새 리뷰 확인 → 이벤트 목록 반환"""
        if not PLAY_APP_ID or not self._scraper_available:
            return []

        events = []

        try:
            from google_play_scraper import app, reviews, Sort

            # 앱 정보 (평점 변동 감지)
            try:
                info = app(PLAY_APP_ID, lang="ko", country="kr")
                current_rating = round(info.get("score", 0), 2)

                if self._initialized and self._last_rating > 0:
                    diff = current_rating - self._last_rating
                    if abs(diff) >= 0.05:
                        direction = "상승" if diff > 0 else "하락"
                        events.append({
                            "source": "playstore",
                            "type": "rating_change",
                            "app": info.get("title", PLAY_APP_ID),
                            "rating": current_rating,
                            "change": f"{diff:+.2f}",
                            "direction": direction,
                            "installs": info.get("installs", ""),
                        })
                self._last_rating = current_rating
            except Exception as e:
                _log(f"앱 정보 조회 실패: {e}")

            # 최신 리뷰
            try:
                result, _ = reviews(
                    PLAY_APP_ID,
                    lang="ko",
                    country="kr",
                    sort=self._Sort.NEWEST,
                    count=10,
                )

                for review in result:
                    rid = review.get("reviewId", "")
                    if rid and rid not in self._seen_reviews:
                        if self._initialized:
                            score = review.get("score", 0)
                            stars = "⭐" * score
                            events.append({
                                "source": "playstore",
                                "type": "review",
                                "app": PLAY_APP_ID.split(".")[-1],
                                "user": review.get("userName", "익명"),
                                "score": score,
                                "stars": stars,
                                "text": (review.get("content", "")[:80] or "내용 없음"),
                                "date": str(review.get("at", "")),
                            })
                        self._seen_reviews.add(rid)

            except Exception as e:
                _log(f"리뷰 조회 실패: {e}")

        except Exception as e:
            _log(f"Play Store 확인 실패: {e}")

        self._initialized = True
        return events


# ──────────────────────────────────────────────
#  백그라운드 데몬
# ──────────────────────────────────────────────
class BackgroundMonitor:
    """
    백그라운드 모니터링 데몬
    주기적으로 GitHub/PlayStore를 확인하고 이벤트를 큐에 넣습니다.

    사용법:
        monitor = BackgroundMonitor()
        monitor.start()

        # 메인 루프에서:
        events = monitor.get_events()
        for evt in events:
            speak(evt["message"])
    """

    def __init__(self):
        self._github = GitHubMonitor()
        self._playstore = PlayStoreMonitor()
        self._event_q = deque(maxlen=50)
        self._thread = None
        self._running = False
        self._lock = threading.Lock()
        self._check_count = 0
        self._total_events = 0

        # 활성화 여부
        self.github_enabled = bool(GITHUB_REPOS)
        self.playstore_enabled = bool(PLAY_APP_ID)

    def start(self):
        """데몬 시작"""
        if not self.github_enabled and not self.playstore_enabled:
            _log("모니터링 비활성: GITHUB_REPOS 또는 PLAY_STORE_APP_ID 설정 필요")
            return

        self._running = True
        self._thread = threading.Thread(target=self._daemon_loop, daemon=True)
        self._thread.start()

        sources = []
        if self.github_enabled: sources.append(f"GitHub({','.join(r.split('/')[-1] for r in GITHUB_REPOS)})")
        if self.playstore_enabled: sources.append(f"PlayStore({PLAY_APP_ID.split('.')[-1]})")
        _log(f"백그라운드 데몬 시작: {' + '.join(sources)}, 간격={MONITOR_INTERVAL}초")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    def get_events(self):
        """대기 중인 이벤트 모두 가져오기"""
        events = []
        with self._lock:
            while self._event_q:
                events.append(self._event_q.popleft())
        return events

    def requeue(self, events):
        """아직 처리(발화)하지 못한 이벤트를 큐 앞쪽으로 되돌려 다음 사이클에 재처리.

        메인 루프가 '한 번에 한 내용만' 말하도록, 이번 사이클에 말하지 못한 모니터
        이벤트를 버리지 않고 보존한다(순서 유지)."""
        if not events:
            return
        with self._lock:
            for evt in reversed(events):
                self._event_q.appendleft(evt)

    def _daemon_loop(self):
        """주기적 확인 루프"""
        # 첫 실행: 기존 데이터 초기화 (초기 스캔)
        _log("초기 스캔 중...")
        if self.github_enabled:
            self._github.check()
        if self.playstore_enabled:
            self._playstore.check()
        _log("초기 스캔 완료 — 이후 새 이벤트만 감지")

        while self._running:
            time.sleep(MONITOR_INTERVAL)
            if not self._running:
                break

            self._check_count += 1
            new_events = []

            # GitHub 확인
            if self.github_enabled:
                try:
                    gh_events = self._github.check()
                    new_events.extend(gh_events)
                except Exception as e:
                    _log(f"GitHub 확인 오류: {e}")

            # PlayStore 확인
            if self.playstore_enabled:
                try:
                    ps_events = self._playstore.check()
                    new_events.extend(ps_events)
                except Exception as e:
                    _log(f"PlayStore 확인 오류: {e}")

            # 이벤트 처리
            for evt in new_events:
                msg = self._format_message(evt)
                evt["message"] = msg
                evt["timestamp"] = datetime.now().isoformat()

                with self._lock:
                    self._event_q.append(evt)
                self._total_events += 1

                # 시스템 알림 (웹캠 창이 없어도)
                _system_notify(
                    self._format_title(evt),
                    msg[:100]
                )
                _log(f"🔔 {msg}")

            # 상태 로그 (10회마다)
            if self._check_count % 10 == 0:
                _log(f"확인 #{self._check_count} 완료, 누적 이벤트: {self._total_events}")

    def _format_title(self, evt):
        """알림 제목 생성"""
        source = evt.get("source", "")
        etype = evt.get("type", "")
        if source == "github":
            return f"GitHub {etype.upper()}: {evt.get('repo', '')}"
        elif source == "playstore":
            if etype == "review":
                return f"Play Store 리뷰: {evt.get('stars', '')}"
            elif etype == "rating_change":
                return f"Play Store 평점 변동"
        return "JARVIS 알림"

    def _format_message(self, evt):
        """음성 브리핑용 메시지 생성"""
        source = evt.get("source", "")
        etype = evt.get("type", "")

        if source == "github":
            repo = evt.get("repo", "")
            user = evt.get("user", "")
            title = evt.get("title", "")[:40]
            num = evt.get("number", "")

            if etype == "issue":
                return f"선생님, {repo} 저장소에 새 이슈가 등록되었습니다. #{num} '{title}', 작성자 {user}."
            elif etype == "pr":
                return f"선생님, {repo}에 새 풀 리퀘스트가 올라왔습니다. #{num} '{title}', 작성자 {user}."

        elif source == "playstore":
            if etype == "review":
                score = evt.get("score", 0)
                user = evt.get("user", "익명")
                text = evt.get("text", "")[:30]
                if score <= 2:
                    return f"선생님, 주의가 필요합니다. {score}점 부정적 리뷰가 등록되었습니다. '{text}'"
                elif score >= 4:
                    return f"좋은 소식입니다. {score}점 리뷰가 등록되었습니다. {user}님이 '{text}'"
                else:
                    return f"새 리뷰가 등록되었습니다. {score}점, {user}님."
            elif etype == "rating_change":
                rating = evt.get("rating", 0)
                change = evt.get("change", "")
                direction = evt.get("direction", "")
                return f"선생님, 앱 평점이 {direction}하여 현재 {rating}점입니다. (변동: {change})"

        return f"새 알림: {evt.get('type', 'unknown')}"

    @property
    def status(self):
        return {
            "running": self._running,
            "checks": self._check_count,
            "total_events": self._total_events,
            "pending": len(self._event_q),
            "github": self.github_enabled,
            "playstore": self.playstore_enabled,
            "interval": f"{MONITOR_INTERVAL}초",
        }


# ──────────────────────────────────────────────
#  LLM 도구 정의
# ──────────────────────────────────────────────

MONITOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_monitor_status",
            "description": "백그라운드 모니터링 상태를 조회합니다. GitHub/PlayStore 감시 현황.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_github_now",
            "description": "GitHub 리포를 즉시 확인합니다. 새 이슈나 PR이 있는지 바로 체크.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_playstore_now",
            "description": "Google Play Store 리뷰와 평점을 즉시 확인합니다.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

_monitor_instance = None

def set_monitor_instance(m):
    global _monitor_instance
    _monitor_instance = m

def execute_monitor_tool(tool_name, arguments):
    global _monitor_instance
    if not _monitor_instance:
        return {"success": False, "message": "모니터 없음"}
    if tool_name == "get_monitor_status":
        return {"success": True, **_monitor_instance.status}
    elif tool_name == "check_github_now":
        events = _monitor_instance._github.check()
        return {"success": True, "events": [{"type": e["type"], "repo": e.get("repo",""),
                "title": e.get("title",""), "number": e.get("number","")} for e in events],
                "count": len(events)}
    elif tool_name == "check_playstore_now":
        events = _monitor_instance._playstore.check()
        return {"success": True, "events": [{"type": e["type"], "score": e.get("score",""),
                "text": e.get("text",""), "user": e.get("user","")} for e in events],
                "count": len(events)}
    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}
