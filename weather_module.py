

from __future__ import annotations

import os
import json
import requests
from datetime import datetime, timezone, timedelta
try:
    import net  # 연결 재사용·재시도·적응형 타임아웃 공용 세션
except Exception:
    net = requests

WEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")

# 날씨 아이콘 → 텍스트 매핑
WEATHER_ICONS = {
    "01d": "☀ 맑음", "01n": "🌙 맑음",
    "02d": "⛅ 구름 조금", "02n": "☁ 구름 조금",
    "03d": "☁ 구름 많음", "03n": "☁ 구름 많음",
    "04d": "☁ 흐림", "04n": "☁ 흐림",
    "09d": "🌧 소나기", "09n": "🌧 소나기",
    "10d": "🌦 비", "10n": "🌧 비",
    "11d": "⛈ 뇌우", "11n": "⛈ 뇌우",
    "13d": "🌨 눈", "13n": "🌨 눈",
    "50d": "🌫 안개", "50n": "🌫 안개",
}

# 풍향 변환
def wind_direction(deg):
    dirs = ["북", "북북동", "북동", "동북동",
            "동", "동남동", "남동", "남남동",
            "남", "남남서", "남서", "서남서",
            "서", "서북서", "북서", "북북서"]
    idx = round(deg / 22.5) % 16
    return dirs[idx]


def get_location_by_ip():
    """IP 기반 대략적 위치·시간대 자동 감지 (API 키 불필요). 실패 시 None.
    1순위 ipwho.is(HTTPS), 폴백 ip-api.com(HTTP). 둘 다 offset(UTC로부터 초)을 제공."""
    for url in ("https://ipwho.is/",
                "http://ip-api.com/json/?fields=status,city,regionName,country,timezone,offset"):
        try:
            d = net.get(url, timeout=(3, 6)).json()
            if "success" in d:                       # ipwho.is 형식
                if not d.get("success"): continue
                tz = d.get("timezone", {}) or {}
                return {"city": d.get("city", ""), "region": d.get("region", ""),
                        "country": d.get("country", ""),
                        "timezone": tz.get("id", ""), "offset": tz.get("offset", 0)}
            if d.get("status") == "success":         # ip-api.com 형식
                return {"city": d.get("city", ""), "region": d.get("regionName", ""),
                        "country": d.get("country", ""),
                        "timezone": d.get("timezone", ""), "offset": d.get("offset", 0)}
        except Exception:
            continue
    return None


def local_time_greeting(offset_seconds=None):
    """현지 시각 기준 (인사말, 시각문자열, 시간대라벨) 반환. offset 없으면 시스템 로컬 시간 사용."""
    if offset_seconds is None:
        now = datetime.now()
    else:
        now = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    h = now.hour
    if   5 <= h < 11:  greet, label = "좋은 아침입니다", "아침"
    elif 11 <= h < 17: greet, label = "안녕하세요", "낮"
    elif 17 <= h < 22: greet, label = "좋은 저녁입니다", "저녁"
    else:              greet, label = "늦은 시간입니다", "밤"
    ampm = "오전" if h < 12 else "오후"
    h12 = h % 12 or 12
    return greet, f"{ampm} {h12}시 {now.minute:02d}분", label


# IP 위치 1회 조회 캐시 — 얼굴 인사처럼 반복 호출되는 경로가 매번 네트워크를 타지 않도록
_CACHED_LOC = None
_LOC_FETCHED = False

def get_location_cached():
    """위치 1회 캐시. USER_LOCATION_CITY 환경변수가 있으면 IP 조회를 건너뛰고 그 값을 사용.

    Why: IP 지오로케이션은 사용자 위치가 아닌 ISP 백본 POP을 반환하는 경우가 흔하다
    (예: 용인 보정동 → 마포구로 잡힘). 동/구 단위 정확도가 필요하면 환경변수로 고정."""
    global _CACHED_LOC, _LOC_FETCHED
    if not _LOC_FETCHED:
        city_override = os.getenv("USER_LOCATION_CITY", "").strip()
        if city_override:
            try:
                off = int(os.getenv("USER_TZ_OFFSET", "").strip())
            except (ValueError, TypeError):
                off = 32400   # 기본: KST(UTC+9). 한국 내 어디든 동일하므로 안전한 기본값
            _CACHED_LOC = {"city": city_override, "region": "", "country": "",
                           "timezone": "", "offset": off}
        else:
            try:
                _CACHED_LOC = get_location_by_ip()
            except Exception:
                _CACHED_LOC = None
        _LOC_FETCHED = True
    return _CACHED_LOC


def local_offset_cached():
    """캐시된 위치의 UTC offset(초) 반환. 위치 미확인 시 None(=시스템 로컬 시간 사용)."""
    loc = get_location_cached()
    return loc.get("offset") if loc else None


