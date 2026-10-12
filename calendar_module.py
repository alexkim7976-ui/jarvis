"""
J.A.R.V.I.S — Google Calendar Module
pip install google-auth google-auth-oauthlib google-api-python-client
"""
from __future__ import annotations
import os,json
from datetime import datetime, timedelta
from pathlib import Path

CREDENTIALS_FILE=Path("credentials.json"); TOKEN_FILE=Path("token.json")
SCOPES=["https://www.googleapis.com/auth/calendar"]
_service=None

def _get_service():
    global _service
    if _service: return _service
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError: return None
    if not CREDENTIALS_FILE.exists(): return None
    creds=None
    if TOKEN_FILE.exists(): creds=Credentials.from_authorized_user_file(str(TOKEN_FILE),SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token: creds.refresh(Request())
        else:
            flow=InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE),SCOPES)
            creds=flow.run_local_server(port=0)
        with open(TOKEN_FILE,"w") as f: f.write(creds.to_json())
    _service=build("calendar","v3",credentials=creds); return _service

def _parse_dt(date_str, time_str=None):
    now=datetime.now(); dl=date_str.lower().strip()
    if dl in ("오늘","today"): d=now.date()
    elif dl in ("내일","tomorrow"): d=(now+timedelta(days=1)).date()
    elif dl in ("모레",): d=(now+timedelta(days=2)).date()
    else:
        try:
            if len(date_str)<=5: d=datetime.strptime(f"{now.year}-{date_str}","%Y-%m-%d").date()
            else: d=datetime.strptime(date_str,"%Y-%m-%d").date()
        except: d=now.date()
    if time_str:
        try:
            ts=time_str.replace("시",":00").replace("분","")
            if ":" not in ts: ts+=":00"
            t=datetime.strptime(ts.strip(),"%H:%M").time()
            return datetime.combine(d,t)
        except: pass
    return datetime.combine(d,datetime.min.time())

def get_events(date="오늘",max_results=10):
    if os.getenv("JARVIS_MAC_CALENDAR","1").strip().lower() not in ("0","false","off"):
        try:
            from mac_calendar import get_mac_events, mac_calendar_available
            if mac_calendar_available():
                r=get_mac_events(date=date,max_results=max_results)
                if r.get("success"): return r
        except Exception: pass
    svc=_get_service()
    if not svc: return _no_svc()
    try:
        base=_parse_dt(date)
        if date.lower() in ("이번주","this week"):
            wd=base.weekday(); start=base-timedelta(days=wd); end=start+timedelta(days=7)
        else: start=base; end=base+timedelta(days=1)
        tmin=start.isoformat()+"T00:00:00+09:00"; tmax=end.isoformat()+"T00:00:00+09:00"
        r=svc.events().list(calendarId="primary",timeMin=tmin,timeMax=tmax,
            maxResults=int(max_results),singleEvents=True,orderBy="startTime").execute()
        events=[]
        for ev in r.get("items",[]):
            sr=ev.get("start",{}); sd=sr.get("dateTime",sr.get("date",""))
            er=ev.get("end",{}); ed=er.get("dateTime",er.get("date",""))
            if "T" in sd:
                try: st=datetime.fromisoformat(sd.replace("Z","+00:00")); ss=st.strftime("%H:%M")
                except: ss=sd
            else: ss="종일"
            if "T" in ed:
                try: et=datetime.fromisoformat(ed.replace("Z","+00:00")); es=et.strftime("%H:%M")
                except: es=ed
            else: es=""
            events.append({"title":ev.get("summary","제목없음"),"start":ss,"end":es,
                          "location":ev.get("location","")})
        return {"success":True,"date":date,"count":len(events),"events":events,
                "message":f"{date} 일정 {len(events)}개" if events else f"{date}에 일정이 없습니다."}
    except Exception as e: return {"success":False,"message":f"조회오류:{e}"}

def create_event(title,date,start_time=None,end_time=None,description="",location=""):
    svc=_get_service()
    if not svc: return _no_svc()
    try:
        sdt=_parse_dt(date,start_time)
        edt=_parse_dt(date,end_time) if end_time else sdt+timedelta(hours=1)
        ev={"summary":title,"location":location,"description":description,
            "start":{"dateTime":sdt.strftime("%Y-%m-%dT%H:%M:%S"),"timeZone":"Asia/Seoul"},
            "end":{"dateTime":edt.strftime("%Y-%m-%dT%H:%M:%S"),"timeZone":"Asia/Seoul"}}
        cr=svc.events().insert(calendarId="primary",body=ev).execute()
        return {"success":True,"message":f"'{title}' 일정이 {date} {start_time or ''}에 등록되었습니다.",
                "event_id":cr.get("id")}
    except Exception as e: return {"success":False,"message":f"추가오류:{e}"}

