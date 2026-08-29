"""Multi-tier document cache (Memory LRU + Persistent Disk Cache)."""

from __future__ import annotations

from collections import OrderedDict
import datetime
import json
import os
from pathlib import Path
import threading
import time
from typing import Any, Dict, Optional, Tuple, Union

from custom_harness.mcp_grounding.hasher import compute_canonical_hash
from custom_harness.mcp_grounding.schemas import DocContentResult


def get_default_cache_dir() -> Path:
    """Return default disk cache path under ~/.gemini/antigravity/cache/docs."""
    env_dir = os.environ.get("ANTIGRAVITY_DOCS_CACHE_DIR")
    if env_dir:
        cache_path = Path(env_dir).resolve()
    else:
        user_home = Path.home()
        cache_path = user_home / ".gemini" / "antigravity" / "cache" / "docs"
    cache_path.mkdir(parents=True, exist_ok=True)
    return cache_path


def _coerce_doc_result(
    key: str,
    value: Union[DocContentResult, str],
    sha256: Optional[str] = None,
    title: Optional[str] = None,
) -> DocContentResult:
    if isinstance(value, DocContentResult):
        return value
    str_val = str(value)
    computed_sha = sha256 or compute_canonical_hash(str_val)
    return DocContentResult(
        doc_id=key,
        title=title or key,
        content=str_val,
        sha256=computed_sha,
        source_type="local",
        uri=key,
        char_count=len(str_val),
    )


class MemoryLRUCache:
    """Thread-safe In-Memory LRU Cache with TTL support."""

    def __init__(
        self,
        capacity: int = 100,
        default_ttl: int = 3600,
        max_size: Optional[int] = None,
        default_ttl_seconds: Optional[int] = None,
    ):
        self.capacity = max_size if max_size is not None else capacity
        self.default_ttl = default_ttl_seconds if default_ttl_seconds is not None else default_ttl
        self._cache: OrderedDict[str, Tuple[DocContentResult, float]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str, allow_stale: bool = False) -> Optional[Union[DocContentResult, str]]:
        with self._lock:
            if key not in self._cache:
                return None

            item, expiry = self._cache[key]
            now = time.time()
            if not allow_stale and expiry > 0 and now > expiry:
                return None

            self._cache.move_to_end(key)
            return item

    def set(
        self,
        key: str,
        value: Union[DocContentResult, str],
        ttl: Optional[int] = None,
        sha256: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        doc_item = _coerce_doc_result(key, value, sha256=sha256, title=title)
        with self._lock:
            effective_ttl = ttl if ttl is not None else self.default_ttl
            expiry = time.time() + effective_ttl if effective_ttl > 0 else 0.0

            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (doc_item, expiry)

            if len(self._cache) > self.capacity:
                self._cache.popitem(last=False)

    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


class DiskDocCache:
    """Persistent on-disk cache for documentation results with metadata."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        default_ttl: int = 86400,
        default_ttl_seconds: Optional[int] = None,
    ):
        self.cache_dir = Path(cache_dir).resolve() if cache_dir else get_default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.default_ttl = default_ttl_seconds if default_ttl_seconds is not None else default_ttl
        self._lock = threading.Lock()

    def _get_file_path(self, key: str) -> Path:
        import hashlib
        key_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{key_hash}.json"

    def get(self, key: str, allow_stale: bool = False) -> Optional[Union[DocContentResult, str]]:
        file_path = self._get_file_path(key)
        with self._lock:
            if not file_path.is_file():
                return None

            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                expiry = data.get("expiry", 0)
                now = time.time()

                if not allow_stale and expiry > 0 and now > expiry:
                    return None

                doc_data = data.get("doc_result")
                if not doc_data:
                    return None

                # Adjust source_type to live_cached if it was live_fresh
                if doc_data.get("source_type") == "live_fresh":
                    doc_data["source_type"] = "live_cached"

                return DocContentResult.model_validate(doc_data)
            except Exception:
                return None

    def get_meta(self, key: str) -> Optional[Dict[str, Any]]:
        file_path = self._get_file_path(key)
        with self._lock:
            if not file_path.is_file():
                return None
            try:
                data = json.loads(file_path.read_text(encoding="utf-8"))
                return {
                    "etag": data.get("etag"),
                    "last_modified": data.get("last_modified"),
                    "cached_at": data.get("cached_at"),
                    "expiry": data.get("expiry"),
                }
            except Exception:
                return None

    def set(
        self,
        key: str,
        value: Union[DocContentResult, str],
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        ttl: Optional[int] = None,
        sha256: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        doc_item = _coerce_doc_result(key, value, sha256=sha256, title=title)
        file_path = self._get_file_path(key)
        effective_ttl = ttl if ttl is not None else self.default_ttl
        now = time.time()
        expiry = now + effective_ttl if effective_ttl > 0 else 0.0

        payload = {
            "key": key,
            "cached_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "expiry": expiry,
            "etag": etag,
            "last_modified": last_modified or doc_item.last_modified,
            "doc_result": doc_item.model_dump(),
        }

        with self._lock:
            tmp_file = file_path.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp_file.replace(file_path)

    def delete(self, key: str) -> bool:
        file_path = self._get_file_path(key)
        with self._lock:
            if file_path.is_file():
                try:
                    file_path.unlink()
                    return True
                except Exception:
                    return False
            return False

    def clear(self) -> None:
        with self._lock:
            for item in self.cache_dir.glob("*.json"):
                try:
                    item.unlink()
                except Exception:
                    pass


class DocCache:
    """Composite multi-tier cache combining In-Memory LRU and Persistent Disk Cache."""

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        mem_capacity: int = 100,
        mem_ttl: int = 3600,
        disk_ttl: int = 86400,
    ):
        self.memory = MemoryLRUCache(capacity=mem_capacity, default_ttl=mem_ttl)
        self.disk = DiskDocCache(cache_dir=cache_dir, default_ttl=disk_ttl)

    def get(self, key: str, allow_stale: bool = False) -> Optional[DocContentResult]:
        # Tier 1: In-Memory LRU
        mem_hit = self.memory.get(key, allow_stale=allow_stale)
        if isinstance(mem_hit, DocContentResult):
            return mem_hit

        # Tier 2: Disk Cache
        disk_hit = self.disk.get(key, allow_stale=allow_stale)
        if isinstance(disk_hit, DocContentResult):
            # Promote to memory cache
            self.memory.set(key, disk_hit)
            return disk_hit

        return None

    def get_meta(self, key: str) -> Optional[Dict[str, Any]]:
        return self.disk.get_meta(key)

    def set(
        self,
        key: str,
        value: Union[DocContentResult, str],
        etag: Optional[str] = None,
        last_modified: Optional[str] = None,
        ttl: Optional[int] = None,
        sha256: Optional[str] = None,
        title: Optional[str] = None,
    ) -> None:
        doc_item = _coerce_doc_result(key, value, sha256=sha256, title=title)
        self.memory.set(key, doc_item, ttl=ttl)
        self.disk.set(key, doc_item, etag=etag, last_modified=last_modified, ttl=ttl)

    def invalidate(self, key: str) -> None:
        self.memory.delete(key)
        self.disk.delete(key)

    def clear(self) -> None:
        self.memory.clear()
        self.disk.clear()


__all__ = [
    "DocCache",
    "MemoryLRUCache",
    "DiskDocCache",
    "get_default_cache_dir",
]
