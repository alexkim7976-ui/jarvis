"""
J.A.R.V.I.S — 채팅 로그 UI (한글 지원)
"""
from __future__ import annotations
import time, threading, cv2, numpy as np
from datetime import datetime
from collections import deque
from korean_font import put_text, wrap_text, text_width

CYAN=(255,220,0); CYAN_DIM=(180,140,0); CYAN_DARK=(100,80,0)
USER_COLOR=(220,180,100); JARVIS_COLOR=(100,255,180)
SYSTEM_COLOR=(0,180,255); PANEL_BG=(15,12,8)

class ChatMessage:
    def __init__(self, role, text, ts=None):
        self.role=role; self.text=text
        self.timestamp=ts or datetime.now()
        self.created_at=time.time()
    @property
    def time_str(self): return self.timestamp.strftime("%H:%M")
    @property
    def age(self): return time.time()-self.created_at
    @property
    def label(self):
        return {"user":"YOU","jarvis":"JARVIS"}.get(self.role,"SYSTEM")
    @property
    def color(self):
        return {"user":USER_COLOR,"jarvis":JARVIS_COLOR}.get(self.role,SYSTEM_COLOR)
    def wrap_lines(self, max_px=210, font_size=13):
        return wrap_text(self.text, max_px, font_size)

class ChatLog:
    def __init__(self, max_messages=50, visible_lines=14):
        self._messages=deque(maxlen=max_messages)
        self._lock=threading.Lock()
        self._visible=visible_lines
        self._scroll=0.0; self._target_scroll=0.0
        self._flash=0
        self.panel_x=10; self.panel_y=115; self.panel_w=250
        self.line_h=17; self.font_size=13
        self.enabled=True

    def add(self, role, text):
        if not text or not text.strip(): return
        with self._lock:
            self._messages.append(ChatMessage(role, text.strip()))
            self._flash=time.time()
            self._target_scroll=max(0, self._total_lines()-self._visible)

    def clear(self):
        with self._lock:
            self._messages.clear(); self._scroll=0; self._target_scroll=0

    @property
    def message_count(self): return len(self._messages)

    def _total_lines(self):
        t=0
        for m in self._messages:
            t+=1+len(m.wrap_lines(self.panel_w-40, self.font_size))+1
        return t

    def render(self, frame):
        if not self.enabled: return frame
        h,w=frame.shape[:2]; t=time.time()
        with self._lock: msgs=list(self._messages)
        if not msgs: return frame

        self._scroll+=(self._target_scroll-self._scroll)*0.15
        px=self.panel_x; py=self.panel_y; pw=self.panel_w
        panel_h=self._visible*self.line_h+40

        # 배경
        ov=frame.copy()
        cv2.rectangle(ov,(px,py),(px+pw,py+panel_h),PANEL_BG,-1)
        cv2.addWeighted(ov,0.65,frame,0.35,0,frame)
        cv2.rectangle(frame,(px,py),(px+pw,py+panel_h),CYAN_DARK,1,cv2.LINE_AA)

        # 헤더
        put_text(frame,"COMM LOG",(px+8,py+16),size=12,color=CYAN,bold=True)
        put_text(frame,f"[{len(msgs)}]",(px+pw-35,py+16),size=10,color=CYAN_DARK)
        cv2.line(frame,(px+5,py+22),(px+pw-5,py+22),CYAN_DARK,1)

        # 플래시
        if t-self._flash<0.5:
            a=0.3*(1-(t-self._flash)/0.5); ov2=frame.copy()
            cv2.rectangle(ov2,(px,py),(px+pw,py+panel_h),JARVIS_COLOR,-1)
            cv2.addWeighted(ov2,a,frame,1-a,0,frame)

        # 메시지 펼치기
        all_lines=[]
        for msg in msgs:
            age=msg.age
            all_lines.append(("header",f"[{msg.time_str}] {msg.label}",msg.color,age))
            for ln in msg.wrap_lines(pw-40, self.font_size):
                all_lines.append(("body",ln,msg.color,age))
            all_lines.append(("gap","",CYAN_DARK,age))

        vs=int(self._scroll); ve=vs+self._visible
        visible=all_lines[max(0,vs):ve]

        cy_start=py+28; cy_end=py+panel_h-8; y=cy_start
        for kind,text,color,age in visible:
            if kind=="gap": y+=self.line_h//2; continue
            if y+self.line_h>cy_end: break

            fade=1.0
            d=y-cy_start
            if d<25: fade=max(0.2,d/25)
            bright=1.0+0.3*(1-age/3) if age<3 else 1.0
            fc=tuple(min(255,int(c*fade*bright)) for c in color)

            if kind=="header":
                put_text(frame,text,(px+12,y),size=self.font_size-1,color=fc,bold=True)
                if "YOU" in text: cv2.circle(frame,(px+6,y-4),2,USER_COLOR,-1)
                elif "JARVIS" in text: cv2.circle(frame,(px+6,y-4),2,JARVIS_COLOR,-1)
            else:
                put_text(frame,text,(px+18,y),size=self.font_size,color=fc)
            y+=self.line_h

        # 스크롤바
        total=len(all_lines)
        if total>self._visible:
            ratio=self._scroll/max(1,total-self._visible)
            ih=max(20,int((cy_end-cy_start)*self._visible/total))
            iy=int(cy_start+ratio*((cy_end-cy_start)-ih))
            cv2.line(frame,(px+pw-4,cy_start),(px+pw-4,cy_end),CYAN_DARK,1)
            cv2.line(frame,(px+pw-4,iy),(px+pw-4,iy+ih),CYAN_DIM,2)

        return frame

    def scroll_up(self): self._target_scroll=max(0,self._target_scroll-3)
    def scroll_down(self):
        mx=max(0,self._total_lines()-self._visible)
        self._target_scroll=min(mx,self._target_scroll+3)
    def scroll_to_bottom(self):
        self._target_scroll=max(0,self._total_lines()-self._visible)

    def get_last_jarvis(self):
        """마지막 JARVIS 메시지 객체 반환 (수정용)"""
        with self._lock:
            for msg in reversed(list(self._messages)):
                if msg.role=="jarvis": return msg
        return None

    def update_message(self, msg: ChatMessage, new_text: str):
        """ChatMessage 텍스트 업데이트 + 스크롤 하단 이동"""
        with self._lock:
            msg.text=new_text.strip()
            self._target_scroll=max(0,self._total_lines()-self._visible)
