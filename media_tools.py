"""
J.A.R.V.I.S — Media Pipeline LLM 도구 + 시스템 컨텍스트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
media_pipeline 런타임을 LLM 도구로 노출하고, 전처리 산출물을 System Prompt에 주입.
"""
from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from media_pipeline.runtime import MediaPipelineRuntime

MEDIA_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "submit_media_url",
            "description": (
                "YouTube·외부 라이브/VOD URL의 오디오를 백그라운드에서 안전하게 캡처하고 "
                "48kHz master WAV + 16kHz Whisper mono + 악보 AI/GarageBand용 전처리를 시작합니다. "
                "'유튜브 라이브 오디오 따서 분석해 줘', '이 방송 악보 변환 준비해 줘' 등에 사용."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "YouTube 또는 외부 스트리밍/VOD URL"},
                    "title": {"type": "string", "description": "작업 제목(방송명·곡명 등). 생략 가능"},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_media_pipeline_status",
            "description": "미디어 캡처·전처리 파이프라인 워커 상태와 최근 작업 요약을 조회합니다.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_media_job",
            "description": "대기 중이거나 진행 중인 미디어 캡처 작업을 취소합니다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string", "description": "submit_media_url이 반환한 작업 ID"},
                },
                "required": ["job_id"],
            },
        },
    },
]

_runtime_instance: "MediaPipelineRuntime | None" = None
_RECENT_ARTIFACTS: deque = deque(maxlen=5)


def set_media_runtime(rt: "MediaPipelineRuntime | None") -> None:
    global _runtime_instance
    _runtime_instance = rt


def get_media_runtime() -> "MediaPipelineRuntime | None":
    return _runtime_instance


def register_media_artifact(artifact: dict) -> None:
    if artifact:
        _RECENT_ARTIFACTS.append(dict(artifact))


def get_media_context_suffix() -> str:
    """최근 전처리 산출물 — LLM System Prompt 주입용."""
    if not _RECENT_ARTIFACTS:
        return ""
    lines = ["\n\n[MEDIA PIPELINE — recent preprocessed audio (sandbox paths)]"]
    for art in _RECENT_ARTIFACTS:
        lines.append(
            f"- job={art.get('job_id', '?')}: "
            f"master_48k={art.get('master_wav', '-')} | "
            f"whisper_16k={art.get('whisper_wav', '-')} | "
            f"manifest={art.get('sidecar_json', '-')}"
        )
    lines.append(
        "사용자가 '방금 캡처한 오디오', '전처리 파일', '악보 변환 준비됐어?' 등을 물으면 "
        "위 경로를 참조하라. 실제 파일은 샌드박스 media_captures/ 내부에만 존재한다."
    )
    return "\n".join(lines)


def build_media_overlay_table(event: dict) -> dict:
    """media_ready 이벤트 → HUD overlay_table."""
    meta = event.get("meta") or {}
    artifact = event.get("artifact") or {}
    title = meta.get("title") or "미디어 전처리"
    job_id = meta.get("job_id") or event.get("job_id", "?")
    url = (meta.get("url") or "")[:48]
    return {
        "type": "table",
        "title": f"🎵 {title}",
        "headers": ["항목", "값", "신호"],
        "rows": [
            ["작업 ID", job_id, "+"],
            ["상태", str(meta.get("status", "ready")).upper(), "★"],
            ["Master WAV", "48kHz stereo", "★"],
            ["Whisper WAV", "16kHz mono", "+"],
            ["다운스트림", "악보 AI / GarageBand", "+"],
            ["URL", url or "-", "+"],
        ],
        "alert_text": f"🎵 전처리 완료 — {title}",
    }


def execute_media_tool(tool_name: str, arguments) -> dict:
    rt = _runtime_instance
    if not rt:
        return {"success": False, "message": "미디어 파이프라인이 비활성화되어 있습니다."}
    try:
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    except Exception:
        arguments = arguments if isinstance(arguments, dict) else {}

    if tool_name == "submit_media_url":
        url = (arguments.get("url") or "").strip()
        title = (arguments.get("title") or "").strip() or "외부 미디어"
        if not url:
            return {"success": False, "message": "URL이 필요합니다."}
        try:
            from media_pipeline.url_guard import preflight_media_url
            from media_pipeline.errors import URLGuardError

            preflight_media_url(url)
        except URLGuardError as e:
            return {"success": False, "message": f"보안 차단(SSRF 방지): {e.message}"}
        except Exception as e:
            return {"success": False, "message": f"URL 검증 실패: {e}"}

        job = rt.submit_url(url, title=title)
        msg = (
            f"선생님, 백그라운드에서 스트리밍 캡처 및 전처리를 시작했습니다. "
            f"(작업 ID: {job.job_id})"
        )
        return {
            "success": True,
            "message": msg,
            "briefing": msg,
            "job_id": job.job_id,
            "status": job.status.value,
            "overlay_table": {
                "type": "table",
                "title": f"📡 미디어 캡처 시작 — {title}",
                "headers": ["항목", "값", "신호"],
                "rows": [
                    ["작업 ID", job.job_id, "+"],
                    ["상태", "QUEUED", "+"],
                    ["URL", url[:60], "+"],
                ],
                "alert_text": f"📡 캡처 시작 — {title}",
            },
        }

    if tool_name == "get_media_pipeline_status":
        st = rt.stats()
        worker = st.get("worker") or {}
        recent = [a for a in list(_RECENT_ARTIFACTS)[-3:]]
        return {
            "success": True,
            "enabled": True,
            "worker": worker,
            "prep_pending": st.get("prep_pending", 0),
            "recent_artifacts": recent,
            "message": (
                f"미디어 파이프라인 {'실행 중' if worker.get('running') else '대기'} — "
                f"큐 {worker.get('queued', 0)}건, "
                f"현재 작업 {worker.get('current_job') or '없음'}"
            ),
        }

    if tool_name == "cancel_media_job":
        job_id = (arguments.get("job_id") or "").strip()
        if not job_id:
            return {"success": False, "message": "job_id가 필요합니다."}
        ok = rt.cancel_job(job_id)
        if ok:
            return {"success": True, "message": f"작업 {job_id} 취소 요청을 처리했습니다."}
        return {"success": False, "message": f"작업 {job_id}를 취소할 수 없습니다(없거나 이미 완료)."}

    return {"success": False, "message": f"알 수 없는 도구: {tool_name}"}
