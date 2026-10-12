"""
J.A.R.V.I.S — 스토니브룩 대학 학사 일정 & SOLAR 행정 감시 모듈
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Fall 2026 학기(2026.8 뉴욕 출국) 준비를 위해 Gmail/Calendar 를 백그라운드로
감시한다. 스토니브룩 행정 메일(국제학생처 VIS·I-20·비자·SOLAR Hold·수강신청·
기숙사·예방접종)이 새로 도착하면 긴급 이벤트를 메인 루프 큐로 push 한다.

설계 원칙:
  1. 넌블로킹  — 모든 Google API 호출은 이 모듈의 데몬 스레드 안에서만 완결.
                메인 OpenCV/마이크 루프는 큐 폴링만 하므로 프레임 드롭 없음.
  2. 비대화형 — 데몬 스레드는 run_local_server() 같은 대화형 OAuth 를 절대
                호출하지 않는다. 기존 토큰 로드/refresh 만 수행.
                최초 인증은 'python3 campus_agent.py --auth' 단독 실행으로.
  3. 방어적   — google 라이브러리 미설치·토큰 없음·네트워크 실패 시 시스템이
                뻗지 않고 경고 로그만 남긴 채 폴백(idle) 모드로 대기.

인증 파일:
  .credentials/credentials.json   (OAuth 클라이언트; 없으면 루트 credentials.json 재사용)
  .credentials/campus_token.json  (Gmail+Calendar readonly 토큰 — --auth 로 생성)

.env:
  CAMPUS_AGENT_ENABLED=true
  CAMPUS_CHECK_INTERVAL=600        (초, 기본 10분)
  CAMPUS_LOOKBACK_DAYS=3           (Gmail 조회 범위)
  CAMPUS_GMAIL_ACCOUNT=  (로그인에서 입력한 학교 메일. 비어 있으면 메일 감시 안 함)

의존성:
  pip install google-api-python-client google-auth google-auth-oauthlib
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME
import json
import base64
import html
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

# ──────────────────────────────────────────────
#  설정
# ──────────────────────────────────────────────
CAMPUS_AGENT_ENABLED  = os.getenv("CAMPUS_AGENT_ENABLED", "true").lower() == "true"
CAMPUS_CHECK_INTERVAL = int(os.getenv("CAMPUS_CHECK_INTERVAL", "600"))   # 초 (기본 10분)
CAMPUS_LOOKBACK_DAYS  = int(os.getenv("CAMPUS_LOOKBACK_DAYS", "3"))      # Gmail 조회 범위(일)
CAMPUS_GMAIL_ACCOUNT  = os.getenv("CAMPUS_GMAIL_ACCOUNT", "").strip().lower()
CAMPUS_COURSE_SENDER = os.getenv("CAMPUS_COURSE_SENDER", "").strip().lower()
CAMPUS_CALL_TO = os.getenv("CAMPUS_CALL_TO", "").strip()
CAMPUS_CALL_ALERTS = os.getenv("CAMPUS_CALL_ALERTS", "true").strip().lower() == "true"

CRED_DIR   = Path(".credentials")
CRED_DIR.mkdir(exist_ok=True)
TOKEN_FILE = CRED_DIR / "campus_token.json"
SEEN_FILE  = CRED_DIR / "campus_seen.json"
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
_SEEN_MAX = 500   # 중복 방지 캐시 상한

# Gmail 필터 키워드 (q 쿼리 + 분류용)
SBU_DOMAIN   = "stonybrook.edu"
GMAIL_KEYWORDS = ['"SOLAR"', '"I-20"', '"Visa"', '"Immunization"',
                  '"Housing"', '"Enrollment"', '"Advising"', '"Hold"']

# 이 발신자에게서 온 과제·수업 메일만 전화 알림 대상
COURSE_MAIL_KEYWORDS = (
    "homework", "assignment", "coursework", "class", "course", "lecture",
    "syllabus", "quiz", "exam", "test", "midterm", "final", "project",
    "lab", "recitation", "discussion", "grade", "due", "deadline",
    "수업", "강의", "과제", "숙제", "시험", "퀴즈", "강의계획서", "마감",
)

# 분류 규칙 (우선순위 순) — (카테고리, [소문자 매칭어], 긴급여부, 한국어 부서/요약, 영문 alert 태그)
CLASSIFY_RULES = [
    ("I-20/비자",  ["i-20", "i20", "ds-2019", "sevis", "visa", "international student", "vis ", "isso"],
     True,  "국제학생처(VIS)",        "SBU VIS Dept - I-20/Visa Document"),
    ("행정 Hold",  ["hold", "registration hold", "account hold", "bursar hold", "service indicator"],
     True,  "행정 보류(Hold)",        "SOLAR Administrative Hold"),
    ("학사지도",   ["advising", "advisor", "academic advisement", "course plan"],
     True,  "학사지도(Advising)",     "Academic Advising Required"),
    ("예방접종",   ["immunization", "vaccine", "vaccination", "health requirement", "mmr"],
     True,  "보건 요건(Immunization)", "Immunization Record Required"),
    ("수강신청",   ["enrollment", "registration", "course registration", "add/drop", "wait list", "waitlist"],
     True,  "수강신청",               "Course Enrollment Deadline"),
    ("기숙사",     ["housing", "dormitory", "residence hall", "move-in", "roommate"],
     False, "기숙사(Housing)",        "Housing / Move-in Notice"),
    ("등록금",     ["bursar", "tuition", "payment due", "financial aid", "billing"],
     False, "등록금(Bursar)",         "Bursar / Tuition Notice"),
    ("SOLAR",      ["solar"],
     False, "SOLAR 시스템",           "SOLAR System Notice"),
]

# 캘린더 학사 키워드 (하이라이트용)
_ACADEMIC_KW = [
    "registration", "수강", "enroll", "deadline", "마감", "tuition", "등록금",
    "housing", "기숙사", "advising", "지도", "orientation", "오리엔테이션",
    "i-20", "visa", "비자", "immunization", "예방접종", "solar", "add/drop",
    "final", "midterm", "semester", "학기", "class", "수업", "move-in", "hold",
]


def _log(level: str, msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    icons = {"INFO": "◈", "OK": "✓", "WARN": "⚠", "ALERT": "🚨", "AUTH": "🔒"}
    print(f"[{ts}] {icons.get(level, '·')} [캠퍼스] {msg}")


def campus_mail_account() -> str:
    return os.getenv("CAMPUS_GMAIL_ACCOUNT", "").strip().lower()


def _extract_emails(header_val: str) -> list[str]:
    if not header_val:
        return []
    return re.findall(r"[\w.+-]+@[\w.-]+\.\w+", header_val.lower())


def message_for_campus_account(meta: dict) -> bool:
    """수신 주소가 로그인한 학교 메일과 일치하는 메일만 True. 메일이 없으면 감시하지 않는다."""
    acct = campus_mail_account()
    if not acct:
        return False
    recipients: list[str] = []
    for key in ("to", "cc", "delivered_to", "x_original_to", "envelope_to"):
        recipients.extend(_extract_emails(meta.get(key, "")))
    return acct in recipients


def is_course_mail_from_configured_sender(meta: dict) -> bool:
    """요청된 발신 주소의 메일 중 숙제·수업 관련 내용만 전화 알림 대상으로 판별."""
    sender = CAMPUS_COURSE_SENDER
    if not sender:
        return False
    if sender not in _extract_emails(meta.get("from", "")):
        return False
    content = f"{meta.get('subject', '')} {meta.get('snippet', '')}".casefold()
    return any(keyword.casefold() in content for keyword in COURSE_MAIL_KEYWORDS)


def _place_course_email_call(message: str) -> str:
    """Twilio Voice를 통해 수업·과제 메일 알림 전화를 건다."""
    if not CAMPUS_CALL_ALERTS:
        return "skipped: call alerts disabled"
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()
    if not (sid and token and from_number and CAMPUS_CALL_TO):
        return "skipped: Twilio Voice configuration required"
    twiml = (
        "<Response><Say language=\"ko-KR\">"
        + html.escape(message)
        + "</Say></Response>"
    )
    body = urllib.parse.urlencode({
        "To": CAMPUS_CALL_TO,
        "From": from_number,
        "Twiml": twiml,
    }).encode("utf-8")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            result = json.loads(response.read().decode("utf-8"))
        return f"started: {result.get('sid', 'unknown')}" if result.get("sid") else "failed"
    except Exception:
        # 예외 문자열에 인증 정보가 포함될 수 있어 상세 URL/오류는 기록하지 않는다.
        return "failed"


def _find_client_secret() -> Path | None:
    """OAuth 클라이언트 시크릿 탐색 — .credentials/ 우선, 루트 폴백."""
    for p in (CRED_DIR / "credentials.json", Path("credentials.json")):
        if p.exists():
            return p
    return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CampusAgent
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class CampusAgent:
    """Gmail/Calendar 백그라운드 감시 데몬 (stock_monitor 와 동일 인터페이스)."""

    def __init__(self, interval: int = CAMPUS_CHECK_INTERVAL):
        self.interval = max(60, interval)
        self.enabled  = CAMPUS_AGENT_ENABLED

        self._event_q: deque[dict] = deque(maxlen=50)
        self._lock    = threading.Lock()
        self._thread  = None
        self._running = False

        self._gmail = None
        self._cal   = None
        self._svc_ready = False
        self._svc_checked_at = 0.0

        self._seen_ids = self._load_seen()
        self._last_mail_log: list[dict] = []   # 최근 감지 메일 (status 표시용)
        self._check_count = 0
        self._total_alerts = 0
        self._last_error = ""

    # ── 라이프사이클 ──
    def start(self):
        if not self.enabled:
            _log("WARN", "비활성(CAMPUS_AGENT_ENABLED=false)")
            return
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        _log("OK", f"감시 시작 — 간격={self.interval}초 | 조회범위={CAMPUS_LOOKBACK_DAYS}일"
                 f" | 수신={campus_mail_account() or '전체'}")

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)

    # ── 이벤트 큐 인터페이스 (stock_monitor 호환) ──
    def get_events(self) -> list[dict]:
        events = []
        with self._lock:
            while self._event_q:
                events.append(self._event_q.popleft())
        return events

    def requeue(self, events: list[dict]):
        with self._lock:
            for evt in reversed(events):
                self._event_q.appendleft(evt)

    # ── Google 서비스 빌드 (비대화형) ──
    def _ensure_services(self) -> bool:
        """
        기존 토큰을 로드해 gmail/calendar 서비스를 준비한다.
        대화형 OAuth(run_local_server)는 절대 호출하지 않는다 — 데몬 안전.
        성공 시 True.
        """
        if self._svc_ready:
            return True
        # 너무 잦은 재시도 방지 (60초 간격)
        now = time.time()
        if now - self._svc_checked_at < 60 and self._svc_checked_at > 0:
            return False
        self._svc_checked_at = now

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError:
            self._last_error = "google-api-python-client 미설치"
            _log("WARN", f"{self._last_error} — 폴백 모드 (pip install google-api-python-client google-auth-oauthlib)")
            return False

        if not TOKEN_FILE.exists():
            self._last_error = "토큰 없음 (--auth 필요)"
            _log("WARN", f"{TOKEN_FILE} 없음 — 'python3 campus_agent.py --auth' 로 1회 인증 필요. 폴백 모드.")
            return False

        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            if not creds.valid:
                if creds.expired and creds.refresh_token:
                    creds.refresh(Request())           # 네트워크 호출 — 데몬 OK (비대화형)
                    TOKEN_FILE.write_text(creds.to_json())
                    _log("OK", "토큰 자동 갱신 완료")
                else:
                    self._last_error = "토큰 무효 (재인증 필요)"
                    _log("WARN", f"{self._last_error} — 폴백 모드")
                    return False
            self._gmail = build("gmail", "v1", credentials=creds, cache_discovery=False)
            self._cal   = build("calendar", "v3", credentials=creds, cache_discovery=False)
            self._svc_ready = True
            self._last_error = ""
            _log("OK", "Gmail/Calendar 서비스 연결 성공")
            return True
        except Exception as e:
            self._last_error = f"서비스 빌드 실패: {e}"
            _log("WARN", f"{self._last_error} — 폴백 모드")
            return False

    # ── 감시 루프 ──
    def _monitor_loop(self):
        time.sleep(10)  # 부팅 직후 부하 분산
        while self._running:
            self._check_count += 1
            try:
                if self._ensure_services():
                    self._check_gmail()
            except Exception as e:
                self._last_error = str(e)
                _log("WARN", f"감시 사이클 오류: {e}")
            # 다음 사이클까지 1초 단위 분할 대기 (종료 신호 즉시 반영)
            slept = 0
            while self._running and slept < self.interval:
                time.sleep(1)
                slept += 1

    # ── Gmail 점검 ──
    def _check_gmail(self):
        q = self._build_query()
        try:
            resp = self._gmail.users().messages().list(
                userId="me", q=q, maxResults=15).execute()
        except Exception as e:
            self._last_error = f"Gmail list 실패: {e}"
            _log("WARN", self._last_error)
            self._svc_ready = False   # 토큰 만료 등 → 다음 사이클 재빌드
            return

        msgs = resp.get("messages", [])
        if not msgs:
            return

        new_count = 0
        for m in msgs:
            mid = m.get("id")
            if not mid or mid in self._seen_ids:
                continue
            meta = self._get_message_meta(mid)
            if not meta:
                continue
            course_mail = is_course_mail_from_configured_sender(meta)
            if not message_for_campus_account(meta):
                if not course_mail:
                    self._mark_seen(mid)
                    continue
            elif not course_mail:
                # 기존 캠퍼스 메일 감시 경로는 이전처럼 대상 수신 주소만 처리
                pass
            self._mark_seen(mid)
            new_count += 1

            if course_mail:
                category, dept_ko, urgent, alert_tag = (
                    "숙제·수업", "수업/과제", True, "Course / Homework Email",
                )
            else:
                category, dept_ko, urgent, alert_tag = self._classify(
                    meta["subject"], meta["from"], meta["snippet"])

            self._last_mail_log.insert(0, {
                "subject": meta["subject"][:60],
                "from":    meta["from"][:40],
                "category": category,
                "urgent":  urgent,
                "at":      datetime.now().strftime("%m/%d %H:%M"),
            })
            self._last_mail_log = self._last_mail_log[:10]

            evt = self._make_event(meta, category, dept_ko, urgent, alert_tag)
            if course_mail:
                alert_message = (
                    f"JARVIS 알림입니다. 수업 또는 숙제 관련 메일이 왔습니다. "
                    f"보낸 사람은 {meta['from'][:120]}입니다. "
                    f"제목은 {meta['subject'][:160]}입니다. 이메일을 확인해 주세요."
                )
                try:
                    message_status = _place_course_email_call(alert_message)
                    _log("INFO", f"수업 메일 전화 알림: {message_status}")
                except Exception as e:
                    message_status = f"failed: {type(e).__name__}"
                    _log("WARN", f"수업 메일 전화 발신 실패: {type(e).__name__}")
                evt["phone_call_status"] = message_status
            with self._lock:
                self._event_q.append(evt)
            self._total_alerts += 1
            _log("ALERT" if urgent else "INFO",
                 f"{'긴급 ' if urgent else ''}메일 감지 [{category}] {meta['subject'][:40]}")

        if new_count:
            self._save_seen()

    def _build_query(self) -> str:
        if CAMPUS_COURSE_SENDER:
            # sender 검색은 정확한 주소로 제한하고 수업·과제 단어를 Gmail에서 선필터링
            academic_query = "{" + " ".join(COURSE_MAIL_KEYWORDS) + "}"
            return (
                f"from:{CAMPUS_COURSE_SENDER} {academic_query} "
                f"newer_than:{CAMPUS_LOOKBACK_DAYS}d"
                f" OR (({self._campus_query_terms()}) newer_than:{CAMPUS_LOOKBACK_DAYS}d)"
            )
        return f"({self._campus_query_terms()}) newer_than:{CAMPUS_LOOKBACK_DAYS}d"

    def _campus_query_terms(self) -> str:
        kw = " OR ".join([f"from:{SBU_DOMAIN}"] + GMAIL_KEYWORDS)
        acct = campus_mail_account()
        if acct:
            recipient = f"(deliveredto:{acct} OR to:{acct})"
            return f"{recipient} ({kw})"
        return f"({kw})"

    def _get_message_meta(self, msg_id: str) -> dict | None:
        try:
            msg = self._gmail.users().messages().get(
                userId="me", id=msg_id, format="metadata",
                metadataHeaders=[
                    "From", "To", "Cc", "Subject", "Date",
                    "Delivered-To", "X-Original-To", "Envelope-To",
                ]).execute()
            headers = {h["name"].lower(): h["value"]
                       for h in msg.get("payload", {}).get("headers", [])}
            return {
                "id":      msg_id,
                "from":    headers.get("from", ""),
                "to":      headers.get("to", ""),
                "cc":      headers.get("cc", ""),
                "delivered_to": headers.get("delivered-to", ""),
                "x_original_to": headers.get("x-original-to", ""),
                "envelope_to": headers.get("envelope-to", ""),
                "subject": headers.get("subject", "(제목 없음)"),
                "date":    headers.get("date", ""),
                "snippet": msg.get("snippet", ""),
            }
        except Exception as e:
            _log("WARN", f"메일 메타 조회 실패({msg_id}): {e}")
            return None

    # ── 분류 ──
    def _classify(self, subject: str, sender: str, snippet: str):
        """(category, dept_ko, urgent, alert_tag) 반환."""
        hay = f"{subject} {sender} {snippet}".lower()
        for category, keys, urgent, dept_ko, alert_tag in CLASSIFY_RULES:
            if any(k in hay for k in keys):
                return category, dept_ko, urgent, alert_tag
        # 키워드 매칭 실패하지만 SBU 도메인 → 일반 공지
        return "SBU 일반", "스토니브룩", False, "Stony Brook University Notice"

    # ── 이벤트 패키지 생성 ──
    def _make_event(self, meta: dict, category: str, dept_ko: str,
                    urgent: bool, alert_tag: str) -> dict:
        subject = meta["subject"]
        # TTS 대사
        if category == "숙제·수업":
            message = (f"{USER_DISPLAY_NAME}, 수업 또는 숙제 관련 메일이 도착했습니다. "
                       f"제목은 '{subject[:60]}'입니다. 이메일을 확인해 주세요.")
        elif urgent:
            message = (f"{USER_DISPLAY_NAME}, 스토니브룩 {dept_ko}에서 {category} 관련 긴급 메일이 "
                       f"도착했습니다. 제목은 '{subject[:40]}'입니다. "
                       f"행정 보류(Hold) 리스크가 있을 수 있으니 확인이 필요합니다.")
        else:
            message = (f"{USER_DISPLAY_NAME}, 스토니브룩 {dept_ko}에서 {category} 관련 메일이 도착했습니다. "
                       f"제목은 '{subject[:40]}'입니다.")

        alert_text = f"CAMPUS ALERT: {alert_tag}"
        overlay_table = {
            "type":    "table",
            "title":   f"🎓 캠퍼스 알림 — {category}",
            "headers": ["항목", "내용"],
            "rows": [
                ["부서",   dept_ko],
                ["분류",   category + (" ⚠긴급" if urgent else "")],
                ["제목",   subject[:38]],
                ["발신",   meta["from"][:34]],
            ],
            "alert_text": alert_text,
        }
        return {
            "source":        "campus_agent",
            "msg_id":        meta["id"],
            "category":      category,
            "dept":          dept_ko,
            "urgent":        urgent,
            "subject":       subject,
            "sender":        meta["from"],
            "snippet":       meta["snippet"][:160],
            "message":       message,
            "alert_text":    alert_text,
            "overlay_table": overlay_table,
            "timestamp":     datetime.now().isoformat(),
        }

    # ── 학사 일정 (Calendar) ──
    def get_academic_schedule(self, days: int = 60, max_results: int = 12) -> dict:
        """다가오는 학사 일정/데드라인 조회 (Calendar readonly). 동기 호출 — 도구 스레드에서 실행."""
        if not self._ensure_services() or self._cal is None:
            return {"success": False,
                    "message": f"캘린더 연동 대기 중입니다. ({self._last_error or '인증 필요'})"}
        try:
            now = datetime.utcnow()
            tmin = now.isoformat() + "Z"
            tmax = (now + timedelta(days=int(days))).isoformat() + "Z"
            r = self._cal.events().list(
                calendarId="primary", timeMin=tmin, timeMax=tmax,
                maxResults=int(max_results), singleEvents=True,
                orderBy="startTime").execute()
            rows, events = [], []
            for ev in r.get("items", []):
                title = ev.get("summary", "(제목 없음)")
                sr = ev.get("start", {})
                sd = sr.get("dateTime", sr.get("date", ""))
                day = sd[:10] if sd else ""
                is_academic = any(k in title.lower() for k in _ACADEMIC_KW)
                tag = "🎓" if is_academic else "·"
                rows.append([day, title[:34], tag])
                events.append({"date": day, "title": title, "academic": is_academic})
            academic_n = sum(1 for e in events if e["academic"])
            msg = (f"향후 {days}일 일정 {len(events)}건 중 학사 관련 {academic_n}건입니다."
                   if events else f"향후 {days}일 내 등록된 일정이 없습니다.")
            return {
                "success": True,
                "count": len(events),
                "academic_count": academic_n,
                "events": events,
                "message": msg,
                "overlay_table": {
                    "type": "table",
                    "title": f"🎓 다가오는 학사 일정 ({days}일)",
                    "headers": ["날짜", "일정", "구분"],
                    "rows": rows or [["-", "일정 없음", "·"]],
                },
            }
        except Exception as e:
            self._svc_ready = False
            return {"success": False, "message": f"학사 일정 조회 오류: {e}"}

    def get_next_class(self) -> dict:
        """다음 수업 — Campus Quest JSON 우선, Google Calendar 폴백."""
        try:
            from campus_schedule import next_class_from_schedule, format_next_class_briefing, is_class_like_event
            local = next_class_from_schedule()
            if local:
                msg = format_next_class_briefing(local)
                loc = local.get("location") or local.get("building") or "-"
                return {
                    "success": True,
                    "course": local.get("course"),
                    "location": loc,
                    "start_time": local.get("start_time"),
                    "starts_in_min": local.get("starts_in_min"),
                    "source": local.get("source"),
                    "message": msg,
                    "overlay_table": {
                        "type": "table",
                        "title": "🎓 다음 수업 (Campus Quest)",
                        "headers": ["과목", "장소", "시작", "남은 시간"],
                        "rows": [[local.get("course", "-"), loc, local.get("start_time", "-"),
                                  f"{local.get('starts_in_min', 0)}분"]],
                    },
                }
        except Exception:
            pass

        if not self._ensure_services() or self._cal is None:
            return {"success": False,
                    "message": "수업 시간표가 없습니다. data/campus_schedule.json 을 추가하거나 캘린더를 연동해 주세요."}
        try:
            from campus_schedule import is_class_like_event, format_next_class_briefing
            from datetime import timezone
            now_utc = datetime.now(timezone.utc)
            tmin = now_utc.isoformat().replace("+00:00", "Z")
            tmax = (now_utc + timedelta(hours=36)).isoformat().replace("+00:00", "Z")
            r = self._cal.events().list(
                calendarId="primary", timeMin=tmin, timeMax=tmax,
                maxResults=20, singleEvents=True, orderBy="startTime").execute()
            for ev in r.get("items", []):
                title = ev.get("summary", "")
                loc = ev.get("location", "")
                if not is_class_like_event(title, loc):
                    continue
                sr = ev.get("start", {})
                sd = sr.get("dateTime", sr.get("date", ""))
                if not sd or "T" not in sd:
                    continue
                try:
                    st = datetime.fromisoformat(sd.replace("Z", "+00:00"))
                    if st.tzinfo is None:
                        st = st.replace(tzinfo=timezone.utc)
                except ValueError:
                    continue
                delta_min = int(max(0, (st - now_utc).total_seconds() / 60))
                local_st = st.astimezone()
                info = {
                    "course": title,
                    "location": loc or "장소 미등록",
                    "start_time": local_st.strftime("%H:%M"),
                    "starts_in_min": delta_min,
                    "source": "google_calendar",
                }
                msg = format_next_class_briefing(info)
                return {
                    "success": True,
                    **info,
                    "message": msg,
                    "overlay_table": {
                        "type": "table",
                        "title": "🎓 다음 수업",
                        "headers": ["과목", "장소", "시작", "남은 시간"],
                        "rows": [[title[:40], loc or "-", info["start_time"], f"{delta_min}분"]],
                    },
                }
            return {"success": False, "message": "다음 36시간 내 수업 일정을 찾지 못했습니다."}
        except Exception as e:
            return {"success": False, "message": f"다음 수업 조회 오류: {e}"}

    # ── 중복 방지 캐시 ──
    def _load_seen(self) -> set:
        if SEEN_FILE.exists():
            try:
                return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
            except Exception:
                return set()
        return set()

    def _mark_seen(self, mid: str):
        self._seen_ids.add(mid)

    def _save_seen(self):
        try:
            ids = list(self._seen_ids)[-_SEEN_MAX:]
            self._seen_ids = set(ids)
            SEEN_FILE.write_text(json.dumps(ids, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            _log("WARN", f"seen 캐시 저장 실패: {e}")

    @property
    def status(self) -> dict:
        return {
            "enabled":       self.enabled,
            "running":       self._running,
            "services_ready": self._svc_ready,
            "interval":      f"{self.interval}초",
            "checks":        self._check_count,
            "total_alerts":  self._total_alerts,
            "seen_cached":   len(self._seen_ids),
            "last_error":    self._last_error or "정상",
            "mail_account":  campus_mail_account(),
            "recent_mail":   self._last_mail_log[:5],
            "token_exists":  TOKEN_FILE.exists(),
        }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  LLM 도구 정의 (Gemini Function Calling)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CAMPUS_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "check_upcoming_academic_schedule",
            "description": (
                "다가오는 주요 학사 일정과 수강신청·기숙사·등록금 데드라인을 Google Calendar에서 "
                "조회합니다. '학사 일정 알려줘', '수강신청 언제야', '다가오는 데드라인' 등에 사용."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days": {"type": "integer",
                             "description": "조회할 향후 일수 (기본 60)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_next_class_location",
            "description": (
                "다음 수업의 과목명·강의실·시작 시간을 조회합니다. "
                "'다음 수업 어디야', '수업 몇 시야', '강의실 어디' 등 SBU 캠퍼스 수업 질문에 사용."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "refresh_campus_agent_status",
            "description": (
                "캠퍼스 감시 에이전트의 Gmail/캘린더 연동 무결성과 "
                f"최근 감지된 스토니브룩 행정 메일({campus_mail_account() or '설정된 계정'}) "
                "로그를 확인합니다. '캠퍼스 감시 상태', '메일 왔어?', '스토니브룩 메일 확인' 등에 사용. "
                "다른 개인 메일함은 보고하지 않습니다."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]

_agent_instance: CampusAgent | None = None


def set_campus_agent_instance(inst: CampusAgent):
    global _agent_instance
    _agent_instance = inst


def execute_campus_tool(tool_name: str, arguments: dict) -> dict:
    global _agent_instance
    if not _agent_instance:
        return {"success": False, "message": "캠퍼스 에이전트 없음"}

    if tool_name == "check_upcoming_academic_schedule":
        days = int(arguments.get("days", 60))
        return _agent_instance.get_academic_schedule(days=days)

    if tool_name == "get_next_class_location":
        return _agent_instance.get_next_class()

    if tool_name == "refresh_campus_agent_status":
        st = _agent_instance.status
        acct = st.get("mail_account") or campus_mail_account()
        recent = st.get("recent_mail", [])
        acct_label = acct or "전체"
        if recent:
            lines = "; ".join(f"[{m['category']}] {m['subject']}" for m in recent[:3])
            msg = (f"캠퍼스 감시 {'정상 작동 중' if st['services_ready'] else '폴백 모드(인증 대기)'}. "
                   f"수신 주소 {acct_label} 기준. "
                   f"누적 감지 {st['total_alerts']}건. 최근: {lines}")
        else:
            msg = (f"캠퍼스 감시 {'정상 작동 중' if st['services_ready'] else '폴백 모드(인증 대기)'}. "
                   f"수신 주소 {acct_label} 로 온 행정 메일은 아직 없습니다. "
                   f"상태: {st['last_error']}")
        return {"success": True, **st, "message": msg,
                "overlay_table": {
                    "type": "table",
                    "title": "🎓 캠퍼스 에이전트 상태",
                    "headers": ["항목", "값"],
                    "rows": [
                        ["연동",     "정상" if st["services_ready"] else "폴백(인증 필요)"],
                        ["수신 주소", acct_label],
                        ["점검 횟수", str(st["checks"])],
                        ["누적 알림", str(st["total_alerts"])],
                        ["캐시",     f"{st['seen_cached']}건"],
                        ["상태",     st["last_error"]],
                    ],
                }}

    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  단독 실행 — 최초 OAuth 인증 (대화형) & 테스트
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _interactive_auth():
    """브라우저로 1회 OAuth 인증 → .credentials/campus_token.json 생성."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("✗ google-auth-oauthlib 미설치: pip install google-auth-oauthlib google-api-python-client")
        return
    secret = _find_client_secret()
    if not secret:
        print("✗ OAuth 클라이언트 시크릿이 없습니다.")
        print("  Google Cloud Console → OAuth 클라이언트(데스크톱) 생성 후")
        print(f"  '{CRED_DIR / 'credentials.json'}' 또는 './credentials.json' 으로 저장하세요.")
        print("  필요 API: Gmail API, Google Calendar API 활성화")
        return
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_FILE.write_text(creds.to_json())
    print(f"✓ 인증 완료 → {TOKEN_FILE}")
    print("  이제 jarvis_v2.py 실행 시 캠퍼스 감시가 자동 작동합니다.")


if __name__ == "__main__":
    import sys
    if "--auth" in sys.argv:
        _interactive_auth()
    else:
        agent = CampusAgent()
        print("상태:", json.dumps(agent.status, ensure_ascii=False, indent=2))
        if agent._ensure_services():
            print("\n▶ 학사 일정 테스트:")
            print(json.dumps(agent.get_academic_schedule(days=90), ensure_ascii=False, indent=2))
            print("\n▶ Gmail 점검 테스트:")
            agent._check_gmail()
            for evt in agent.get_events():
                print(f"  [{evt['category']}] {evt['subject'][:50]} (urgent={evt['urgent']})")
        else:
            print("\n폴백 모드 — 'python3 campus_agent.py --auth' 로 먼저 인증하세요.")
