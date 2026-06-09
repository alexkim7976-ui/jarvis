"""
J.A.R.V.I.S — Iron Man HUD
"""
from __future__ import annotations
import math, time, random, struct, threading
from datetime import datetime
import cv2, numpy as np
from korean_font import put_text, wrap_text

CYAN=(255,220,0); CYAN_BRIGHT=(255,255,0); CYAN_DIM=(180,140,0)
CYAN_DARK=(100,80,0); RED_ALERT=(50,50,255); GREEN=(50,255,50)
AMBER=(0,180,255); PANEL_BG=(15,12,8)

class IronManHUD:
    def __init__(self):
        self._audio_levels=[0]*40; self._audio_lock=threading.Lock()
        self._frame_count=0; self._boot_time=time.time()
        self._scan_y=0
        self._particles=[]
        for _ in range(15):
            self._particles.append({"x":random.uniform(0,1),"y":random.uniform(0,1),
                "vx":random.uniform(-0.001,0.001),"vy":random.uniform(-0.0005,0.0005),
                "size":random.randint(1,3),"alpha":random.uniform(0.2,0.6)})

    def update_audio_level(self, audio_data):
        with self._audio_lock:
            if audio_data and len(audio_data)>=2:
                count=len(audio_data)//2
                try:
                    shorts=struct.unpack(f"{count}h",audio_data)
                    cs=max(1,count//40); levels=[]
                    for i in range(40):
                        s=i*cs; e=min(s+cs,count)
                        if s<count:
                            chunk=shorts[s:e]
                            levels.append(min(int((sum(x*x for x in chunk)/len(chunk))**0.5),8000))
                        else: levels.append(0)
                    self._audio_levels=levels
                except: pass
            else: self._audio_levels=[max(0,l-200) for l in self._audio_levels]

    def push_audio_level(self, rms):
        with self._audio_lock:
            self._audio_levels.pop(0)
            self._audio_levels.append(min(int(rms), 8000))

    def render(self, frame, state="IDLE", transcript="", answer="", face_count=0, llm_provider="gemini"):
        self._frame_count+=1; h,w=frame.shape[:2]; t=time.time()
        tint=np.zeros_like(frame); tint[:]=(12,8,4)
        cv2.addWeighted(frame,0.92,tint,0.08,0,frame)
        self._draw_scan_line(frame,h,w)
        self._draw_corner_frame(frame,h,w)
        self._draw_top_bar(frame,w,state,llm_provider)
        self._draw_clock(frame,w)
        self._draw_arc_reactor(frame,h,w,state,t)
        self._draw_waveform(frame,h,w,state)
        self._draw_status_panel(frame,h,w,state,face_count)
        self._draw_bottom_text(frame,h,w,transcript,answer)
        self._draw_particles(frame,h,w)
        self._draw_system_info(frame,h,w)
        if state=="RECORDING":
            pulse=int(abs(math.sin(t*4))*80)+40
            cv2.rectangle(frame,(0,0),(w-1,h-1),(0,0,pulse),2)
        return frame

    def _draw_top_bar(self,frame,w,state,llm):
        ov=frame.copy(); cv2.rectangle(ov,(0,0),(w,48),PANEL_BG,-1)
        cv2.addWeighted(ov,0.7,frame,0.3,0,frame)
        cv2.line(frame,(0,48),(w,48),CYAN_DARK,1)
        cv2.putText(frame,"J.A.R.V.I.S",(20,32),cv2.FONT_HERSHEY_DUPLEX,0.8,CYAN_BRIGHT,1,cv2.LINE_AA)
        cv2.putText(frame,"v3.1",(165,32),cv2.FONT_HERSHEY_SIMPLEX,0.4,CYAN_DIM,1,cv2.LINE_AA)
        sc={"IDLE":CYAN_DIM,"RECORDING":RED_ALERT,"PROCESSING":AMBER,"SPEAKING":GREEN}
        cx=w-190; t=time.time()
        if state=="RECORDING":
            p=int(abs(math.sin(t*4))*255); cv2.circle(frame,(cx,26),5,(0,0,p),-1,cv2.LINE_AA)
        elif state=="PROCESSING":
            a=int(t*360)%360; cv2.ellipse(frame,(cx,26),(6,6),0,a,a+270,AMBER,2,cv2.LINE_AA)
        elif state=="SPEAKING":
            p=int(abs(math.sin(t*3))*200)+55; cv2.circle(frame,(cx,26),5,(0,p,0),-1,cv2.LINE_AA)
        else: cv2.circle(frame,(cx,26),4,CYAN_DIM,1,cv2.LINE_AA)
        cv2.putText(frame,state,(cx+14,32),cv2.FONT_HERSHEY_SIMPLEX,0.5,sc.get(state,CYAN_DIM),1,cv2.LINE_AA)
        cv2.putText(frame,llm.upper(),(w-60,32),cv2.FONT_HERSHEY_SIMPLEX,0.35,CYAN_DARK,1,cv2.LINE_AA)

    def _draw_clock(self,frame,w):
        now=datetime.now()
        cv2.putText(frame,now.strftime("%H:%M:%S"),(w//2-60,32),cv2.FONT_HERSHEY_DUPLEX,0.65,CYAN,1,cv2.LINE_AA)
        cv2.putText(frame,now.strftime("%Y.%m.%d %a").upper(),(w//2-58,44),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DIM,1,cv2.LINE_AA)

    def _draw_arc_reactor(self,frame,h,w,state,t):
        cx,cy=80,h-120; r1,r2,r3=45,35,22
        ao=int(t*30)%360
        for i in range(6):
            a=ao+i*60; cv2.ellipse(frame,(cx,cy),(r1,r1),0,a,a+40,CYAN_DIM,1,cv2.LINE_AA)
        ao2=int(-t*45)%360
        for i in range(8):
            a=ao2+i*45; cv2.ellipse(frame,(cx,cy),(r2,r2),0,a,a+25,CYAN,1,cv2.LINE_AA)
        ic={"IDLE":CYAN_DARK,"RECORDING":RED_ALERT,"PROCESSING":AMBER,"SPEAKING":GREEN}
        c=ic.get(state,CYAN_DARK)
        if state in ("RECORDING","SPEAKING"):
            p=abs(math.sin(t*3)); rp=int(r3+p*5); cv2.circle(frame,(cx,cy),rp,c,1,cv2.LINE_AA)
        cv2.circle(frame,(cx,cy),r3,c,1,cv2.LINE_AA)
        cv2.circle(frame,(cx,cy),3,CYAN_BRIGHT,-1,cv2.LINE_AA)
        for i in range(12):
            ang=math.radians(i*30+ao)
            x1=int(cx+(r1+3)*math.cos(ang)); y1=int(cy+(r1+3)*math.sin(ang))
            x2=int(cx+(r1+8)*math.cos(ang)); y2=int(cy+(r1+8)*math.sin(ang))
            cv2.line(frame,(x1,y1),(x2,y2),CYAN_DARK,1,cv2.LINE_AA)
        cv2.putText(frame,"ARC REACTOR",(cx-38,cy+r1+22),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DARK,1,cv2.LINE_AA)

    def _draw_waveform(self,frame,h,w,state):
        wy=h-85; wx=150; ww=w-300; bw=max(2,ww//42)
        with self._audio_lock: levels=list(self._audio_levels)
        ov=frame.copy()
        cv2.rectangle(ov,(wx-10,wy-40),(wx+ww+10,wy+10),PANEL_BG,-1)
        cv2.addWeighted(ov,0.5,frame,0.5,0,frame)
        cv2.putText(frame,"AUDIO",(wx,wy-45),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DARK,1,cv2.LINE_AA)
        cv2.line(frame,(wx,wy),(wx+ww,wy),CYAN_DARK,1,cv2.LINE_AA)
        t=time.time()
        for i,lv in enumerate(levels[:40]):
            x=wx+i*bw
            if state=="RECORDING":
                bh=max(2,int((lv/8000)*35)); c=RED_ALERT
            elif state=="SPEAKING":
                v=math.sin(t*8+i*0.5)*15+math.sin(t*12+i*0.3)*8; bh=max(2,int(abs(v))); c=GREEN
            elif state=="PROCESSING":
                d=abs(i-(int(t*20)%40)); bh=max(2,int(15*max(0,1-d/8))); c=AMBER
            else:
                bh=max(1,int(abs(math.sin(t*2+i*0.8))*3)); c=CYAN_DARK
            cv2.line(frame,(x,wy-bh),(x,wy+bh),c,bw-1,cv2.LINE_AA)

    def _draw_status_panel(self,frame,h,w,state,fc):
        px=w-180; py=60; ov=frame.copy()
        cv2.rectangle(ov,(px-10,py),(w-10,py+160),PANEL_BG,-1)
        cv2.addWeighted(ov,0.55,frame,0.45,0,frame)
        cv2.line(frame,(px-10,py),(px-10,py+160),CYAN_DARK,1,cv2.LINE_AA)
        y=py+18; cv2.putText(frame,"SYSTEM STATUS",(px,y),cv2.FONT_HERSHEY_SIMPLEX,0.32,CYAN,1,cv2.LINE_AA)
        y+=8; cv2.line(frame,(px,y),(w-15,y),CYAN_DARK,1)
        y+=20; ut=int(time.time()-self._boot_time); m,s=divmod(ut,60); hr,m=divmod(m,60)
        cv2.putText(frame,"UPTIME",(px,y),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DIM,1,cv2.LINE_AA)
        cv2.putText(frame,f"{hr:02d}:{m:02d}:{s:02d}",(px+65,y),cv2.FONT_HERSHEY_SIMPLEX,0.30,CYAN,1,cv2.LINE_AA)
        y+=22; cv2.putText(frame,"STT",(px,y),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DIM,1,cv2.LINE_AA)
        cv2.putText(frame,"WHISPER",(px+65,y),cv2.FONT_HERSHEY_SIMPLEX,0.30,GREEN,1,cv2.LINE_AA)
        y+=22; cv2.putText(frame,"TTS",(px,y),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DIM,1,cv2.LINE_AA)
        cv2.putText(frame,"FISH",(px+65,y),cv2.FONT_HERSHEY_SIMPLEX,0.30,GREEN,1,cv2.LINE_AA)
        y+=22; cv2.putText(frame,"FACES",(px,y),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DIM,1,cv2.LINE_AA)
        cv2.putText(frame,str(fc),(px+65,y),cv2.FONT_HERSHEY_SIMPLEX,0.30,GREEN if fc>0 else CYAN_DIM,1,cv2.LINE_AA)
        y+=22; cv2.putText(frame,"FRAME",(px,y),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DIM,1,cv2.LINE_AA)
        cv2.putText(frame,str(self._frame_count),(px+65,y),cv2.FONT_HERSHEY_SIMPLEX,0.30,CYAN_DIM,1,cv2.LINE_AA)

    def _draw_corner_frame(self,frame,h,w):
        L=40; c=CYAN_DIM
        cv2.line(frame,(4,4),(4+L,4),c,1,cv2.LINE_AA); cv2.line(frame,(4,4),(4,4+L),c,1,cv2.LINE_AA)
        cv2.line(frame,(w-5,4),(w-5-L,4),c,1,cv2.LINE_AA); cv2.line(frame,(w-5,4),(w-5,4+L),c,1,cv2.LINE_AA)
        cv2.line(frame,(4,h-5),(4+L,h-5),c,1,cv2.LINE_AA); cv2.line(frame,(4,h-5),(4,h-5-L),c,1,cv2.LINE_AA)
        cv2.line(frame,(w-5,h-5),(w-5-L,h-5),c,1,cv2.LINE_AA); cv2.line(frame,(w-5,h-5),(w-5,h-5-L),c,1,cv2.LINE_AA)
        cx,cy=w//2,h//2
        cv2.line(frame,(cx-12,cy),(cx+12,cy),CYAN_DARK,1,cv2.LINE_AA)
        cv2.line(frame,(cx,cy-12),(cx,cy+12),CYAN_DARK,1,cv2.LINE_AA)

    def _draw_scan_line(self,frame,h,w):
        self._scan_y=(self._scan_y+3)%h; y=self._scan_y
        ov=frame.copy(); cv2.line(ov,(0,y),(w,y),CYAN,1)
        cv2.addWeighted(ov,0.15,frame,0.85,0,frame)
        for i in range(1,15):
            ty=y-i*2
            if ty<0: ty+=h
            a=0.06*(1-i/15); ov2=frame.copy()
            cv2.line(ov2,(0,ty),(w,ty),CYAN_DARK,1)
            cv2.addWeighted(ov2,a,frame,1-a,0,frame)

    def _draw_bottom_text(self,frame,h,w,transcript,answer):
        ov=frame.copy(); cv2.rectangle(ov,(0,h-60),(w,h),PANEL_BG,-1)
        cv2.addWeighted(ov,0.6,frame,0.4,0,frame)
        cv2.line(frame,(0,h-60),(w,h-60),CYAN_DARK,1)
        if transcript:
            txt=f"> {transcript[:50]}{'...'if len(transcript)>50 else''}"
            put_text(frame,txt,(20,h-40),size=14,color=CYAN_DIM)
        if answer:
            lines=wrap_text(answer,w-40,14)
            for i,ln in enumerate(lines[:2]):
                put_text(frame,ln,(20,h-20+i*16),size=14,color=CYAN)
        cv2.putText(frame,"SPACE:Rec  R:Face  W:Wake  L:Log  D:Overlay  Q:Quit",(w-380,h-65),cv2.FONT_HERSHEY_SIMPLEX,0.26,CYAN_DARK,1,cv2.LINE_AA)

    def _draw_particles(self,frame,h,w):
        for p in self._particles:
            p["x"]+=p["vx"]; p["y"]+=p["vy"]
            if p["x"]<0: p["x"]=1
            if p["x"]>1: p["x"]=0
            if p["y"]<0: p["y"]=1
            if p["y"]>1: p["y"]=0
            px=int(p["x"]*w); py=int(p["y"]*h)
            c=tuple(int(v*p["alpha"]) for v in CYAN_DIM)
            cv2.circle(frame,(px,py),p["size"],c,-1,cv2.LINE_AA)

    def _draw_system_info(self,frame,h,w):
        for i,(l,v) in enumerate([("CAM","1280x720"),("MIC","16kHz"),("NET","ONLINE")]):
            y=60+i*18
            cv2.putText(frame,l,(15,y),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DARK,1,cv2.LINE_AA)
            cv2.putText(frame,v,(50,y),cv2.FONT_HERSHEY_SIMPLEX,0.28,CYAN_DIM,1,cv2.LINE_AA)
