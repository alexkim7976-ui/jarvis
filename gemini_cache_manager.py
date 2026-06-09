"""
J.A.R.V.I.S — Gemini Context Cache 매니저

고정 시스템 프롬프트·도구 선언·프로젝트 소스 컨텍스트를 Google Context Cache에
올려 매 요청마다 중복 입력 토큰 비용을 절감한다 (최대 ~80% 할인).

환경변수:
  GEMINI_CACHE_ENABLED=1          캐싱 활성 (기본 ON, GEMINI_API_KEY 필요)
  GEMINI_CACHE_TTL_SEC=7200       캐시 TTL (초, 기본 2시간)
  GEMINI_CACHE_REFRESH_BEFORE=600   만료 N초 전 TTL 자동 연장
  GEMINI_CACHE_CONTEXT_FILES        캐시에 포함할 파일 (쉼표 구분, 프로젝트 루트 기준)
  GEMINI_CACHE_MAX_FILE_KB=256      파일당 최대 KB (초과 시 앞부분만)
  GEMINI_CACHE_REGISTRY=.cache/gemini_cache_registry.json

google-generativeai >= 0.7.0 의 caching.CachedContent API 사용.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

PROJECT_ROOT = Path(__file__).resolve().parent

_DEFAULT_CONTEXT_FILES = (
    "security.py,file_manager.py,stock_quant.py,news_analyzer.py,"
    "portfolio_manager.py,supply_chain_module.py,cross_validation.py"
)

_log_fn: Callable[[str, str], None] | None = None


def set_cache_logger(fn: Callable[[str, str], None]) -> None:
    global _log_fn
    _log_fn = fn


def _log(level: str, msg: str) -> None:
    if _log_fn:
        _log_fn(level, msg)
    else:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] 💾 [GeminiCache] {msg}")


def cache_enabled() -> bool:
    if not os.getenv("GEMINI_API_KEY", "").strip():
        try:
            from secure_config import get_secret
            if not get_secret("GEMINI_API_KEY"):
                return False
        except Exception:
            return False
    return os.getenv("GEMINI_CACHE_ENABLED", "1").strip().lower() not in (
        "0", "false", "no", "off",
    )


def cache_ttl_seconds() -> int:
    return max(300, int(os.getenv("GEMINI_CACHE_TTL_SEC", "7200")))


def cache_refresh_before_seconds() -> int:
    return max(60, int(os.getenv("GEMINI_CACHE_REFRESH_BEFORE", "600")))


def cache_context_files() -> list[Path]:
    raw = os.getenv("GEMINI_CACHE_CONTEXT_FILES", _DEFAULT_CONTEXT_FILES)
    paths: list[Path] = []
    for part in raw.split(","):
        p = part.strip()
        if not p:
            continue
        fp = Path(p)
        if not fp.is_absolute():
            fp = PROJECT_ROOT / fp
        if fp.is_file():
            paths.append(fp)
        else:
            _log("WARN", f"캐시 컨텍스트 파일 없음 — 건너뜀: {fp}")
    return paths


def build_gemini_tool_decls(genai, tools_list: list) -> list:
    """OpenAI function-calling 스키마 → Gemini FunctionDeclaration."""
    decls = []
    for tool in tools_list:
        func = tool["function"]
        props = {}
        for k, v in func["parameters"].get("properties", {}).items():
            pt = v.get("type", "string").upper()
            if pt == "INTEGER":
                pt = "NUMBER"
            if pt not in ("STRING", "NUMBER", "BOOLEAN", "OBJECT", "ARRAY"):
                pt = "STRING"
            props[k] = genai.protos.Schema(
                type=getattr(genai.protos.Type, pt, genai.protos.Type.STRING),
                description=v.get("description", ""),
            )
        decls.append(
            genai.protos.FunctionDeclaration(
                name=func["name"],
                description=func["description"],
                parameters=genai.protos.Schema(
                    type=genai.protos.Type.OBJECT,
                    properties=props,
                    required=func["parameters"].get("required", []),
                ),
            )
        )
    return [genai.protos.Tool(function_declarations=decls)]


def log_gemini_usage(response, *, label: str = "Gemini") -> dict:
    """응답 usage_metadata에서 캐시 히트 여부를 로그."""
    stats = {"cached_tokens": 0, "prompt_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    um = getattr(response, "usage_metadata", None)
    if not um:
        return stats
    stats["cached_tokens"] = int(getattr(um, "cached_content_token_count", 0) or 0)
    stats["prompt_tokens"] = int(getattr(um, "prompt_token_count", 0) or 0)
    stats["output_tokens"] = int(
        getattr(um, "candidates_token_count", 0) or getattr(um, "output_token_count", 0) or 0
    )
    stats["total_tokens"] = int(getattr(um, "total_token_count", 0) or 0)
    if stats["cached_tokens"] > 0:
        _log(
            "OK",
            f"{label} 토큰 — 캐시히트 {stats['cached_tokens']:,} | "
            f"신규입력 {stats['prompt_tokens']:,} | "
            f"출력 {stats['output_tokens']:,} | "
            f"합계 {stats['total_tokens']:,}",
        )
    elif stats["prompt_tokens"] > 0:
        _log(
            "INFO",
            f"{label} 토큰 — 입력 {stats['prompt_tokens']:,} | "
            f"출력 {stats['output_tokens']:,} (캐시 미사용)",
        )
    return stats


class GeminiCacheManager:
    """Gemini Context Cache 싱글톤 — 생성·재사용·TTL 갱신."""

    _instance: Optional["GeminiCacheManager"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._api_key = ""
        self._flash_model = os.getenv("GEMINI_FLASH_MODEL", "gemini-3.5-flash")
        self._vision_model = os.getenv("GEMINI_PRO_MODEL", "gemini-1.5-pro")
        self._ttl_sec = cache_ttl_seconds()
        self._refresh_before = cache_refresh_before_seconds()
        self._registry_path = Path(
            os.getenv("GEMINI_CACHE_REGISTRY", str(PROJECT_ROOT / ".cache" / "gemini_cache_registry.json"))
        )
        self._local: dict[str, dict] = {}
        self._runtime: dict[str, Any] = {}  # profile -> CachedContent
        self._load_registry()

    @classmethod
    def get(cls) -> "GeminiCacheManager":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def configure(self, api_key: str, flash_model: str = "", vision_model: str = "") -> None:
        self._api_key = api_key or self._api_key
        if flash_model:
            self._flash_model = flash_model
        if vision_model:
            self._vision_model = vision_model

    @staticmethod
    def _normalize_model(model_name: str) -> str:
        return model_name if model_name.startswith("models/") else f"models/{model_name}"

    def _display_name(self, profile: str, fingerprint: str) -> str:
        return f"jarvis-{profile}-{fingerprint[:12]}"

    def _fingerprint(
        self,
        *,
        profile: str,
        model_name: str,
        system_instruction: str,
        tools_list: list | None,
        extra_contents: str,
    ) -> str:
        tool_blob = json.dumps(tools_list or [], ensure_ascii=False, sort_keys=True)
        payload = "|".join([profile, model_name, system_instruction, tool_blob, extra_contents])
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _load_registry(self) -> None:
        try:
            if self._registry_path.is_file():
                self._local = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except Exception as e:
            _log("WARN", f"로컬 레지스트리 로드 실패: {e}")
            self._local = {}

    def _save_registry(self) -> None:
        try:
            self._registry_path.parent.mkdir(parents=True, exist_ok=True)
            self._registry_path.write_text(
                json.dumps(self._local, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            _log("WARN", f"로컬 레지스트리 저장 실패: {e}")

    def _load_context_blob(self) -> str:
        max_kb = max(16, int(os.getenv("GEMINI_CACHE_MAX_FILE_KB", "256")))
        chunks: list[str] = []
        for fp in cache_context_files():
            try:
                text = fp.read_text(encoding="utf-8", errors="replace")
                limit = max_kb * 1024
                if len(text) > limit:
                    text = text[:limit] + f"\n\n... [truncated at {max_kb}KB] ..."
                chunks.append(f"### FILE: {fp.name}\n```\n{text}\n```")
            except Exception as e:
                _log("WARN", f"파일 읽기 실패 {fp}: {e}")
        return "\n\n".join(chunks)

    @staticmethod
    def _cache_contents_from_blob(blob: str) -> list[dict] | None:
        if not blob.strip():
            return None
        return [
            {
                "role": "user",
                "parts": [
                    {
                        "text": (
                            "# JARVIS Project Source Context (cached)\n"
                            "아래는 프로젝트 핵심 모듈 소스입니다. 도구 호출·보안 규칙 참고용.\n\n"
                            + blob
                        )
                    }
                ],
            },
            {
                "role": "model",
                "parts": [{"text": "프로젝트 컨텍스트를 로드했습니다. 도구와 규칙을 준수하겠습니다."}],
            },
        ]

    def _is_expired(self, expire_iso: str) -> bool:
        try:
            exp = datetime.fromisoformat(expire_iso.replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            return exp <= datetime.now(timezone.utc)
        except Exception:
            return True

    def _maybe_extend_ttl(self, cached) -> None:
        try:
            exp = cached.expire_time
            if not exp:
                return
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            remaining = (exp - datetime.now(timezone.utc)).total_seconds()
            if remaining < self._refresh_before:
                cached.update(ttl=self._ttl_sec)
                _log("INFO", f"캐시 TTL 연장 → {self._ttl_sec}s ({cached.name})")
        except Exception as e:
            _log("WARN", f"TTL 연장 실패: {e}")

    def _find_remote_cache(self, genai, *, profile: str, fingerprint: str, model_name: str):
        target = self._display_name(profile, fingerprint)
        norm_model = self._normalize_model(model_name)
        now = datetime.now(timezone.utc)
        try:
            for cc in genai.caching.CachedContent.list(page_size=100):
                if cc.display_name != target:
                    continue
                if norm_model not in cc.model and cc.model not in norm_model:
                    continue
                exp = cc.expire_time
                if exp:
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=timezone.utc)
                    if exp <= now:
                        continue
                _log("OK", f"기존 캐시 재사용 (list) — {cc.name} | tokens≈{cc.usage_metadata.total_token_count:,}")
                self._maybe_extend_ttl(cc)
                return cc
        except Exception as e:
            _log("WARN", f"캐시 목록 조회 실패: {e}")
        return None

    def _find_local_cache(self, genai, *, profile: str, fingerprint: str):
        entry = self._local.get(profile) or {}
        if entry.get("fingerprint") != fingerprint:
            return None
        name = entry.get("name", "")
        expire_iso = entry.get("expire_time", "")
        if not name or self._is_expired(expire_iso):
            return None
        try:
            cc = genai.caching.CachedContent.get(name)
            _log("OK", f"기존 캐시 재사용 (registry) — {cc.name}")
            self._maybe_extend_ttl(cc)
            return cc
        except Exception:
            return None

    def _persist_cache(self, profile: str, fingerprint: str, cached) -> None:
        exp = cached.expire_time
        expire_iso = exp.isoformat() if exp else ""
        self._local[profile] = {
            "name": cached.name,
            "fingerprint": fingerprint,
            "display_name": cached.display_name,
            "model": cached.model,
            "expire_time": expire_iso,
            "total_token_count": int(getattr(cached.usage_metadata, "total_token_count", 0) or 0),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        self._save_registry()
        self._runtime[profile] = cached

    def get_or_create(
        self,
        *,
        profile: str,
        model_name: str,
        system_instruction: str,
        tools_list: list | None = None,
        include_project_files: bool = True,
    ):
        """유효 캐시 반환. 실패/비활성 시 None."""
        if not cache_enabled():
            return None

        import google.generativeai as genai

        genai.configure(api_key=self._api_key or os.getenv("GEMINI_API_KEY", ""))

        context_blob = self._load_context_blob() if include_project_files else ""
        fingerprint = self._fingerprint(
            profile=profile,
            model_name=model_name,
            system_instruction=system_instruction,
            tools_list=tools_list,
            extra_contents=context_blob,
        )

        if profile in self._runtime:
            cached = self._runtime[profile]
            entry = self._local.get(profile, {})
            if entry.get("fingerprint") == fingerprint and not self._is_expired(entry.get("expire_time", "")):
                self._maybe_extend_ttl(cached)
                return cached

        cached = self._find_local_cache(genai, profile=profile, fingerprint=fingerprint)
        if cached:
            self._runtime[profile] = cached
            return cached

        cached = self._find_remote_cache(
            genai, profile=profile, fingerprint=fingerprint, model_name=model_name,
        )
        if cached:
            self._persist_cache(profile, fingerprint, cached)
            return cached

        return self._create_cache(
            genai,
            profile=profile,
            model_name=model_name,
            system_instruction=system_instruction,
            tools_list=tools_list,
            fingerprint=fingerprint,
            context_blob=context_blob,
        )

    def _create_cache(
        self,
        genai,
        *,
        profile: str,
        model_name: str,
        system_instruction: str,
        tools_list: list | None,
        fingerprint: str,
        context_blob: str,
    ):
        tools = build_gemini_tool_decls(genai, tools_list) if tools_list else None
        contents = self._cache_contents_from_blob(context_blob)
        display_name = self._display_name(profile, fingerprint)
        norm_model = self._normalize_model(model_name)

        _log(
            "INFO",
            f"새 Context Cache 생성 중 — profile={profile}, model={norm_model}, "
            f"files={len(cache_context_files())}, ttl={self._ttl_sec}s",
        )
        t0 = time.time()
        try:
            cached = genai.caching.CachedContent.create(
                model=norm_model,
                display_name=display_name,
                system_instruction=system_instruction,
                contents=contents,
                tools=tools,
                ttl=self._ttl_sec,
            )
            elapsed = time.time() - t0
            tok = int(getattr(cached.usage_metadata, "total_token_count", 0) or 0)
            _log(
                "OK",
                f"Context Cache 생성 완료 ({elapsed:.1f}s) — {cached.name} | "
                f"cached_tokens≈{tok:,} | expire={cached.expire_time}",
            )
            self._persist_cache(profile, fingerprint, cached)
            return cached
        except Exception as e:
            err = str(e).lower()
            if "minimum" in err or "token" in err or "cache" in err:
                _log(
                    "WARN",
                    f"Context Cache 생성 실패(토큰 부족/모델 미지원 가능) — "
                    f"비캐시 폴백: {e}",
                )
            else:
                _log("WARN", f"Context Cache 생성 실패 — 비캐시 폴백: {e}")
            return None

    def get_flash_model(
        self,
        *,
        system_instruction: str,
        tools_list: list,
        generation_config: dict | None = None,
    ):
        """FLASH 대화 모델 — 캐시 우선, 실패 시 일반 GenerativeModel."""
        import google.generativeai as genai

        genai.configure(api_key=self._api_key)

        cached = self.get_or_create(
            profile="flash",
            model_name=self._flash_model,
            system_instruction=system_instruction,
            tools_list=tools_list,
            include_project_files=True,
        )
        if cached:
            return genai.GenerativeModel.from_cached_content(
                cached,
                generation_config=generation_config or {"temperature": 0.4, "max_output_tokens": 250},
            ), True

        tools = build_gemini_tool_decls(genai, tools_list)
        model = genai.GenerativeModel(
            model_name=self._flash_model,
            system_instruction=system_instruction,
            tools=tools,
            generation_config=generation_config or {"temperature": 0.4, "max_output_tokens": 250},
        )
        return model, False

    def get_vision_model(self, *, system_instruction: str, generation_config: dict | None = None):
        """PRO 비전 모델 — 시스템 프롬프트만 캐시 (도구 없음)."""
        import google.generativeai as genai

        genai.configure(api_key=self._api_key)

        cached = self.get_or_create(
            profile="vision",
            model_name=self._vision_model,
            system_instruction=system_instruction,
            tools_list=None,
            include_project_files=False,
        )
        if cached:
            return genai.GenerativeModel.from_cached_content(
                cached,
                generation_config=generation_config or {"temperature": 0.3, "max_output_tokens": 512},
            ), True

        model = genai.GenerativeModel(
            model_name=self._vision_model,
            system_instruction=system_instruction,
            generation_config=generation_config or {"temperature": 0.3, "max_output_tokens": 512},
        )
        return model, False

    def warm_up(
        self,
        *,
        flash_system_instruction: str,
        flash_tools: list,
        vision_system_instruction: str = "",
    ) -> None:
        """부팅 시 백그라운드 캐시 프리워밍."""
        if not cache_enabled():
            _log("INFO", "Context Cache 비활성 (GEMINI_CACHE_ENABLED=0)")
            return
        t0 = time.time()
        _, flash_ok = self.get_flash_model(
            system_instruction=flash_system_instruction,
            tools_list=flash_tools,
        )
        vision_ok = False
        if vision_system_instruction:
            _, vision_ok = self.get_vision_model(system_instruction=vision_system_instruction)
        _log(
            "OK" if flash_ok else "INFO",
            f"캐시 워밍업 완료 ({time.time()-t0:.1f}s) — flash={'HIT' if flash_ok else 'MISS'}, "
            f"vision={'HIT' if vision_ok else 'N/A'}",
        )

    def status(self) -> dict:
        out = {"enabled": cache_enabled(), "ttl_sec": self._ttl_sec, "profiles": {}}
        for profile, entry in self._local.items():
            out["profiles"][profile] = {
                "name": entry.get("name"),
                "tokens": entry.get("total_token_count"),
                "expire_time": entry.get("expire_time"),
                "fingerprint": (entry.get("fingerprint") or "")[:12],
            }
        return out


def get_cache_manager() -> GeminiCacheManager:
    return GeminiCacheManager.get()
