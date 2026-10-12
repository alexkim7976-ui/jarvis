"""
J.A.R.V.I.S — 로컬 파일 관리 모듈
──────────────────────────────────
음성 명령을 분석하여 로컬 파일 작업을 수행합니다.

지원 명령:
  - 파일 검색: "다운로드 폴더에서 PDF 찾아줘"
  - 폴더 정리: "바탕화면 정리해줘"
  - 파일 정보: "바탕화면에 뭐가 있어?"
  - 파일 열기: "최근 다운로드한 파일 열어줘"
  - 파일 이동: "바탕화면 스크린샷들 Pictures로 옮겨줘"
  - 앱 실행:  "크롬 열어줘"
"""

from __future__ import annotations

import os
import sys
import json
import glob
import shutil
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict, deque

# LLM 컨텍스트 — 최근 열린 파일/앱 (지시대명사 "그 문서", "아까 본 파일" 해석용)
_RECENT_FILES: deque = deque(maxlen=8)


def register_recent_file(path: str, name: str = "", action: str = "opened") -> None:
    p = (path or "").strip()
    if not p:
        return
    label = (name or Path(p).name or p).strip()
    _RECENT_FILES.append({
        "path": p,
        "name": label,
        "action": action,
        "at": datetime.now().strftime("%H:%M"),
    })


def get_file_context_suffix() -> str:
    """최근 파일/앱 — LLM System Prompt 주입용."""
    if not _RECENT_FILES:
        return ""
    lines = ["\n\n[RECENT FILES — recently opened by user or JARVIS tools]"]
    for i, item in enumerate(reversed(list(_RECENT_FILES)), 1):
        lines.append(
            f"- #{i} {item.get('name')} ({item.get('action')}) "
            f"path={item.get('path')} @ {item.get('at')}"
        )
    lines.append(
        "사용자가 '그 문서', '아까 본 파일', '방금 연 파일', '아까 보던 그거' 등을 말하면 "
        "가장 최근(#1) path로 open_file을 호출하라. 여러 단계 요청(열고+볼륨 조절 등)은 "
        "필요한 도구를 순서대로 여러 번 호출하라."
    )
    return "\n".join(lines)

try:
    from security import validate_path, sanitize_filename, audit_tool_call
    _SECURITY = True
except ImportError:
    _SECURITY = False

# OS 레벨 파일 시스템 샌드박스 — 모든 파일 작업을 가상 루트 내부로 격리
try:
    import sandbox as _sb
    _SANDBOX = True
except Exception:  # pragma: no cover
    _SANDBOX = False


def _sandbox_on() -> bool:
    """샌드박스 모듈이 있고 강제 모드가 켜져 있는지."""
    return _SANDBOX and getattr(_sb, "ENFORCED", False)


# ──────────────────────────────────────────────
#  기본 경로 설정 (macOS 기준)
# ──────────────────────────────────────────────
HOME = Path.home()

# 사용자 폴더 매핑 (한글 + 영어 모두 인식)
FOLDER_ALIASES = {
    "바탕화면": HOME / "Desktop",
    "데스크탑": HOME / "Desktop",
    "desktop":  HOME / "Desktop",
    "다운로드": HOME / "Downloads",
    "downloads": HOME / "Downloads",
    "문서":     HOME / "Documents",
    "documents": HOME / "Documents",
    "사진":     HOME / "Pictures",
    "pictures": HOME / "Pictures",
    "음악":     HOME / "Music",
    "music":    HOME / "Music",
    "영상":     HOME / "Movies",
    "movies":   HOME / "Movies",
    "홈":       HOME,
    "home":     HOME,
}

