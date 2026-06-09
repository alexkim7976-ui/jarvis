"""
J.A.R.V.I.S — 시스템 모니터 모듈
──────────────────────────────────
CPU, 메모리, 디스크, 온도 등 시스템 상태를 실시간 조회합니다.

의존성: pip install psutil
"""

from __future__ import annotations

import os
import json
import platform
import subprocess
import shutil
from datetime import datetime

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False


def get_system_status():
    """CPU, 메모리, 디스크, 온도 등 전체 시스템 상태 조회"""
    if not PSUTIL_AVAILABLE:
        return {"success": False, "message": "psutil 미설치. pip install psutil 실행하세요."}

    cpu_pct = psutil.cpu_percent(interval=0.5)
    cpu_count = psutil.cpu_count()
    cpu_freq = psutil.cpu_freq()

    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    boot = datetime.fromtimestamp(psutil.boot_time())
    uptime_sec = (datetime.now() - boot).total_seconds()
    h, rem = divmod(int(uptime_sec), 3600)
    m, s = divmod(rem, 60)

    # 온도 (macOS)
    temp = _get_temperature()

    # 배터리
    battery = None
    try:
        bat = psutil.sensors_battery()
        if bat:
            battery = {
                "percent": f"{bat.percent}%",
                "plugged": bat.power_plugged,
                "time_left": f"{bat.secsleft // 60}분" if bat.secsleft > 0 else "충전중",
            }
    except Exception:
        pass

    # 상위 프로세스 (CPU 기준)
    top_procs = []
    try:
        for p in sorted(psutil.process_iter(["name", "cpu_percent", "memory_percent"]),
                        key=lambda x: x.info.get("cpu_percent", 0) or 0, reverse=True)[:5]:
            info = p.info
            if info.get("cpu_percent", 0) > 0:
                top_procs.append({
                    "name": info["name"][:20],
                    "cpu": f"{info.get('cpu_percent', 0):.1f}%",
                    "mem": f"{info.get('memory_percent', 0):.1f}%",
                })
    except Exception:
        pass

    result = {
        "success": True,
        "cpu": {
            "usage": f"{cpu_pct}%",
            "cores": cpu_count,
            "freq": f"{cpu_freq.current:.0f}MHz" if cpu_freq else "N/A",
        },
        "memory": {
            "total": _fmt_bytes(mem.total),
            "used": _fmt_bytes(mem.used),
            "available": _fmt_bytes(mem.available),
            "percent": f"{mem.percent}%",
        },
        "disk": {
            "total": _fmt_bytes(disk.total),
            "used": _fmt_bytes(disk.used),
            "free": _fmt_bytes(disk.free),
            "percent": f"{disk.percent}%",
        },
        "temperature": temp,
        "battery": battery,
        "uptime": f"{h}시간 {m}분",
        "os": f"{platform.system()} {platform.release()}",
        "top_processes": top_procs,
    }

    return result


def get_cpu_usage():
    """CPU 사용률만 빠르게 조회"""
    if not PSUTIL_AVAILABLE:
        return {"success": False, "message": "psutil 미설치"}
    return {
        "success": True,
        "cpu_percent": f"{psutil.cpu_percent(interval=0.3)}%",
        "per_core": [f"{x}%" for x in psutil.cpu_percent(interval=0.3, percpu=True)],
        "cores": psutil.cpu_count(),
    }


def get_memory_usage():
    """메모리 사용량만 빠르게 조회"""
    if not PSUTIL_AVAILABLE:
        return {"success": False, "message": "psutil 미설치"}
    m = psutil.virtual_memory()
    return {
        "success": True,
        "total": _fmt_bytes(m.total),
        "used": _fmt_bytes(m.used),
        "available": _fmt_bytes(m.available),
        "percent": f"{m.percent}%",
    }


def _get_temperature():
    """macOS/Linux 온도 조회"""
    try:
        if platform.system() == "Darwin":
            # macOS: powermetrics 또는 osx-cpu-temp
            for cmd in [["osx-cpu-temp"], ["sudo", "powermetrics", "--samplers", "smc", "-n", "1"]]:
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
                    if r.returncode == 0 and r.stdout:
                        for line in r.stdout.split("\n"):
                            if "temp" in line.lower() or "°" in line:
                                return line.strip()[:30]
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    continue
            return "측정 불가 (brew install osx-cpu-temp)"
        elif platform.system() == "Linux":
            temps = psutil.sensors_temperatures()
            if temps:
                for name, entries in temps.items():
                    for entry in entries:
                        if entry.current > 0:
                            return f"{entry.current:.1f}°C ({entry.label or name})"
            return "센서 없음"
    except Exception:
        return "측정 불가"
    return "N/A"


def _fmt_bytes(b):
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if b < 1024:
            return f"{b:.1f}{unit}"
        b /= 1024
    return f"{b:.1f}PB"


# ──────────────────────────────────────────────
#  LLM 도구 정의
# ──────────────────────────────────────────────

SYSTEM_MONITOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": "컴퓨터의 CPU, 메모리, 디스크, 온도, 배터리, 가동시간, 상위 프로세스 등 전체 시스템 상태를 조회합니다. 시스템 상태, 컴퓨터 상태, 성능, 리소스 사용량을 물어볼 때 사용하세요.",
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
            "name": "get_cpu_usage",
            "description": "CPU 사용률을 코어별로 빠르게 조회합니다.",
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
            "name": "get_memory_usage",
            "description": "메모리(RAM) 사용량을 조회합니다.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
]

SYSTEM_MONITOR_TOOL_MAP = {
    "get_system_status": get_system_status,
    "get_cpu_usage": get_cpu_usage,
    "get_memory_usage": get_memory_usage,
}

def execute_system_tool(tool_name, arguments):
    func = SYSTEM_MONITOR_TOOL_MAP.get(tool_name)
    if not func:
        return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return func(**arguments)
    except Exception as e:
        return {"success": False, "message": f"오류: {e}"}
