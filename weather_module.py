

from __future__ import annotations

import os
import json
import re
import requests
from datetime import datetime, timezone, timedelta
from pathlib import Path
try:
    import net  # 연결 재사용·재시도·적응형 타임아웃 공용 세션
except Exception:
    net = requests

WEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")


def weather_api_enabled() -> bool:
    """OpenWeather 날씨 API — 기본 OFF (JARVIS_WEATHER_ENABLED=1 일 때만)."""
    return os.getenv("JARVIS_WEATHER_ENABLED", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )

_USER_LOC_FILE = Path(os.getenv("JARVIS_USER_LOCATION_FILE", "data/user_location.json"))


def _user_loc_path() -> Path:
    p = _USER_LOC_FILE
    if not p.is_absolute():
        p = Path(__file__).resolve().parent / p
    return p


def load_user_city() -> str:
    """사용자가 말해 준 도시(음성 입력 저장)."""
    try:
        raw = _user_loc_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return (data.get("city") or "").strip()
    except Exception:
        return ""


def save_user_city(city: str) -> None:
    c = (city or "").strip()
    if not c:
        return
    path = _user_loc_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"city": c, "updated_at": datetime.now().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )
    try:
        from memory_store import record_pref
        record_pref("location:city", c)
    except ImportError:
        pass


def user_city_configured() -> bool:
    return bool(load_user_city())


