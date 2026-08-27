"""Load `.env` from the repo root, if present.

Deliberately does not overwrite variables already set in the environment: an
explicit `export` in the shell should win over a stale file. Values are never
logged or echoed.

`.env` is gitignored. `.env.example` is the committed template and holds no
real values.
"""
from __future__ import annotations

import os
from pathlib import Path

from fda_agent.config import REPO_ROOT

ENV_PATH = REPO_ROOT / ".env"


def load_env(path: Path = ENV_PATH) -> list[str]:
    """Set any unset variables from `path`. Returns the names it set."""
    if not path.exists():
        return []

    loaded = []
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded.append(key)
    return loaded