# 파일 분류 카테고리
FILE_CATEGORIES = {
    "이미지": [".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".svg", ".heic", ".tiff"],
    "문서":   [".pdf", ".doc", ".docx", ".txt", ".rtf", ".hwp", ".pptx", ".ppt", ".xlsx", ".xls", ".csv"],
    "영상":   [".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm"],
    "음악":   [".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"],
    "압축":   [".zip", ".rar", ".7z", ".tar", ".gz", ".dmg"],
    "코드":   [".py", ".js", ".html", ".css", ".java", ".cpp", ".c", ".ts", ".json", ".xml"],
    "기타":   [],
}

# macOS 앱 매핑
APP_ALIASES = {
    "크롬":     "Google Chrome",
    "chrome":   "Google Chrome",
    "사파리":   "Safari",
    "safari":   "Safari",
    "파인더":   "Finder",
    "finder":   "Finder",
    "메모":     "Notes",
    "notes":    "Notes",
    "카카오톡": "KakaoTalk",
    "슬랙":     "Slack",
    "slack":    "Slack",
    "vscode":   "Visual Studio Code",
    "코드":     "Visual Studio Code",
    "스포티파이": "Spotify",
    "spotify":  "Spotify",
    "메시지":   "Messages",
    "계산기":   "Calculator",
    "캘린더":   "Calendar",
}

# 셸/스크립트 임의 실행이 가능한 고위험 앱 — 자동 실행 금지 (인젝션 악용 차단)
_DANGEROUS_APPS = {
    "Terminal", "iTerm", "iTerm2", "Script Editor", "Automator", "Console",
    "Activity Monitor", "System Settings", "System Preferences",
}
# 실행 허용 화이트리스트 — 별칭에 등록된 안전한 앱만 (고위험 앱 제외)
ALLOWED_APPS = {v for v in APP_ALIASES.values() if v not in _DANGEROUS_APPS}


# 샌드박스 모드에서 폴더 별칭 → 샌드박스 내부 표준 하위 폴더 매핑
# (실제 ~/Desktop 대신 <SANDBOX_ROOT>/Desktop 으로 격리)
_SANDBOX_ALIAS = {
    "바탕화면": "Desktop", "데스크탑": "Desktop", "desktop": "Desktop",
    "다운로드": "Downloads", "downloads": "Downloads",
    "문서": "Documents", "documents": "Documents",
    "사진": "Pictures", "pictures": "Pictures",
    "음악": "Music", "music": "Music",
    "영상": "Movies", "movies": "Movies",
    "홈": "", "home": "",           # 홈 → 샌드박스 루트
    "applications": "Applications",  # 앱 폴더도 샌드박스 내부로 격리
}


# ──────────────────────────────────────────────
#  유틸리티 함수
# ──────────────────────────────────────────────
def _resolve_folder_sandbox(name):
    """샌드박스 강제 모드 전용 폴더 해석 — 모든 이름/경로를 가상 루트 내부로 앵커링."""
    name_lower = name.lower().strip()
    sub = _SANDBOX_ALIAS.get(name_lower)
    if sub is not None:
        target = _sb.SANDBOX_ROOT / sub if sub else _sb.SANDBOX_ROOT
    else:
        target = _sb.anchor_path(name)
    try:
        target.mkdir(parents=True, exist_ok=True)
        # 심볼릭 링크 추적 후 경계 검증 (외부 가리키면 PermissionError)
        return _sb.assert_within_sandbox(target)
    except PermissionError:
        return None
    except OSError:
        return None


def resolve_folder(name):
    """폴더 이름을 실제 경로로 변환 — 보안 검증 포함"""
    # 샌드박스 강제 모드: 모든 경로를 가상 루트 내부로 치환
    if _sandbox_on():
        return _resolve_folder_sandbox(name)

    name_lower = name.lower().strip()
    if name_lower in FOLDER_ALIASES:
        return FOLDER_ALIASES[name_lower]
    # 직접 경로 (절대/상대 모두)
    p = Path(name).expanduser()
    if p.exists():
        # 보안: .ssh, .gnupg 등 민감 경로 차단
        if _SECURITY:
            safe, _, _ = validate_path(str(p))
            if not safe:
                return None
        return p
    # /로 시작하는 절대 경로
    if name.startswith("/"):
        p = Path(name)
        if p.exists():
            if _SECURITY:
                safe, _, _ = validate_path(str(p))
                if not safe:
                    return None
            return p
    # ~로 시작하는 홈 경로
    if name.startswith("~"):
        p = Path(name).expanduser()
        if p.exists():
            if _SECURITY:
                safe, _, _ = validate_path(str(p))
                if not safe:
                    return None
            return p
    # HOME 하위 폴더 검색
    for d in HOME.iterdir():
        if d.is_dir() and d.name.lower() == name_lower:
            return d
    # Applications 검색
    app_dir = Path("/Applications")
    if app_dir.exists() and name_lower == "applications":
        return app_dir
    return None


def get_file_category(ext):
    """확장자로 파일 카테고리 반환"""
    ext = ext.lower()
    for category, extensions in FILE_CATEGORIES.items():
        if ext in extensions:
            return category
    return "기타"


def format_size(size_bytes):
    """파일 크기를 읽기 쉬운 형태로 변환"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_time(timestamp):
    """타임스탬프를 읽기 쉬운 형태로 변환"""
    dt = datetime.fromtimestamp(timestamp)
    now = datetime.now()
    diff = now - dt

    if diff.days == 0:
        if diff.seconds < 3600:
            return f"{diff.seconds // 60}분 전"
        return f"{diff.seconds // 3600}시간 전"
    elif diff.days == 1:
        return "어제"
    elif diff.days < 7:
        return f"{diff.days}일 전"
    else:
        return dt.strftime("%Y-%m-%d")


# ──────────────────────────────────────────────
#  파일 관리 기능들
# ──────────────────────────────────────────────

def search_files(folder_name, extension=None, keyword=None, max_results=20):
    """
    폴더에서 파일 검색
    - extension: ".pdf", ".jpg" 등
    - keyword: 파일명에 포함된 키워드
    """
    folder = resolve_folder(folder_name)
    if not folder or not folder.exists():
        return {"success": False, "message": f"'{folder_name}' 폴더를 찾을 수 없습니다."}

    results = []
    try:
        for f in folder.iterdir():
            if f.name.startswith("."):
                continue
            if extension and f.suffix.lower() != extension.lower():
                continue
            if keyword and keyword.lower() not in f.name.lower():
                continue
            stat = f.stat()
            results.append({
                "name": f.name,
                "path": str(f),
                "size": format_size(stat.st_size),
                "modified": format_time(stat.st_mtime),
                "is_dir": f.is_dir(),
                "category": get_file_category(f.suffix) if not f.is_dir() else "폴더",
            })
    except PermissionError:
        return {"success": False, "message": f"'{folder_name}' 폴더 접근 권한이 없습니다."}

    results.sort(key=lambda x: x["name"])
    return {
        "success": True,
        "folder": str(folder),
        "total": len(results),
        "files": results[:max_results],
    }


def list_folder(folder_name):
    """폴더 내용 요약"""
    folder = resolve_folder(folder_name)
    if not folder or not folder.exists():
        return {"success": False, "message": f"'{folder_name}' 폴더를 찾을 수 없습니다."}

    categories = defaultdict(list)
    total_size = 0
    file_count = 0
    dir_count = 0

    try:
        for f in folder.iterdir():
            if f.name.startswith("."):
                continue
            if f.is_dir():
                dir_count += 1
                categories["폴더"].append(f.name)
            else:
                file_count += 1
                stat = f.stat()
                total_size += stat.st_size
                cat = get_file_category(f.suffix)
                categories[cat].append(f.name)
    except PermissionError:
        return {"success": False, "message": "접근 권한이 없습니다."}

    summary = {cat: len(files) for cat, files in categories.items()}

    return {
        "success": True,
        "folder": str(folder),
        "file_count": file_count,
        "dir_count": dir_count,
        "total_size": format_size(total_size),
        "categories": summary,
        "sample_files": {cat: files[:5] for cat, files in categories.items()},
    }


def organize_folder(folder_name, dry_run=True):
    """
    폴더 정리 — 파일을 카테고리별 하위 폴더로 분류
    dry_run=True: 미리보기만 (실제 이동 안 함)
    dry_run=False: 실제 이동
    """
    folder = resolve_folder(folder_name)
    if not folder or not folder.exists():
        return {"success": False, "message": f"'{folder_name}' 폴더를 찾을 수 없습니다."}

    plan = defaultdict(list)

    try:
        for f in folder.iterdir():
            if f.name.startswith(".") or f.is_dir():
                continue
            cat = get_file_category(f.suffix)
            plan[cat].append(f.name)
    except PermissionError:
        return {"success": False, "message": "접근 권한이 없습니다."}

    if not dry_run:
        moved = 0
        for cat, files in plan.items():
            if cat == "기타":
                target_dir = folder / "기타"
            else:
                target_dir = folder / cat
            target_dir.mkdir(exist_ok=True)
            for fname in files:
                src = folder / fname
                dst = target_dir / fname
                if dst.exists():
                    base = dst.stem
                    ext = dst.suffix
                    counter = 1
                    while dst.exists():
                        dst = target_dir / f"{base}_{counter}{ext}"
                        counter += 1
                try:
                    shutil.move(str(src), str(dst))
                    moved += 1
                except Exception:
                    pass
        return {
            "success": True,
            "action": "organized",
            "moved": moved,
            "categories": {cat: len(files) for cat, files in plan.items()},
        }

    return {
        "success": True,
        "action": "preview",
        "plan": {cat: {"count": len(files), "files": files[:5]} for cat, files in plan.items()},
        "total_files": sum(len(f) for f in plan.values()),
    }


def find_recent_files(folder_name="다운로드", hours=24, max_results=10):
    """최근 N시간 내 생성/수정된 파일 검색"""
    folder = resolve_folder(folder_name)
    if not folder or not folder.exists():
        return {"success": False, "message": f"'{folder_name}' 폴더를 찾을 수 없습니다."}

    cutoff = datetime.now().timestamp() - (hours * 3600)
    results = []

    try:
        for f in folder.iterdir():
            if f.name.startswith(".") or f.is_dir():
                continue
            stat = f.stat()
            if stat.st_mtime >= cutoff:
                results.append({
                    "name": f.name,
                    "path": str(f),
                    "size": format_size(stat.st_size),
                    "modified": format_time(stat.st_mtime),
                    "category": get_file_category(f.suffix),
                })
    except PermissionError:
        return {"success": False, "message": "접근 권한이 없습니다."}

    results.sort(key=lambda x: x["modified"])
    return {
        "success": True,
        "folder": str(folder),
        "hours": hours,
        "total": len(results),
        "files": results[:max_results],
    }


def move_files(source_folder, dest_folder, extension=None, keyword=None):
    """파일 이동"""
    src = resolve_folder(source_folder)
    dst = resolve_folder(dest_folder)

    if not src or not src.exists():
        return {"success": False, "message": f"원본 폴더 '{source_folder}'를 찾을 수 없습니다."}

    if _sandbox_on():
        # 샌드박스 강제 모드: 대상 경로를 가상 루트 내부로 앵커링 + 심볼릭 링크 경계 검증
        try:
            dst = _sb.resolve_in_sandbox(dest_folder, create_parents=True)
            dst.mkdir(parents=True, exist_ok=True)
        except PermissionError as e:
            return {"success": False, "message": f"대상 폴더 보안 위반: {e}"}
    else:
        if not dst:
            # 별칭이 아닌 새 폴더명 → 경로 트래버설('../') 차단을 위해 파일명만 허용
            safe_name = sanitize_filename(dest_folder) if _SECURITY else os.path.basename(str(dest_folder))
            if not safe_name:
                return {"success": False, "message": "잘못된 대상 폴더명입니다."}
            dst = HOME / safe_name

        # 보안: 대상 경로가 허용 샌드박스(HOME 등) 안이고 차단 경로가 아닌지 최종 검증
        if _SECURITY:
            safe, reason, resolved = validate_path(str(dst))
            if not safe:
                return {"success": False, "message": f"대상 폴더 보안 위반: {reason}"}
            dst = resolved
        dst.mkdir(parents=True, exist_ok=True)

    moved = []
    try:
        for f in src.iterdir():
            if f.name.startswith(".") or f.is_dir():
                continue
            if extension and f.suffix.lower() != extension.lower():
                continue
            if keyword and keyword.lower() not in f.name.lower():
                continue
            target = dst / f.name
            if target.exists():
                base = target.stem
                ext = target.suffix
                counter = 1
                while target.exists():
                    target = dst / f"{base}_{counter}{ext}"
                    counter += 1
            # 샌드박스 모드: 이동 직전 최종 경로(심볼릭 링크 추적)가 루트 내부인지 재검증
            if _sandbox_on():
                _sb.assert_within_sandbox(target.parent)
            shutil.move(str(f), str(target))
            moved.append(f.name)
    except PermissionError:
        return {"success": False, "message": "파일 이동 권한이 없습니다."}

    return {
        "success": True,
        "moved_count": len(moved),
        "moved_files": moved[:10],
        "destination": str(dst),
    }


def open_file(file_path):
    """파일 또는 폴더 열기 (macOS: open 명령)"""
    if _sandbox_on():
        # 샌드박스 강제 모드: 경로를 가상 루트 내부로 앵커링 + 심볼릭 링크 경계 검증
        try:
            p = _sb.resolve_in_sandbox(file_path, must_exist=False)
        except PermissionError as e:
            return {"success": False, "message": f"보안 정책상 열 수 없습니다: {e}"}
        if not p.exists():
            folder = _resolve_folder_sandbox(file_path)
            if folder and folder.exists():
                p = folder
            else:
                return {"success": False, "message": f"'{file_path}'를 샌드박스에서 찾을 수 없습니다."}
    else:
        p = Path(file_path).expanduser()
        if not p.exists():
            # 폴더 이름으로 시도
            folder = resolve_folder(file_path)
            if folder and folder.exists():
                p = folder
            else:
                return {"success": False, "message": f"'{file_path}'를 찾을 수 없습니다."}

        # 보안: 함수 내부에서도 경로 재검증 — 게이트 우회·심볼릭릭·민감파일(.env 등) 차단
        if _SECURITY:
            safe, reason, resolved = validate_path(str(p), must_exist=True)
            if not safe:
                return {"success": False, "message": f"보안 정책상 열 수 없습니다: {reason}"}
            p = resolved

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", str(p)], check=True)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", str(p)], check=True)
        elif sys.platform == "win32":
            os.startfile(str(p))
        register_recent_file(str(p), p.name, "opened")
        return {"success": True, "opened": str(p)}
    except Exception as e:
        return {"success": False, "message": f"열기 실패: {e}"}


def delete_file(file_path):
    """파일/빈 폴더 삭제 — 오직 샌드박스 내부만 영향. 외부 경로/심볼릭 링크는 차단."""
    if _sandbox_on():
        try:
            p = _sb.resolve_in_sandbox(file_path, must_exist=True)
        except PermissionError as e:
            return {"success": False, "message": f"보안 정책상 삭제할 수 없습니다: {e}"}
        except FileNotFoundError:
            return {"success": False, "message": f"'{file_path}'를 샌드박스에서 찾을 수 없습니다."}
        if str(p) == str(_sb.SANDBOX_ROOT):
            return {"success": False, "message": "샌드박스 루트는 삭제할 수 없습니다."}
    else:
        # 비강제 모드: 기존 보안 경로 검증으로 시스템 보호
        p = Path(file_path).expanduser()
        if not _SECURITY:
            return {"success": False, "message": "보안 모듈 없이 삭제는 허용되지 않습니다."}
        safe, reason, resolved = validate_path(str(p), must_exist=True)
        if not safe:
            return {"success": False, "message": f"보안 정책상 삭제할 수 없습니다: {reason}"}
        p = resolved

    try:
        if p.is_dir() and not p.is_symlink():
            p.rmdir()  # 빈 폴더만 (재귀 삭제는 의도적으로 미지원)
        else:
            p.unlink()
        if _SECURITY:
            try:
                audit_tool_call("delete_file", {"file_path": file_path}, {"success": True})
            except Exception:
                pass
        return {"success": True, "deleted": str(p)}
    except OSError as e:
        return {"success": False, "message": f"삭제 실패: {e}"}


def open_app(app_name):
    """앱 실행 (macOS) — 화이트리스트에 등록된 앱만 허용"""
    app_lower = app_name.lower().strip()
    resolved = APP_ALIASES.get(app_lower, app_name)

    # 보안: 화이트리스트 강제 — 임의 앱/고위험 앱(Terminal·Script Editor 등) 실행 차단
    if resolved not in ALLOWED_APPS:
        if _SECURITY:
            try:
                audit_tool_call("open_app", {"app_name": app_name, "resolved": resolved},
                                {"success": False})
            except Exception:
                pass
        return {"success": False,
                "message": f"'{app_name}' 실행은 보안 정책상 허용되지 않습니다."}

    try:
        if sys.platform == "darwin":
            subprocess.run(["open", "-a", resolved], check=True)
            register_recent_file(resolved, resolved, "app")
            try:
                from memory_store import record_app_open
                record_app_open(resolved)
            except ImportError:
                pass
            return {"success": True, "opened": resolved}
        elif sys.platform.startswith("linux"):
            subprocess.Popen([resolved.lower()])
            return {"success": True, "opened": resolved}
        elif sys.platform == "win32":
            os.startfile(resolved)
            return {"success": True, "opened": resolved}
    except Exception as e:
        return {"success": False, "message": f"'{resolved}' 앱을 열 수 없습니다: {e}"}


def get_disk_usage():
    """디스크 사용량 조회"""
    usage = shutil.disk_usage(str(HOME))
    return {
        "success": True,
        "total": format_size(usage.total),
        "used": format_size(usage.used),
        "free": format_size(usage.free),
        "percent_used": round(usage.used / usage.total * 100, 1),
    }


def find_large_files(folder_name="홈", min_size_mb=100, max_results=15):
    """대용량 파일 찾기"""
    folder = resolve_folder(folder_name)
    if not folder or not folder.exists():
        return {"success": False, "message": f"'{folder_name}' 폴더를 찾을 수 없습니다."}

    min_bytes = min_size_mb * 1024 * 1024
    results = []

    try:
        for f in folder.rglob("*"):
            if f.name.startswith(".") or f.is_dir():
                continue
            try:
                stat = f.stat()
                if stat.st_size >= min_bytes:
                    results.append({
                        "name": f.name,
                        "path": str(f),
                        "size": format_size(stat.st_size),
                        "size_bytes": stat.st_size,
                        "modified": format_time(stat.st_mtime),
                    })
            except (PermissionError, OSError):
                continue
    except PermissionError:
        pass

    results.sort(key=lambda x: x["size_bytes"], reverse=True)
    for r in results:
        del r["size_bytes"]

    return {
        "success": True,
        "folder": str(folder),
        "min_size": f"{min_size_mb} MB",
        "total": len(results),
        "files": results[:max_results],
    }


# ──────────────────────────────────────────────
#  도구 목록 (LLM에 전달)
# ──────────────────────────────────────────────

FILE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "폴더에서 파일을 검색합니다. 확장자나 키워드로 필터링할 수 있습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "검색할 폴더 (예: 바탕화면, 다운로드, 문서)"},
                    "extension": {"type": "string", "description": "파일 확장자 필터 (예: .pdf, .jpg)"},
                    "keyword": {"type": "string", "description": "파일명에 포함된 키워드"},
                },
                "required": ["folder_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_folder",
            "description": "폴더의 내용을 카테고리별로 요약합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "조회할 폴더 (예: 바탕화면, 다운로드)"},
                },
                "required": ["folder_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "organize_folder",
            "description": "폴더의 파일을 카테고리별(이미지, 문서, 영상 등) 하위 폴더로 자동 정리합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "정리할 폴더 (예: 바탕화면, 다운로드)"},
                    "dry_run": {"type": "boolean", "description": "true면 미리보기만, false면 실제 정리 실행", "default": True},
                },
                "required": ["folder_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_recent_files",
            "description": "최근 N시간 내에 생성되거나 수정된 파일을 찾습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "검색할 폴더", "default": "다운로드"},
                    "hours": {"type": "integer", "description": "최근 몇 시간 이내", "default": 24},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "move_files",
            "description": "파일을 한 폴더에서 다른 폴더로 이동합니다. 확장자나 키워드로 필터링할 수 있습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_folder": {"type": "string", "description": "원본 폴더"},
                    "dest_folder": {"type": "string", "description": "목적지 폴더"},
                    "extension": {"type": "string", "description": "이동할 파일 확장자 (예: .png)"},
                    "keyword": {"type": "string", "description": "파일명 키워드 필터"},
                },
                "required": ["source_folder", "dest_folder"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_file",
            "description": "파일이나 폴더를 기본 앱으로 엽니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "열 파일 또는 폴더 경로"},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_file",
            "description": "샌드박스 내부의 파일 또는 빈 폴더를 삭제합니다. 샌드박스 외부/시스템 파일은 절대 삭제되지 않습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "삭제할 파일 또는 빈 폴더 경로"},
                },
                "required": ["file_path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "앱을 실행합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "앱 이름 (예: 크롬, 사파리, 터미널, vscode)"},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_disk_usage",
            "description": "디스크 사용량을 조회합니다.",
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
            "name": "find_large_files",
            "description": "대용량 파일을 찾습니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_name": {"type": "string", "description": "검색할 폴더", "default": "홈"},
                    "min_size_mb": {"type": "integer", "description": "최소 파일 크기 (MB)", "default": 100},
                },
                "required": [],
            },
        },
    },
]


# 함수 이름 → 실제 함수 매핑
TOOL_MAP = {
    "search_files": search_files,
    "list_folder": list_folder,
    "organize_folder": organize_folder,
    "find_recent_files": find_recent_files,
    "move_files": move_files,
    "open_file": open_file,
    "delete_file": delete_file,
    "open_app": open_app,
    "get_disk_usage": get_disk_usage,
    "find_large_files": find_large_files,
}


def execute_tool(tool_name, arguments):
    """도구 실행"""
    func = TOOL_MAP.get(tool_name)
    if not func:
        return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
        return func(**arguments)
    except Exception as e:
        return {"success": False, "message": f"실행 오류: {e}"}