def delete_event(title,date="오늘"):
    svc=_get_service()
    if not svc: return _no_svc()
    try:
        base=_parse_dt(date)
        tmin=base.isoformat()+"T00:00:00+09:00"; tmax=(base+timedelta(days=1)).isoformat()+"T00:00:00+09:00"
        r=svc.events().list(calendarId="primary",timeMin=tmin,timeMax=tmax,singleEvents=True).execute()
        for ev in r.get("items",[]):
            if title.lower() in ev.get("summary","").lower():
                svc.events().delete(calendarId="primary",eventId=ev["id"]).execute()
                return {"success":True,"message":f"'{ev['summary']}' 삭제 완료."}
        return {"success":False,"message":f"'{title}' 일정을 찾을 수 없습니다."}
    except Exception as e: return {"success":False,"message":f"삭제오류:{e}"}

def get_upcoming(hours=24,max_results=5):
    if os.getenv("JARVIS_MAC_CALENDAR","1").strip().lower() not in ("0","false","off"):
        try:
            from mac_calendar import get_mac_upcoming, mac_calendar_available
            if mac_calendar_available():
                r=get_mac_upcoming(hours=hours,max_results=max_results)
                if r.get("success"): return r
        except Exception: pass
    svc=_get_service()
    if not svc: return _no_svc()
    try:
        now=datetime.now()
        tmin=now.isoformat()+"+09:00"; tmax=(now+timedelta(hours=int(hours))).isoformat()+"+09:00"
        r=svc.events().list(calendarId="primary",timeMin=tmin,timeMax=tmax,
            maxResults=int(max_results),singleEvents=True,orderBy="startTime").execute()
        events=[]
        for ev in r.get("items",[]):
            sr=ev.get("start",{}); sd=sr.get("dateTime",sr.get("date",""))
            if "T" in sd:
                try:
                    st=datetime.fromisoformat(sd.replace("Z","+00:00"))
                    rem=(st-now).total_seconds()/60
                    ts=f"{int(rem)}분후" if rem<60 else f"{int(rem/60)}시간후"
                    ss=st.strftime("%H:%M")
                except: ss=sd; ts=""
            else: ss="종일"; ts=""
            events.append({"title":ev.get("summary","제목없음"),"time":ss,"remaining":ts})
        return {"success":True,"count":len(events),"events":events,
                "message":f"다가오는 일정 {len(events)}개" if events else "다가오는 일정 없음"}
    except Exception as e: return {"success":False,"message":f"조회오류:{e}"}

def _no_svc():
    if not CREDENTIALS_FILE.exists():
        return {"success":False,"message":"credentials.json 파일이 없습니다. Google Cloud Console에서 OAuth 클라이언트를 생성하고 다운로드하세요."}
    return {"success":False,"message":"pip install google-auth google-auth-oauthlib google-api-python-client"}

CALENDAR_TOOLS=[
    {"type":"function","function":{"name":"get_events","description":"Google 캘린더에서 특정 날짜의 일정을 조회합니다. 수업, 미팅, 약속 등.",
        "parameters":{"type":"object","properties":{
            "date":{"type":"string","description":"날짜: 오늘,내일,모레,이번주,YYYY-MM-DD"},
            "max_results":{"type":"integer","description":"최대 결과수","default":10}},"required":["date"]}}},
    {"type":"function","function":{"name":"create_event","description":"Google 캘린더에 새 일정을 추가합니다.",
        "parameters":{"type":"object","properties":{
            "title":{"type":"string","description":"일정 제목"},
            "date":{"type":"string","description":"날짜"},
            "start_time":{"type":"string","description":"시작시간: 15:00, 3시"},
            "end_time":{"type":"string","description":"종료시간 (생략시 1시간후)"},
            "description":{"type":"string","description":"설명"},
            "location":{"type":"string","description":"장소"}},"required":["title","date"]}}},
    {"type":"function","function":{"name":"delete_event","description":"Google 캘린더에서 일정을 삭제합니다.",
        "parameters":{"type":"object","properties":{
            "title":{"type":"string","description":"삭제할 일정 제목"},
            "date":{"type":"string","description":"날짜","default":"오늘"}},"required":["title"]}}},
    {"type":"function","function":{"name":"get_upcoming","description":"다가오는 일정을 조회합니다.",
        "parameters":{"type":"object","properties":{
            "hours":{"type":"integer","description":"몇시간 이내","default":24}},"required":[]}}},
]

CALENDAR_TOOL_MAP={"get_events":get_events,"create_event":create_event,
                   "delete_event":delete_event,"get_upcoming":get_upcoming}

def execute_calendar_tool(tool_name, arguments):
    func=CALENDAR_TOOL_MAP.get(tool_name)
    if not func: return {"success":False,"message":f"알수없는도구:{tool_name}"}
    try:
        if isinstance(arguments,str): arguments=json.loads(arguments)
        for k in ("max_results","hours"):
            if k in arguments:
                try: arguments[k]=int(arguments[k])
                except: pass
        return func(**arguments)
    except Exception as e: return {"success":False,"message":f"오류:{e}"}
