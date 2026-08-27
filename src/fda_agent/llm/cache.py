"""Content-hash cache for LLM responses.

CLAUDE.md: every LLM call is content-hash cached. The key is a hash of everything
that determines the answer — model, prompt version, system prompt, user message,
and decoding parameters. Change any of them and you get a new key rather than a
stale hit.

This is not the same thing as Anthropic's prompt caching. That reduces the cost of
a call; this removes the call. Re-running an eval over 38 questions costs nothing
the second time, which is what makes iterating on a prompt affordable.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from fda_agent.config import DATA_DIR

CACHE_DIR = DATA_DIR / "cache" / "llm"


def cache_key(payload: dict) -> str:
    """Stable hash of a request.

    `sort_keys=True` matters: dict ordering must not change the key, or the cache
    silently misses and every run pays full price.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


@dataclass(frozen=True)
class CacheEntry:
    key: str
    value: dict
    hit: bool


class ResponseCache:
    def __init__(self, cache_dir: Path = CACHE_DIR) -> None:
        self.cache_dir = cache_dir

    def _path(self, key: str) -> Path:
        # Shard by prefix so the directory stays navigable at scale.
        return self.cache_dir / key[:2] / f"{key}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if not path.exists():
            return None
        return json.loads(path.read_text())["value"]

    def put(self, key: str, value: dict, meta: dict | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"key": key, "meta": meta or {}, "value": value}, indent=2)
        )