def ask_location_on_boot() -> bool:
    """0=자동 위치(GPS/IP), 1=음성으로 도시 입력 (기본 OFF)."""
    return os.getenv("JARVIS_ASK_LOCATION_ON_BOOT", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


_CITY_NOISE_RE = re.compile(
    r"^(?:저는|지금|여기|현재|도시는?|위치는?|날씨\s*(?:알려|말해)?|"
    r"(?:알려|말해)\s*줘?)\s*",
    re.I,
)
_CITY_TAIL_RE = re.compile(
    r"(?:에\s*(?:있어|계세요|살아)|(?:이|이에|이요|예|예요|입니다))[\.!?\s]*$",
    re.I,
)


def extract_city_from_text(text: str) -> str:
    """음성에서 도시명 추출 — '부천이요', '지금 서울' 등."""
    t = (text or "").strip()
    if not t:
        return ""
    t = _CITY_NOISE_RE.sub("", t)
    t = _CITY_TAIL_RE.sub("", t)
    t = re.sub(r"[\.!?,~]+", " ", t).strip()
    # "서울 날씨" → 서울
    t = re.split(r"\s+날씨", t, maxsplit=1, flags=re.I)[0].strip()
    return t[:80]


def format_weather_line(city: str) -> str:
    w = get_current_weather(city)
    if not w.get("success"):
        return w.get("message", "Unable to retrieve weather information.")
    name = w.get("city") or city
    return f"The weather in {name} is {w.get('weather', '')} with a temperature of {w.get('temperature', '')}."


def format_where_from_loc(loc: dict) -> str:
    city = (loc.get("city") or "").strip()
    region = (loc.get("region") or "").strip()
    country = (loc.get("country") or "").strip()
    parts = [p for p in (city, region, country) if p]
    return ", ".join(parts) if parts else ""


def resolve_auto_location(*, refresh: bool = False) -> dict | None:
    """CoreLocation(GPS/WiFi) → IP → env 폴백. refresh=True면 캐시 무시."""
    global _CACHED_LOC, _LOC_FETCHED
    if refresh:
        _LOC_FETCHED = False
        _CACHED_LOC = None
    loc = None
    if device_location_enabled():
        try:
            loc = get_device_location()
        except Exception:
            loc = None
    if not loc or not (loc.get("city") or loc.get("region")):
        loc = get_location_cached()
    return loc


def _fast_boot_enabled() -> bool:
    return os.getenv("JARVIS_FAST_BOOT", "").strip().lower() in ("1", "true", "yes", "on")


def build_fast_startup_greeting() -> str:
    """Immediate greeting — local system time without waiting for network/GPS."""
    greet, time_str, label = local_time_greeting(None)
    return f"{greet}. The local time is {time_str}."


def build_location_weather_followup(*, try_gps: bool = True) -> str:
    """Location and local time follow-up after fast greeting."""
    loc = None
    if try_gps and device_location_enabled():
        try:
            t = float(os.getenv("JARVIS_LOCATION_TIMEOUT", "2.5"))
            loc = get_device_location(timeout=t)
        except (TypeError, ValueError):
            loc = get_device_location()
        except Exception:
            loc = None
    if not loc or not (loc.get("city") or loc.get("region")):
        try:
            loc = get_location_by_ip()
        except Exception:
            loc = None
    if not loc or not (loc.get("city") or loc.get("region")):
        loc = resolve_auto_location(refresh=False)
    _, time_str, label = local_time_greeting(loc.get("offset") if loc else None)
    parts: list[str] = []
    where = format_where_from_loc(loc) if loc else ""
    if where:
        parts.append(f"Your current location is {where}.")
        parts.append(f"The local time is {time_str}.")
        if weather_api_enabled() and WEATHER_API_KEY:
            city = (loc.get("city") or loc.get("region") or "").strip()
            if city:
                parts.append(format_weather_line(city))
    if not parts:
        return ""
    return " ".join(parts)


def build_auto_startup_briefing() -> str:
    """Boot greeting — auto-detected location + local time."""
    loc = resolve_auto_location(refresh=True)
    greet, time_str, label = local_time_greeting(loc.get("offset") if loc else None)
    parts: list[str] = [f"{greet}."]
    where = format_where_from_loc(loc) if loc else ""
    if where:
        parts.append(f"Your current location is {where}.")
        parts.append(f"The local time is {time_str}.")
        if weather_api_enabled() and WEATHER_API_KEY:
            city = (loc.get("city") or loc.get("region") or "").strip()
            if city:
                parts.append(format_weather_line(city))
    else:
        parts.append(f"I could not automatically determine your location. The local time is {time_str}.")
        parts.append("Please check Location Services and JARVIS location permissions in System Settings.")
    return " ".join(parts)


def build_location_weather_briefing(city: str, *, include_greet: bool = True) -> str:
    """Location and local time briefing for a saved city."""
    c = (city or "").strip()
    if not c:
        return "Please tell me your city and I will provide your location and local time."
    greet, time_str, label = local_time_greeting(None)
    parts: list[str] = []
    if include_greet:
        parts.append(f"{greet}.")
    parts.append(f"Your current location is {c}.")
    parts.append(f"The local time is {time_str}.")
    if weather_api_enabled() and WEATHER_API_KEY:
        parts.append(format_weather_line(c))
    return " ".join(parts)


def startup_location_prompt() -> str:
    """First-run prompt asking for city."""
    greet, _, _ = local_time_greeting(None)
    return (
        f"{greet}. Please tell me which city you are in so I can set your local time. "
        f"For example, you can say Seoul, New York, or Stony Brook."
    )


# 날씨 아이콘 → 텍스트 매핑
WEATHER_ICONS = {
    "01d": "☀ Clear", "01n": "🌙 Clear",
    "02d": "⛅ Partly Cloudy", "02n": "☁ Partly Cloudy",
    "03d": "☁ Mostly Cloudy", "03n": "☁ Mostly Cloudy",
    "04d": "☁ Overcast", "04n": "☁ Overcast",
    "09d": "🌧 Showers", "09n": "🌧 Showers",
    "10d": "🌦 Rain", "10n": "🌧 Rain",
    "11d": "⛈ Thunderstorm", "11n": "⛈ Thunderstorm",
    "13d": "🌨 Snow", "13n": "🌨 Snow",
    "50d": "🌫 Fog", "50n": "🌫 Fog",
}

# 풍향 변환
def wind_direction(deg):
    dirs = ["N", "NNE", "NE", "ENE",
            "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW",
            "W", "WNW", "NW", "NNW"]
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


def reverse_geocode(lat, lon, lang="en"):
    """Latitude/longitude -> city/region/country via BigDataCloud reverse-geocode-client."""
    try:
        d = net.get("https://api.bigdatacloud.net/data/reverse-geocode-client",
                    params={"latitude": lat, "longitude": lon, "localityLanguage": lang},
                    timeout=(3, 6)).json()
        city = (d.get("city") or d.get("locality") or "").strip()
        region = (d.get("principalSubdivision") or "").strip()
        country = (d.get("countryName") or "").strip()
        if not (city or region or country):
            return None
        return {"city": city, "region": region, "country": country,
                "timezone": "", "offset": None, "lat": lat, "lon": lon}
    except Exception:
        return None


def get_device_location(*, timeout: float | None = None):
    """macOS CoreLocation device coordinates -> English reverse geocoding."""
    try:
        from mac_location import get_device_coordinates
    except Exception:
        return None
    try:
        coords = get_device_coordinates(timeout=timeout)
    except Exception:
        coords = None
    if not coords:
        return None
    lat, lon = coords[0], coords[1]
    return reverse_geocode(lat, lon)


def local_time_greeting(offset_seconds=None):
    """Return (greeting, time_string, period_label) in English."""
    if offset_seconds is None:
        now = datetime.now()
    else:
        now = datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    h = now.hour
    if   5 <= h < 12:  greet, label = "Good morning", "morning"
    elif 12 <= h < 17: greet, label = "Good afternoon", "afternoon"
    elif 17 <= h < 22: greet, label = "Good evening", "evening"
    else:              greet, label = "Good evening", "night"
    ampm = "AM" if h < 12 else "PM"
    h12 = h % 12 or 12
    return greet, f"{h12}:{now.minute:02d} {ampm}", label


# IP 위치 1회 조회 캐시 — 얼굴 인사처럼 반복 호출되는 경로가 매번 네트워크를 타지 않도록
_CACHED_LOC = None
_LOC_FETCHED = False


def device_location_enabled() -> bool:
    v = os.getenv("JARVIS_DEVICE_LOCATION", "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def get_location_cached():
    """위치 1회 캐시. JARVIS_DEVICE_LOCATION=1 이면 CoreLocation 우선(정확).
    USER_LOCATION_CITY 는 기기 위치 실패 시에만 폴백으로 사용."""
    global _CACHED_LOC, _LOC_FETCHED
    if not _LOC_FETCHED:
        loc = None
        city_override = os.getenv("USER_LOCATION_CITY", "").strip()
        if device_location_enabled():
            try:
                loc = get_device_location()
            except Exception:
                loc = None
        if (not loc or not (loc.get("city") or loc.get("region"))) and city_override:
            try:
                off = int(os.getenv("USER_TZ_OFFSET", "").strip())
            except (ValueError, TypeError):
                off = 32400
            loc = {"city": city_override, "region": "", "country": "",
                   "timezone": "", "offset": off}
        if not loc or not (loc.get("city") or loc.get("region")):
            try:
                loc = get_location_by_ip()
            except Exception:
                loc = None
        _CACHED_LOC = loc
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


def get_current_location():
    """Return user's current location (city/region/country) and local time."""
    loc = resolve_auto_location(refresh=True)
    if not loc:
        return {"success": False,
                "message": "Could not determine your current location. Please allow JARVIS in System Settings -> Privacy & Security -> Location Services."}
    city = (loc.get("city") or "").strip()
    region = (loc.get("region") or "").strip()
    country = (loc.get("country") or "").strip()
    parts = [p for p in (city, region, country) if p]
    where = ", ".join(parts) if parts else "Unknown"
    _, time_str, _ = local_time_greeting(loc.get("offset"))
    return {
        "success": True,
        "city": city, "region": region, "country": country,
        "timezone": loc.get("timezone", ""),
        "location": where,
        "local_time": time_str,
        "message": f"Your current location is {where}, and the local time is {time_str}.",
    }


# ──────────────────────────────────────────────
#  LLM 도구 정의
# ──────────────────────────────────────────────

_LOCATION_TOOL = {
    "type": "function",
    "function": {
        "name": "get_current_location",
        "description": (
            "사용자의 현재 위치(도시/지역/국가)와 현지 시각을 반환합니다. "
            "'내 위치 어디야', '나 지금 어디 있어', '여기 어디야' 같은 질문에 사용하세요."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
}

_WEATHER_API_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "현재 날씨를 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "도시 이름 (영어 권장)"},
                    "units": {"type": "string", "default": "metric"},
                },
                "required": ["city"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather_forecast",
            "description": "날씨 예보를 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": "string", "description": "도시 이름 (영어 권장)"},
                    "days": {"type": "integer", "default": 3},
                },
                "required": ["city"],
            },
        },
    },
]

WEATHER_TOOLS = [_LOCATION_TOOL] + (
    _WEATHER_API_TOOLS if weather_api_enabled() else []
)

WEATHER_TOOL_MAP = {
    "get_current_weather": get_current_weather,
    "get_weather_forecast": get_weather_forecast,
    "get_current_location": get_current_location,
}

def execute_weather_tool(tool_name, arguments):
    """위치·(선택) 날씨 도구 실행"""
    if tool_name in ("get_current_weather", "get_weather_forecast") and not weather_api_enabled():
        return {"success": False, "message": "날씨 기능은 사용하지 않습니다."}
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