def get_current_weather(city, units="metric", lang="kr"):
    """
    현재 날씨 조회
    - city: 도시 이름 (한글/영어 모두 가능)
    - units: metric(섭씨) / imperial(화씨)
    - lang: kr(한국어) / en(영어)
    """
    if not WEATHER_API_KEY:
        return {"success": False, "message": "OPENWEATHER_API_KEY가 .env에 설정되지 않았습니다. https://openweathermap.org 에서 무료 발급하세요."}

    url = "https://api.openweathermap.org/data/2.5/weather"
    params = {
        "q": city,
        "appid": WEATHER_API_KEY,
        "units": units,
        "lang": lang,
    }

    try:
        resp = net.get(url, params=params, timeout=(5, 10))
        if resp.status_code == 404:
            return {"success": False, "message": f"'{city}' 도시를 찾을 수 없습니다. 영어 도시명을 시도해보세요. (예: Seoul, Tokyo)"}
        if resp.status_code == 401:
            return {"success": False, "message": "OpenWeatherMap API 키가 유효하지 않습니다."}
        if resp.status_code != 200:
            return {"success": False, "message": f"API 오류: {resp.status_code}"}

        d = resp.json()

        icon_code = d["weather"][0].get("icon", "01d")
        weather_text = WEATHER_ICONS.get(icon_code, d["weather"][0].get("description", ""))

        temp_unit = "°C" if units == "metric" else "°F"
        speed_unit = "m/s" if units == "metric" else "mph"

        result = {
            "success": True,
            "city": d.get("name", city),
            "country": d.get("sys", {}).get("country", ""),
            "weather": weather_text,
            "description": d["weather"][0].get("description", ""),
            "temperature": f"{d['main']['temp']:.1f}{temp_unit}",
            "feels_like": f"{d['main']['feels_like']:.1f}{temp_unit}",
            "temp_min": f"{d['main']['temp_min']:.1f}{temp_unit}",
            "temp_max": f"{d['main']['temp_max']:.1f}{temp_unit}",
            "humidity": f"{d['main']['humidity']}%",
            "pressure": f"{d['main']['pressure']}hPa",
            "wind_speed": f"{d['wind']['speed']}{speed_unit}",
            "wind_direction": wind_direction(d["wind"].get("deg", 0)),
            "clouds": f"{d.get('clouds', {}).get('all', 0)}%",
            "visibility": f"{d.get('visibility', 0) / 1000:.1f}km",
            "sunrise": datetime.fromtimestamp(d["sys"]["sunrise"]).strftime("%H:%M"),
            "sunset": datetime.fromtimestamp(d["sys"]["sunset"]).strftime("%H:%M"),
        }

        return result

    except requests.Timeout:
        return {"success": False, "message": "날씨 API 시간 초과"}
    except Exception as e:
        return {"success": False, "message": f"날씨 조회 오류: {e}"}


def get_weather_forecast(city, units="metric", lang="kr", days=3):
    """
    날씨 예보 조회 (3시간 간격, 최대 5일)
    - days: 예보 일수 (1~5)
    """
    if not WEATHER_API_KEY:
        return {"success": False, "message": "OPENWEATHER_API_KEY가 설정되지 않았습니다."}

    url = "https://api.openweathermap.org/data/2.5/forecast"
    params = {
        "q": city,
        "appid": WEATHER_API_KEY,
        "units": units,
        "lang": lang,
        "cnt": min(days * 8, 40),  # 3시간 간격 × 8 = 1일
    }

    try:
        resp = net.get(url, params=params, timeout=(5, 10))
        if resp.status_code != 200:
            return {"success": False, "message": f"예보 API 오류: {resp.status_code}"}

        d = resp.json()
        temp_unit = "°C" if units == "metric" else "°F"

        # 일별로 그룹핑
        daily = {}
        for item in d.get("list", []):
            dt = datetime.fromtimestamp(item["dt"])
            date_key = dt.strftime("%m/%d (%a)")
            if date_key not in daily:
                daily[date_key] = {
                    "temps": [],
                    "weather": [],
                    "humidity": [],
                }
            daily[date_key]["temps"].append(item["main"]["temp"])
            daily[date_key]["weather"].append(item["weather"][0]["description"])
            daily[date_key]["humidity"].append(item["main"]["humidity"])

        forecast = []
        for date, info in list(daily.items())[:days]:
            temps = info["temps"]
            # 가장 빈번한 날씨
            weather_counts = {}
            for w in info["weather"]:
                weather_counts[w] = weather_counts.get(w, 0) + 1
            main_weather = max(weather_counts, key=weather_counts.get)

            forecast.append({
                "date": date,
                "min_temp": f"{min(temps):.0f}{temp_unit}",
                "max_temp": f"{max(temps):.0f}{temp_unit}",
                "weather": main_weather,
                "humidity": f"{sum(info['humidity']) // len(info['humidity'])}%",
            })

        return {
            "success": True,
            "city": d.get("city", {}).get("name", city),
            "forecast": forecast,
            "days": len(forecast),
        }

    except Exception as e:
        return {"success": False, "message": f"예보 조회 오류: {e}"}


# ──────────────────────────────────────────────
#  LLM 도구 정의
# ──────────────────────────────────────────────

WEATHER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "현재 날씨를 조회합니다. 기온, 습도, 바람, 체감온도, 일출/일몰 등의 정보를 반환합니다. 날씨를 물어보면 이 도구를 사용하세요.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "도시 이름 (영어 권장: Seoul, Tokyo, New York, London, Incheon)",
                    },
                    "units": {
                        "type": "string",
                        "description": "온도 단위: metric(섭씨) 또는 imperial(화씨)",
                        "default": "metric",
                    },
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": "날씨 예보를 조회합니다. 최대 5일간의 일별 최저/최고 기온과 날씨를 반환합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "도시 이름 (영어 권장)",
                    },
                    "days": {
                        "type": "integer",
                        "description": "예보 일수 (1~5, 기본 3)",
                        "default": 3,
                    },
                },
                "required": ["city"],
            },
        },
    },
]

WEATHER_TOOL_MAP = {
    "get_current_weather": get_current_weather,
    "get_weather_forecast": get_weather_forecast,
}

def execute_weather_tool(tool_name, arguments):
    """날씨 도구 실행"""
    func = WEATHER_TOOL_MAP.get(tool_name)
    if not func:
        return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        # integer 변환
        if "days" in arguments:
            try:
                arguments["days"] = int(arguments["days"])
            except (ValueError, TypeError):
                arguments["days"] = 3
        return func(**arguments)
    except Exception as e:
        return {"success": False, "message": f"실행 오류: {e}"}
