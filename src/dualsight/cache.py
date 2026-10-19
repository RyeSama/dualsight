"""Perception cache: in-memory LRU and an optional SQLite store."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .chat import Usage
from .errors import ConfigError
from .models import Perception


def cache_key(
    *,
    image_sha256: str,
    provider: str,
    model: str,
    profile: str,
    detail: str,
    prompt_version: str,
) -> str:
    payload = json.dumps(
        {
            "image": image_sha256,
            "provider": provider,
            "model": model,
            "profile": profile,
            "detail": detail,
            "prompt": prompt_version,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CacheEntry:
    perception: Perception
    usage: Usage | None
    created_at: float
    provider: str
    model: str
    profile: str
    detail: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "perception": self.perception.model_dump(mode="json"),
                "usage": self.usage.to_dict() if self.usage else None,
                "created_at": self.created_at,
                "provider": self.provider,
                "model": self.model,
                "profile": self.profile,
                "detail": self.detail,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, value: str) -> CacheEntry:
        data = json.loads(value)
        usage_data = data.get("usage")
        usage = (
            Usage(
                prompt_tokens=usage_data.get("prompt_tokens"),
                completion_tokens=usage_data.get("completion_tokens"),
                total_tokens=usage_data.get("total_tokens"),
            )
            if usage_data
            else None
        )
        return cls(
            perception=Perception.model_validate(data["perception"]),
            usage=usage,
            created_at=float(data["created_at"]),
            provider=str(data.get("provider", "")),
            model=str(data.get("model", "")),
            profile=str(data.get("profile", "")),
            detail=str(data.get("detail", "")),
        )


class PerceptionCache:
    """Small interface shared by the memory and SQLite implementations."""

    def get(self, key: str) -> CacheEntry | None:  # pragma: no cover - interface
        raise NotImplementedError

    def set(self, key: str, entry: CacheEntry) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def clear(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def stats(self) -> dict[str, Any]:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        pass


class NullCache(PerceptionCache):
    def get(self, key: str) -> CacheEntry | None:
        return None

    def set(self, key: str, entry: CacheEntry) -> None:
        return None

    def clear(self) -> None:
        return None

    def stats(self) -> dict[str, Any]:
        return {"backend": "none", "entries": 0, "hits": 0, "misses": 0}


class MemoryCache(PerceptionCache):
    """LRU + TTL cache. Safe for the single event loop FastAPI runs on."""

    def __init__(self, *, ttl_s: int, max_entries: int) -> None:
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._hits = 0
        self._misses = 0
        self._lock = threading.Lock()

    def get(self, key: str) -> CacheEntry | None:
        now = time.time()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            if self.ttl_s and now - entry.created_at > self.ttl_s:
                self._entries.pop(key, None)
                self._misses += 1
                return None
            self._entries.move_to_end(key)
            self._hits += 1
            return entry

    def set(self, key: str, entry: CacheEntry) -> None:
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "backend": "memory",
                "entries": len(self._entries),
                "hits": self._hits,
                "misses": self._misses,
            }


class SQLiteCache(PerceptionCache):
    """A tiny durable cache. No ORM, no background writer, no surprises."""

    def __init__(self, path: str, *, ttl_s: int, max_entries: int) -> None:
        self.path = Path(path)
        self.ttl_s = ttl_s
        self.max_entries = max_entries
        self._lock = threading.Lock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.path, check_same_thread=False)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute(
                """
                CREATE TABLE IF NOT EXISTS perceptions (
                    key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            self._connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_perceptions_created ON perceptions(created_at)"
            )
            self._connection.commit()
        except (OSError, sqlite3.Error) as exc:
            raise ConfigError(f"cannot open SQLite cache at {self.path}: {exc}") from exc
        self._hits = 0
        self._misses = 0

    def get(self, key: str) -> CacheEntry | None:
        now = time.time()
        with self._lock:
            row = self._connection.execute(
                "SELECT payload, created_at FROM perceptions WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                self._misses += 1
                return None
            payload, created_at = row
            if self.ttl_s and now - float(created_at) > self.ttl_s:
                self._connection.execute("DELETE FROM perceptions WHERE key = ?", (key,))
                self._connection.commit()
                self._misses += 1
                return None
            self._hits += 1
        try:
            return CacheEntry.from_json(payload)
        except (ValueError, KeyError):
            with self._lock:
                self._connection.execute("DELETE FROM perceptions WHERE key = ?", (key,))
                self._connection.commit()
            return None

    def set(self, key: str, entry: CacheEntry) -> None:
        with self._lock:
            self._connection.execute(
                "INSERT OR REPLACE INTO perceptions(key, payload, created_at) VALUES (?, ?, ?)",
                (key, entry.to_json(), entry.created_at),
            )
            if self.max_entries:
                count = self._connection.execute("SELECT COUNT(*) FROM perceptions").fetchone()[0]
                if count > self.max_entries:
                    self._connection.execute(
                        """
                        DELETE FROM perceptions WHERE key IN (
                            SELECT key FROM perceptions ORDER BY created_at ASC LIMIT ?
                        )
                        """,
                        (count - self.max_entries,),
                    )
            self._connection.commit()

    def clear(self) -> None:
        with self._lock:
            self._connection.execute("DELETE FROM perceptions")
            self._connection.commit()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            entries = self._connection.execute("SELECT COUNT(*) FROM perceptions").fetchone()[0]
            return {
                "backend": "sqlite",
                "entries": entries,
                "hits": self._hits,
                "misses": self._misses,
                "path": str(self.path),
            }

    def close(self) -> None:
        with self._lock:
            self._connection.close()


def build_cache(settings: Any) -> PerceptionCache:
    if settings.cache == "none":
        return NullCache()
    if settings.cache == "sqlite":
        return SQLiteCache(
            settings.cache_path,
            ttl_s=settings.cache_ttl_s,
            max_entries=settings.cache_max_entries,
        )
    return MemoryCache(ttl_s=settings.cache_ttl_s, max_entries=settings.cache_max_entries)
