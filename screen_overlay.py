"""
J.A.R.V.I.S — 스크린 오버레이 (한글 지원)
"""
from __future__ import annotations

from speech_style import USER_DISPLAY_NAME
import io, json, os, time, threading, cv2, numpy as np
from pathlib import Path
from korean_font import put_text, wrap_text


def _find_korean_font():
    """matplotlib 한글 범례/축 라벨용 OS 내장 폰트 탐색."""
    for path in [
        "/System/Library/Fonts/AppleSDGothicNeo.ttc",
        "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
        "/Library/Fonts/Arial Unicode.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/malgunbd.ttf",
        "C:/Windows/Fonts/NanumGothic.ttf",
        "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    ]:
        if Path(path).exists():
            return path
    return None


def _normalise_tickers(tickers):
    """Gemini/OpenAI 도구 인자에서 들어오는 문자열/배열 티커를 리스트로 정규화."""
    if isinstance(tickers, str):
        # JSON 배열 문자열 또는 "AAPL,NVDA" 둘 다 지원
        try:
            parsed = json.loads(tickers)
            if isinstance(parsed, list):
                tickers = parsed
            else:
                tickers = [tickers]
        except Exception:
            tickers = tickers.split(",")
    aliases = {"하이닉스": "SK하이닉스", "삼전": "삼성전자"}
    out = []
    try:
        from stock_quant import _resolve_ticker
    except Exception:
        _resolve_ticker = lambda x: x
    for t in (tickers or []):
        q = str(t).strip()
        if not q:
            continue
        q = aliases.get(q, q)
        out.append(_resolve_ticker(q))
    return out


def generate_comparison_chart(tickers, indicator="vwma", period="1mo"):
    """
    두 개 이상 종목의 최근 추세 비교 차트를 메모리 버퍼에서 RGBA numpy 이미지로 생성.

    Args:
        tickers: ["005930.KS", "000660.KS"] 형태 티커 리스트
        indicator: "vwma" 또는 "price"
        period: yfinance period (기본 최근 1개월)

    Returns:
        (rgba_image, summary_rows, error)
    """
    tickers = _normalise_tickers(tickers)
    if len(tickers) < 2:
        return None, [], "두 개 이상의 티커가 필요합니다."

    try:
        # matplotlib import 전에 캐시 디렉터리를 고정해야 권한 경고가 나지 않는다.
        os.environ.setdefault("MPLCONFIGDIR", str(Path(os.getenv("AUDIO_SAVE_DIR", "audio_cache")) / ".mplcache"))
        Path(os.environ["MPLCONFIGDIR"]).mkdir(parents=True, exist_ok=True)

        import yfinance as yf
        import pandas as pd
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from stock_quant import calc_vwma
    except Exception as e:
        return None, [], f"차트 의존성 로드 실패: {e}"

    indicator = (indicator or "vwma").lower()
    series_map = {}
    summary_rows = []

    for ticker in tickers[:5]:
        try:
            df = yf.Ticker(ticker).history(period=period, auto_adjust=True)
            if df is None or df.empty or len(df) < 5:
                continue

            if indicator == "price":
                series = df["Close"]
                label_suffix = "Price"
            else:
                series = calc_vwma(df["Close"], df["Volume"], period=20)
                label_suffix = "VWMA20"

            series = series.dropna()
            if series.empty:
                continue

            # 서로 가격 단위가 달라도 시각 비교 가능하도록 시작값 100으로 정규화.
            normalized = series / series.iloc[0] * 100
            series_map[f"{ticker} {label_suffix}"] = normalized

            change = (series.iloc[-1] / series.iloc[0] - 1) * 100
            summary_rows.append([ticker, f"{change:+.2f}%"])
        except Exception:
            continue

    if len(series_map) < 2:
        return None, [], "비교 가능한 종목 데이터가 부족합니다."

    try:
        font_path = _find_korean_font()
        if font_path:
            font_manager.fontManager.addfont(font_path)
            plt.rcParams["font.family"] = font_manager.FontProperties(fname=font_path).get_name()
        plt.rcParams["axes.unicode_minus"] = False

        fig, ax = plt.subplots(figsize=(6.2, 3.6), dpi=120)
        fig.patch.set_alpha(0.0)
        ax.set_facecolor((0.02, 0.02, 0.02, 0.72))

        colors = ["#00D4FF", "#39FF88", "#FFD166", "#FF4D9D", "#B388FF"]
        for idx, (label, series) in enumerate(series_map.items()):
            ax.plot(series.index, series.values, lw=2.4, color=colors[idx % len(colors)], label=label)

        title_metric = "주가" if indicator == "price" else "VWMA20"
        ax.set_title(f"최근 20일 {title_metric} 추세 비교", color="#EAFBFF", fontsize=12, pad=10)
        ax.set_ylabel("Indexed 100", color="#9EEBFF", fontsize=9)
        ax.tick_params(axis="x", colors="#A0A0A0", labelsize=7)
        ax.tick_params(axis="y", colors="#A0A0A0", labelsize=8)
        ax.grid(True, color="#2A5361", alpha=0.34, linewidth=0.7)
        for spine in ax.spines.values():
            spine.set_color("#00D4FF")
            spine.set_alpha(0.55)

        leg = ax.legend(loc="upper left", fontsize=8, framealpha=0.32)
        leg.get_frame().set_facecolor("#050505")
        leg.get_frame().set_edgecolor("#00D4FF")
        for txt in leg.get_texts():
            txt.set_color("#F0F8FF")

        fig.tight_layout(pad=0.8)
        buf = io.BytesIO()
        fig.savefig(buf, format="png", transparent=True)
        plt.close(fig)
        buf.seek(0)

        png = np.frombuffer(buf.getvalue(), dtype=np.uint8)
        rgba = cv2.imdecode(png, cv2.IMREAD_UNCHANGED)
        if rgba is None:
            return None, [], "차트 이미지 디코딩 실패"
        if rgba.shape[2] == 4:
            rgba = cv2.cvtColor(rgba, cv2.COLOR_BGRA2RGBA)
        else:
            rgba = cv2.cvtColor(rgba, cv2.COLOR_BGR2RGBA)
        return rgba, summary_rows, None
    except Exception as e:
        return None, [], f"차트 생성 실패: {e}"

# ══════════════════════════════════════════════════════════════════════
#  전역 HUD 스타일 가이드 (UI 커스텀 단일 진입점)
#  매크로 가드레일 등급(안정/경계/위험) → 테마 색·애니메이션 파라미터를 모듈화.
# ══════════════════════════════════════════════════════════════════════
def _hex_to_bgr(hx: str) -> tuple:
    hx = hx.lstrip("#")
    r, g, b = int(hx[0:2], 16), int(hx[2:4], 16), int(hx[4:6], 16)
    return (b, g, r)   # OpenCV 는 BGR


HUD_STYLE = {
    # 등급별 테마 — 폰트/선/그래프 메인 색이 모두 이 값을 따른다
    "theme": {
        "안정": {"hex": "#00F0FF", "name": "STABLE"},    # 테크니컬 블루
        "경계": {"hex": "#FFAA00", "name": "WARNING"},   # 오렌지 골드
        "위험": {"hex": "#FF0055", "name": "DANGER"},    # 아크 원자로 과부하 레드
    },
    "default_hex": "#00C8FF",
    # 애니메이션 파라미터 (프레임 기반 드로잉)
    "anim": {
        "table_collapse_sec": 0.35,   # 테이블 접힘 시간
        "radar_popup_sec": 0.45,      # 레이더 팝업 시간
        "danger_pulse_hz": 1.5,       # 위험 보더 페이드 인/아웃 주기
        "warning_border_px": 2,
        "danger_border_px": 3,
        "gesture_cooldown_sec": 1.0,  # 제스처 토글 디바운스
    },
    # 레이더(방사형) 차트 파라미터
    "radar": {
        "max_radius_ratio": 0.26,     # 화면 짧은 변 대비 반지름
        "fill_alpha": 0.32,
        "rings": 4,                   # 동심 격자 개수
        "label_size": 13,
    },
}

# 등급 → BGR (HUD_STYLE 에서 파생; 외부 호환용 공개 딕셔너리)
THEME_BGR = {lv: _hex_to_bgr(v["hex"]) for lv, v in HUD_STYLE["theme"].items()}
_DEFAULT_THEME_BGR = _hex_to_bgr(HUD_STYLE["default_hex"])


def _smoothstep(t: float) -> float:
    """0~1 구간 부드러운 ease-in-out (3t²-2t³)."""
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


class ScreenOverlay:
    def __init__(self):
        self._data=None; self._show_until=0; self._lock=threading.Lock()
        self._alert=False  # 긴급 알림 시 화면 테두리 깜빡임
        self._img_cache={}  # {path:(mtime, bgr image)} — 이미지 팝업 캐시
        self._theme_level="안정"  # 매크로 가드레일 전역 테마 등급 (Thread-safe)
        # ── 동적 HUD 애니메이션 상태 (백엔드 스레드가 setter로 갱신, UI 스레드가 render로 보간) ──
        self._radar_target=0.0      # 0=숨김, 1=표시 (목표)
        self._radar_anim=0.0        # 현재 보간값(0~1)
        self._table_collapse=0.0    # 테이블 접힘 진행도(0~1) — 레이더와 연동
        self._radar_data=None       # [{"label","value","max"}] 3레이어 점수
        self._anim_t=time.time()    # 마지막 보간 시각(프레임 dt 계산)
        self._gesture_cd=0.0        # 제스처 토글 쿨다운 타임스탬프

    def set_data(self, data, duration=15, alert=False):
        with self._lock:
            self._data=data; self._show_until=time.time()+duration; self._alert=alert
            # 매트릭스 표가 들어오면 레이더 축(3레이어 점수)을 자동 추출해 보관
            rd=self._extract_radar(data)
            if rd:
                self._radar_data=rd

    @staticmethod
    def _extract_radar(data):
        """overlay_table 데이터에서 레이더 축(label,value,max)을 추출.
        1) 명시적 data["radar"] 우선  2) QUANT/WHALE/SOCIAL 'x/y' 행 파싱."""
        if not isinstance(data, dict):
            return None
        if isinstance(data.get("radar"), list) and data["radar"]:
            return data["radar"]
        axes=[]
        label_map={"QUANT":"기술적 분석","WHALE":"고래 플로우","SOCIAL":"소셜 하이프",
                   "COMPOSITE":"종합","FINAL":"배분"}
        for row in (data.get("rows") or []):
            if len(row)<2:
                continue
            key=str(row[0]).upper()
            val=str(row[1])
            if "/" in val:
                try:
                    num,den=val.split("/")[0:2]
                    v=float(num.strip()); m=float(den.split()[0].strip().rstrip("%"))
                except Exception:
                    continue
                short=next((label_map[k] for k in label_map if k in key), None)
                if short and short not in ("종합","배분") and m>0:
                    axes.append({"label":short,"value":v,"max":m})
        return axes or None

    # ── 레이더/제스처 동적 인터랙션 (Thread-safe) ──
    def set_radar_data(self, axes):
        with self._lock:
            self._radar_data=axes

    def get_radar_data(self):
        """현재 레이더 축 데이터 사본(모바일 스트리밍/외부 연동용, Thread-safe)."""
        with self._lock:
            return list(self._radar_data) if self._radar_data else []

    def show_radar(self, axes=None):
        with self._lock:
            if axes: self._radar_data=axes
            self._radar_target=1.0

    def hide_radar(self):
        with self._lock:
            self._radar_target=0.0

    def toggle_radar(self, axes=None):
        with self._lock:
            if axes: self._radar_data=axes
            self._radar_target=0.0 if self._radar_target>=0.5 else 1.0
            return self._radar_target>=0.5

    def handle_gesture(self, gesture, held=False):
        """제스처 이벤트 → HUD 동적 레이아웃 전환. (손바닥 펼치기=레이더 토글, 주먹=닫기)
        내부 쿨다운으로 디바운스 → 홀드 중 매 프레임 토글되는 것을 방지."""
        if not held:
            return None
        now=time.time()
        with self._lock:
            if now-self._gesture_cd < HUD_STYLE["anim"]["gesture_cooldown_sec"]:
                return None
            if gesture in ("OPEN_PALM","OPEN"):
                self._gesture_cd=now
                self._radar_target=0.0 if self._radar_target>=0.5 else 1.0
                return ("RADAR_TOGGLE", self._radar_target>=0.5)
        return None

    def set_theme(self, risk_level):
        """매크로 가드레일 등급(안정/경계/위험)으로 HUD 전역 테마를 Thread-safe 하게 갱신."""
        if not risk_level:
            return
        with self._lock:
            self._theme_level = risk_level

    def _theme_color(self, level=None):
        """등급 → 테두리/타이틀 BGR 색. 미지정 시 현재 전역 테마."""
        lv = level or self._theme_level
        return THEME_BGR.get(lv, _DEFAULT_THEME_BGR)

    def clear(self):
        with self._lock: self._data=None; self._show_until=0; self._alert=False

    @property
    def is_active(self):
        return self._data is not None and time.time()<self._show_until

    def _advance_animations(self):
        """프레임 dt 기반으로 레이더 팝업/테이블 접힘 보간값을 갱신(부드러운 30~60FPS)."""
        now=time.time()
        dt=now-self._anim_t
        self._anim_t=now
        if dt<=0 or dt>0.5:
            dt=1/60.0   # 첫 프레임/스파이크 보호
        a=HUD_STYLE["anim"]
        # 레이더 팝업 진행
        step_r=dt/max(0.05,a["radar_popup_sec"])
        if self._radar_anim < self._radar_target:
            self._radar_anim=min(self._radar_target, self._radar_anim+step_r)
        elif self._radar_anim > self._radar_target:
            self._radar_anim=max(self._radar_target, self._radar_anim-step_r)
        # 테이블 접힘은 레이더 표시도를 추종
        step_t=dt/max(0.05,a["table_collapse_sec"])
        tgt=self._radar_target
        if self._table_collapse < tgt:
            self._table_collapse=min(tgt, self._table_collapse+step_t)
        elif self._table_collapse > tgt:
            self._table_collapse=max(tgt, self._table_collapse-step_t)

    def render(self, frame):
        with self._lock:
            self._advance_animations()
            data=self._data; alert=self._alert; theme=self._theme_level
            radar_anim=self._radar_anim; collapse=self._table_collapse
            radar_data=self._radar_data
            active = self._data is not None and time.time()<self._show_until
        # 전역 매크로 테마 보더 — 데이터 유무와 무관하게 위험/경계 시 항상 표시.
        # 단, 긴급 alert가 자체 보더를 그릴 때는 중복을 피한다.
        if not (active and alert):
            frame=self._draw_theme_border(frame, theme)
        if active and data:
            if alert: frame=self._draw_alert_border(frame,data)
            dt=data.get("type","table")
            if dt=="table": frame=self._draw_table(frame,data,collapse=collapse)
            elif dt=="stock": frame=self._draw_stock(frame,data)
            elif dt=="info": frame=self._draw_info(frame,data)
            elif dt=="image": frame=self._draw_image(frame,data)
            elif dt=="chart": frame=self.render_chart_overlay(frame,data)
            # 경고 설명문(음성 대신 화면으로만 설명) — 상단 배너 아래 가독성 있는 텍스트 패널
            if data.get("alert_message"):
                frame=self._draw_alert_message(frame,data)
        # 레이더 차트 팝업 (테이블이 접힌 만큼 전면에 등장)
        if radar_anim>0.01 and radar_data:
            frame=self._draw_radar(frame, radar_data, radar_anim, theme)
        return frame

    def _draw_radar(self, frame, axes, anim, level):
        """3레이어 퀀트 점수를 반투명 방사형(Spider/Radar) 그래프로 전면 팝업.
        anim(0~1) 으로 팝업 스케일·알파를 ease 하여 부드럽게 등장."""
        import math
        if not axes:
            return frame
        h,w=frame.shape[:2]
        e=_smoothstep(anim)
        tcol=self._theme_color(level)
        cfg=HUD_STYLE["radar"]
        cx,cy=w//2, h//2
        R=int(min(w,h)*cfg["max_radius_ratio"]*e)
        if R<8:
            return frame
        n=len(axes)
        if n<3:   # 레이더는 최소 3축 — 부족하면 더미 축 보강
            axes=axes+[{"label":"·","value":0,"max":1}]*(3-n); n=3

        def _vert(rad, ratio, ang):
            return (int(cx+rad*ratio*math.cos(ang)), int(cy+rad*ratio*math.sin(ang)))

        angles=[(-math.pi/2)+(2*math.pi*i/n) for i in range(n)]

        # 반투명 배경 디스크
        ov=frame.copy()
        cv2.circle(ov,(cx,cy),R+34,(10,12,15),-1)
        cv2.addWeighted(ov,0.55*e,frame,1-0.55*e,0,frame)

        # 동심 격자 + 축선
        for ring in range(1,cfg["rings"]+1):
            rr=ring/cfg["rings"]
            pts=np.array([_vert(R,rr,a) for a in angles],dtype=np.int32)
            cv2.polylines(frame,[pts],True,(70,80,85),1,cv2.LINE_AA)
        for a in angles:
            cv2.line(frame,(cx,cy),_vert(R,1.0,a),(70,80,85),1,cv2.LINE_AA)

        # 데이터 폴리곤 (테마색 반투명 채움 + 외곽선)
        dpts=[]
        for ax,a in zip(axes,angles):
            try: ratio=max(0.0,min(1.0,float(ax.get("value",0))/max(1e-6,float(ax.get("max",1)))))
            except Exception: ratio=0.0
            dpts.append(_vert(R,ratio*e,a))
        dpoly=np.array(dpts,dtype=np.int32)
        fill=frame.copy()
        cv2.fillPoly(fill,[dpoly],tcol)
        cv2.addWeighted(fill,cfg["fill_alpha"]*e,frame,1-cfg["fill_alpha"]*e,0,frame)
        cv2.polylines(frame,[dpoly],True,tcol,2,cv2.LINE_AA)
        for p in dpts:
            cv2.circle(frame,p,3,tcol,-1,cv2.LINE_AA)

        # 축 라벨 + 수치
        if e>0.6:
            for ax,a in zip(axes,angles):
                lx,ly=_vert(R+24,1.0,a)
                lbl=str(ax.get("label",""))
                val=ax.get("value",0); mx=ax.get("max",0)
                put_text(frame,lbl,(lx-26,ly-4),size=cfg["label_size"],color=tcol,bold=True)
                if mx:
                    put_text(frame,f"{val:.0f}/{mx:.0f}",(lx-26,ly+13),size=11,color=(210,210,210))
            put_text(frame,"QUANT RADAR",(cx-52,cy-R-52),size=14,color=tcol,bold=True)
        return frame

    def _draw_alert_border(self,frame,data=None):
        """긴급 알림 — 화면 테두리 적색 펄스 + 상단 배너(alert_text)"""
        import math
        h,w=frame.shape[:2]
        pulse=abs(math.sin(time.time()*5))           # 0~1 깜빡임
        thick=int(6+pulse*10)
        color=(0,0,int(120+pulse*135))               # 적색 (BGR)
        cv2.rectangle(frame,(0,0),(w-1,h-1),color,thick)
        # 상단 긴급 배너 바
        banner=(data or {}).get("alert_text","⚠ CRITICAL ALERT")
        bar_h=40
        ov=frame.copy()
        cv2.rectangle(ov,(0,0),(w,bar_h),(0,0,int(60+pulse*120)),-1)
        cv2.addWeighted(ov,0.5+pulse*0.3,frame,0.5-pulse*0.3,0,frame)
        put_text(frame,banner,(20,28),size=18,color=(255,255,255),bold=True)
        return frame

    def _draw_alert_message(self,frame,data):
        """경고 설명문을 화면 상단(긴급 배너 아래)에 여러 줄로 표시.
        음성 없이 '스크린을 통해서만' 경고 내용을 전달하기 위한 가독성 패널."""
        msg=str(data.get("alert_message","")).strip()
        if not msg:
            return frame
        h,w=frame.shape[:2]
        pw=min(w-360, 900)   # 우측 지표 표(px≈w-340)와 겹치지 않도록 폭 제한
        if pw < 240:
            pw = min(w-40, 900)
        lines=wrap_text(msg, pw-28, 16)[:7]
        if not lines:
            return frame
        line_h=25
        ph=len(lines)*line_h+22
        x0,y0=20, 46   # 긴급 배너(bar_h=40) 바로 아래
        ov=frame.copy()
        cv2.rectangle(ov,(x0,y0),(x0+pw,y0+ph),(6,6,14),-1)
        cv2.addWeighted(ov,0.74,frame,0.26,0,frame)
        cv2.rectangle(frame,(x0,y0),(x0+pw,y0+ph),(60,60,255),1)
        # 좌측 경고 악센트 바
        cv2.rectangle(frame,(x0,y0),(x0+4,y0+ph),(60,60,255),-1)
        y=y0+24
        for ln in lines:
            put_text(frame,ln,(x0+16,y),size=16,color=(235,235,255))
            y+=line_h
        return frame

    def _draw_theme_border(self,frame,level):
        """매크로 등급 반응형 전역 테두리.
        · 위험: 아크 원자로 경고처럼 알파(투명도)가 부드럽게 페이드 인/아웃 하는 크림슨 보더
        · 경계: 정적 오렌지 골드 얇은 보더
        · 안정: 표시 없음(클린 화면)
        """
        if level not in ("위험","경계"):
            return frame
        import math
        h,w=frame.shape[:2]
        a=HUD_STYLE["anim"]
        if level=="위험":
            # 알파 페이드 인/아웃 (0.35~1.0) — 깜빡임을 투명도로 표현
            pulse=(math.sin(time.time()*2*math.pi*a["danger_pulse_hz"])+1)/2   # 0~1
            alpha=0.35+0.65*pulse
            thick=a["danger_border_px"]
            color=(85,0,255)                              # 크림슨 레드 (#FF0055, BGR)
            ov=frame.copy()
            cv2.rectangle(ov,(0,0),(w-1,h-1),color,thick*2)
            cv2.addWeighted(ov,alpha,frame,1-alpha,0,frame)
            # 또렷한 안쪽 라인 한 겹(테두리 존재감 유지 → 테스트/가시성 보장)
            cv2.rectangle(frame,(2,2),(w-3,h-3),color,thick)
            if pulse>0.5:
                cv2.circle(frame,(w-24,22),7,color,-1)
            put_text(frame,"MACRO: DANGER",(w-190,28),size=14,color=(120,120,255),bold=True)
        else:  # 경계
            cv2.rectangle(frame,(0,0),(w-1,h-1),(0,170,255),a["warning_border_px"])
            put_text(frame,"MACRO: WARNING",(w-205,28),size=14,color=(0,190,255),bold=True)
        return frame

    def _draw_table(self,frame,data,collapse=0.0):
        h,w=frame.shape[:2]
        title=data.get("title",""); headers=data.get("headers",[]); rows=data.get("rows",[])
        if not headers or not rows: return frame
        # 접힘 애니메이션: 완전히 접히면 렌더 생략
        if collapse>=0.985: return frame
        fold=_smoothstep(collapse)            # 0(펼침)~1(접힘)
        vis=1.0-fold                          # 가시도
        # 매크로 등급 반응형 테마 색 (data.theme 우선, 없으면 전역 테마)
        tcol=self._theme_color(data.get("theme"))
        crimson=(85,0,255)
        # 시나리오 스트레스 섹션(예상 MDD / 리스크 가중치)
        scen_rows=data.get("scenario_rows") or []
        scen_alert=data.get("scenario_alert")
        nc=len(headers)
        # 접힘에 따라 보이는 행 수를 줄여 'roll-up' 효과
        nr_full=min(len(rows),10)
        nr=max(0,int(round(nr_full*vis))) if fold>0 else nr_full
        cw=140; pw=min(cw*nc+40,w-60); cw=(pw-40)//nc
        ph=(nr+2)*30+50; px=w-pw-20; py=60
        # 시나리오 섹션은 접힘이 약할 때만 표시
        scen_extra=0
        show_scen = fold<0.25
        if show_scen and scen_rows: scen_extra += 26 + len(scen_rows)*22
        if show_scen and scen_alert: scen_extra += 28
        ph += scen_extra
        # 패널 알파를 가시도에 비례시켜 페이드 아웃
        panel_a=0.85*vis+0.0
        ov=frame.copy()
        cv2.rectangle(ov,(px,py),(px+pw,py+ph),(15,15,15),-1)
        cv2.addWeighted(ov,panel_a,frame,1-panel_a,0,frame)
        cv2.rectangle(frame,(px,py),(px+pw,py+ph),tcol,1)
        y=py+28
        if title: put_text(frame,title,(px+15,y),size=14,color=tcol,bold=True); y+=32
        cv2.rectangle(frame,(px+5,y-16),(px+pw-5,y+6),(40,40,40),-1)
        for i,hd in enumerate(headers):
            put_text(frame,str(hd)[:12],(px+15+i*cw,y),size=12,color=(180,180,180))
        y+=28
        cv2.line(frame,(px+10,y-18),(px+pw-10,y-18),(60,60,60),1)
        for ri,row in enumerate(rows[:nr]):
            if ri%2==0: cv2.rectangle(frame,(px+5,y-16),(px+pw-5,y+6),(25,25,25),-1)
            for ci,cell in enumerate(row):
                txt=str(cell)[:14]; color=(220,220,220)
                if isinstance(cell,str):
                    if any(c in cell for c in ["+","▲"]): color=(60,220,60)
                    elif any(c in cell for c in ["-","▼"]): color=(60,60,255)
                put_text(frame,txt,(px+15+ci*cw,y),size=12,color=color)
            y+=28
        # ── 시나리오 스트레스 섹션 (접힘 시 숨김) ──
        if scen_rows and show_scen:
            cv2.line(frame,(px+10,y-12),(px+pw-10,y-12),tcol,1)
            put_text(frame,"STRESS SCENARIO",(px+15,y+6),size=11,color=tcol,bold=True); y+=26
            for k,v in scen_rows:
                vs=str(v)
                vcol=crimson if "-" in vs else (170,170,170)
                put_text(frame,str(k)[:14],(px+15,y),size=11,color=(150,150,150))
                put_text(frame,vs[:16],(px+pw-150,y),size=11,color=vcol,bold=True)
                y+=22
        if scen_alert and show_scen:
            cv2.rectangle(frame,(px+5,y-12),(px+pw-5,y+10),(0,0,40),-1)
            put_text(frame,str(scen_alert)[:46],(px+12,y+5),size=11,color=crimson,bold=True)
            y+=28
        rem=max(0,self._show_until-time.time())
        put_text(frame,f"[{rem:.0f}s]",(px+pw-40,py+ph-8),size=10,color=(80,80,80))
        return frame

    def _draw_stock(self,frame,data):
        h,w=frame.shape[:2]
        sym=data.get("symbol",""); name=data.get("name",sym)
        price=data.get("price",""); change=data.get("change","")
        chg_pct=data.get("change_percent",""); details=data.get("details",{})
        pw=320; ph=180+len(details)*26; px=w-pw-20; py=60
        # 시선 기반 상황 인지: 이 종목 패널의 화면 Bounding Box 를 등록해
        # 사용자가 패널을 3초 이상 응시하면 gaze_context 가 current_focused_ticker 로 확정.
        if sym:
            try:
                from gaze_context import get_gaze_context
                get_gaze_context().register_region(
                    "stock_panel", (px, py, px+pw, py+ph), sym, name)
            except Exception:
                pass
        ov=frame.copy()
        cv2.rectangle(ov,(px,py),(px+pw,py+ph),(10,10,10),-1)
        cv2.addWeighted(ov,0.88,frame,0.12,0,frame)
        is_up=str(change).startswith("+") or "▲" in str(change)
        bc=(60,220,60) if is_up else (60,60,255)
        cv2.rectangle(frame,(px,py),(px+pw,py+ph),bc,2)
        cv2.rectangle(frame,(px,py),(px+pw,py+42),bc,-1)
        put_text(frame,f"{sym}  {name}",(px+12,py+28),size=14,color=(0,0,0),bold=True)
        y=py+70
        put_text(frame,str(price),(px+15,y),size=22,color=(255,255,255),bold=True)
        cc=(60,220,60) if is_up else (60,60,255)
        put_text(frame,f"{change} ({chg_pct})",(px+15,y+26),size=13,color=cc)
        y+=50; cv2.line(frame,(px+10,y),(px+pw-10,y),(50,50,50),1); y+=20
        for k,v in details.items():
            put_text(frame,str(k),(px+15,y),size=12,color=(140,140,140))
            put_text(frame,str(v),(px+155,y),size=12,color=(220,220,220))
            y+=24
        return frame

    def _draw_info(self,frame,data):
        h,w=frame.shape[:2]
        title=data.get("title","INFO"); items=data.get("items",{})
        pw=300; ph=55+len(items)*26; px=w-pw-20; py=60
        tcol=self._theme_color(data.get("theme"))
        ov=frame.copy()
        cv2.rectangle(ov,(px,py),(px+pw,py+ph),(10,10,10),-1)
        cv2.addWeighted(ov,0.85,frame,0.15,0,frame)
        cv2.rectangle(frame,(px,py),(px+pw,py+ph),tcol,1)
        put_text(frame,title,(px+12,py+25),size=13,color=tcol,bold=True)
        y=py+45
        for k,v in items.items():
            put_text(frame,str(k),(px+15,y),size=12,color=(140,140,140))
            put_text(frame,str(v),(px+140,y),size=12,color=(220,220,220))
            y+=24
        return frame

    def _load_image(self, path):
        """이미지 파일을 mtime 기반 캐시로 로드 (매 프레임 디스크 재읽기 방지)"""
        import os
        try:
            mtime=os.path.getmtime(path)
        except OSError:
            return None
        cached=self._img_cache.get(path)
        if cached and cached[0]==mtime:
            return cached[1]
        img=cv2.imread(path, cv2.IMREAD_COLOR)
        if img is None:
            return None
        self._img_cache={path:(mtime,img)}  # 단일 항목 유지
        return img

    def _draw_image(self,frame,data):
        """화면 우측에 반투명 이미지 팝업(차트) + 선택적 지표 표를 투사"""
        h,w=frame.shape[:2]
        img=self._load_image(data.get("image_path",""))
        title=data.get("title",""); rows=data.get("rows",[])

        pw=min(440,w-60)                       # 팝업 너비
        ix=w-pw-20; iy=64                      # 우측 상단 기준
        ih=0
        if img is not None:
            ih=int(img.shape[0]*(pw/img.shape[1]))
            ih=min(ih,int(h*0.42))
        title_h=30 if title else 0
        rows_h=len(rows)*24+(14 if rows else 0)
        ph=title_h+ih+rows_h+24
        ph=min(ph,h-iy-20)

        # 반투명 배경 패널
        ov=frame.copy()
        cv2.rectangle(ov,(ix,iy),(ix+pw,iy+ph),(12,12,12),-1)
        cv2.addWeighted(ov,0.82,frame,0.18,0,frame)
        cv2.rectangle(frame,(ix,iy),(ix+pw,iy+ph),(0,200,255),1)

        y=iy+22
        if title:
            put_text(frame,title,(ix+14,y),size=15,color=(0,220,255),bold=True); y+=title_h-6

        # 차트 이미지 합성 (반투명)
        if img is not None and ih>0:
            iw=pw-20
            resized=cv2.resize(img,(iw,ih))
            x0=ix+10; y0=y
            y1=min(y0+ih,frame.shape[0]); x1=min(x0+iw,frame.shape[1])
            roi=frame[y0:y1,x0:x1]
            rh,rw=roi.shape[:2]
            if rh>0 and rw>0:
                blended=cv2.addWeighted(resized[:rh,:rw],0.9,roi,0.1,0)
                frame[y0:y1,x0:x1]=blended
            y=y1+12

        # 지표 표
        for kv in rows:
            if len(kv)<2: continue
            k=str(kv[0]); v=str(kv[1])
            color=(220,220,220)
            if any(c in v for c in ["+","▲"]): color=(60,220,60)
            elif any(c in v for c in ["-","▼"]): color=(60,60,255)
            put_text(frame,k,(ix+16,y),size=12,color=(150,150,150))
            put_text(frame,v,(ix+pw-150,y),size=12,color=color)
            y+=24
        return frame

    def render_chart_overlay(self, frame, data):
        """
        RGBA 차트 이미지를 웹캠 BGR 프레임 우측 상단에 반투명 합성.
        set_data 타이머가 끝나면 render() 진입 전 자동 소거되고, D 키는 기존 clear()로 즉시 소거된다.
        """
        chart = data.get("chart_rgba")
        if chart is None:
            return frame

        h, w = frame.shape[:2]
        title = data.get("title", "TREND COMPARISON")
        rows = data.get("rows", [])
        alpha_scale = float(data.get("alpha", 0.6))

        # 우측 상단 HUD 패널 크기
        pw = min(520, w - 60)
        px, py = w - pw - 20, 62
        title_h = 34
        rows_h = min(len(rows), 5) * 24 + (14 if rows else 0)
        max_chart_h = max(160, min(int(h * 0.45), h - py - title_h - rows_h - 35))

        ch, cw = chart.shape[:2]
        scale = min((pw - 24) / cw, max_chart_h / ch)
        rw, rh = max(1, int(cw * scale)), max(1, int(ch * scale))
        resized = cv2.resize(chart, (rw, rh), interpolation=cv2.INTER_AREA)

        ph = title_h + rh + rows_h + 22

        # 반투명 블랙 패널 + 시안 테두리
        ov = frame.copy()
        cv2.rectangle(ov, (px, py), (px + pw, py + ph), (8, 10, 12), -1)
        cv2.addWeighted(ov, 0.68, frame, 0.32, 0, frame)
        cv2.rectangle(frame, (px, py), (px + pw, py + ph), (0, 210, 255), 1)
        put_text(frame, title, (px + 14, py + 24), size=15, color=(0, 230, 255), bold=True)

        # RGBA 알파 합성. rgba 색상 -> BGR 순서로 변환.
        x0 = px + 12
        y0 = py + title_h
        x1, y1 = min(x0 + rw, w), min(y0 + rh, h)
        roi = frame[y0:y1, x0:x1]
        rh2, rw2 = roi.shape[:2]
        if rh2 > 0 and rw2 > 0:
            img = resized[:rh2, :rw2]
            rgb = img[:, :, :3].astype(np.float32)
            bgr = rgb[:, :, ::-1]
            alpha = (img[:, :, 3:4].astype(np.float32) / 255.0) * alpha_scale
            blended = bgr * alpha + roi.astype(np.float32) * (1.0 - alpha)
            frame[y0:y1, x0:x1] = blended.astype(np.uint8)

        # 요약 행 (최근 1개월 변화율)
        y = y0 + rh + 18
        for row in rows[:5]:
            if len(row) < 2:
                continue
            k, v = str(row[0]), str(row[1])
            color = (60, 220, 60) if "+" in v else ((60, 60, 255) if "-" in v else (220, 220, 220))
            put_text(frame, k, (px + 18, y), size=12, color=(150, 160, 165))
            put_text(frame, v, (px + pw - 95, y), size=12, color=color, bold=True)
            y += 24

        rem = max(0, self._show_until - time.time())
        put_text(frame, f"[{rem:.0f}s]", (px + pw - 46, py + ph - 8), size=10, color=(90, 95, 100))
        return frame

DISPLAY_TOOLS=[
    {
        "type":"function","function":{
            "name":"display_table",
            "description":"화면에 표/차트를 표시합니다. 주식,비교,통계,시스템상태 등 시각정보를 보여줄때 사용. type: stock/table/info",
            "parameters":{"type":"object","properties":{
                "type":{"type":"string","description":"표시유형: stock, table, info","enum":["stock","table","info"]},
                "title":{"type":"string","description":"제목"},
                "symbol":{"type":"string","description":"(stock)종목코드"},
                "name":{"type":"string","description":"(stock)종목이름"},
                "price":{"type":"string","description":"(stock)현재가"},
                "change":{"type":"string","description":"(stock)변동액"},
                "change_percent":{"type":"string","description":"(stock)변동률"},
                "details":{"type":"string","description":"JSON {key:value} 세부정보"},
                "headers":{"type":"string","description":"(table) JSON배열 헤더"},
                "rows":{"type":"string","description":"(table) JSON 2차원배열 데이터"},
                "duration":{"type":"string","description":"표시시간(초) 기본15"},
            },"required":["type"]},
        },
    },
    {
        "type":"function","function":{
            "name":"compare_stocks_trend",
            "description":"두 개 이상 종목의 최근 1개월 주가 또는 VWMA20 추세를 matplotlib 차트로 생성해 웹캠 HUD 우측 상단에 반투명 투사합니다. 예: 삼성전자랑 하이닉스 추세 비교.",
            "parameters":{"type":"object","properties":{
                "tickers":{"type":"string","description":"비교할 티커 목록. JSON 배열 문자열 또는 쉼표 구분 문자열. 예: [\"005930.KS\",\"000660.KS\"] 또는 005930.KS,000660.KS"},
                "indicator":{"type":"string","description":"비교 지표: vwma 또는 price. 기본 vwma","enum":["vwma","price"]},
                "duration":{"type":"integer","description":"표시 시간(초). 기본 15"},
            },"required":["tickers"]},
        },
    },
]

_overlay_instance=None
def set_overlay_instance(i): global _overlay_instance; _overlay_instance=i
def execute_display_tool(tool_name, arguments):
    global _overlay_instance
    if tool_name not in ("display_table","compare_stocks_trend"):
        return {"success":False,"message":"알수없는도구"}
    if not _overlay_instance: return {"success":False,"message":"오버레이없음"}
    try:
        if isinstance(arguments,str): arguments=json.loads(arguments)
        if tool_name=="compare_stocks_trend":
            tickers=_normalise_tickers(arguments.get("tickers",[]))
            indicator=arguments.get("indicator","vwma")
            dur=int(arguments.get("duration",15))
            chart, rows, err=generate_comparison_chart(tickers,indicator=indicator)
            if err:
                return {"success":False,"message":err}
            label="VWMA20" if str(indicator).lower()!="price" else "주가"
            data={"type":"chart",
                  "title":f"{label} Trend Sync | {', '.join(tickers[:4])}",
                  "chart_rgba":chart,
                  "rows":rows,
                  "alpha":0.72}
            _overlay_instance.set_data(data,dur)
            return {"success":True,
                    "message":f"{USER_DISPLAY_NAME}, 요청하신 두 종목의 최근 Trend 비교 분석 차트를 화면 우측 상단에 동기화했습니다.",
                    "briefing":f"{USER_DISPLAY_NAME}, 요청하신 두 종목의 최근 Trend 비교 분석 차트를 화면 우측 상단에 동기화했습니다.",
                    "tickers":tickers,
                    "indicator":indicator,
                    "rows":rows}

        dt=arguments.get("type","table"); dur=int(arguments.get("duration","15"))
        data={"type":dt}
        if arguments.get("title"): data["title"]=arguments["title"]
        if dt=="stock":
            for k in ["symbol","name","price","change","change_percent"]:
                data[k]=arguments.get(k,"")
            if arguments.get("details"):
                try: data["details"]=json.loads(arguments["details"])
                except: data["details"]={}
        if arguments.get("headers"):
            try: data["headers"]=json.loads(arguments["headers"])
            except: data["headers"]=[]
        if arguments.get("rows"):
            try: data["rows"]=json.loads(arguments["rows"])
            except: data["rows"]=[]
        if dt=="info" and arguments.get("details"):
            try: data["items"]=json.loads(arguments["details"])
            except: data["items"]={}
        _overlay_instance.set_data(data,dur)
        return {"success":True,"message":f"화면에 {dt} 표시 ({dur}초)"}
    except Exception as e:
        return {"success":False,"message":f"오류:{e}"}
