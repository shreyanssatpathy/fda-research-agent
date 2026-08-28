"""Hard spend ceiling, enforced in code.

CLAUDE.md: the ceiling is enforced here, not by asking the model to be frugal. A
prompt cannot enforce a budget — it is text the model may ignore, and it cannot
see the running total across a batch. This can.

Spend is tracked in a JSON file so a ceiling survives process restarts. A run that
crashes halfway does not get a fresh budget on the next attempt.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from fda_agent.config import DATA_DIR

LEDGER_PATH = DATA_DIR / "logs" / "llm_spend.json"

# USD per million tokens. Source: Anthropic pricing, recorded 2026-08-27.
# Cache reads bill at ~0.1x input; cache writes at ~1.25x.
PRICING = {
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-sonnet-5": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}

CACHE_READ_MULTIPLIER = 0.1
CACHE_WRITE_MULTIPLIER = 1.25

# Ceiling for the whole project. Deliberately small: this is a portfolio project
# on a public repo, and an accidental full-corpus loop is the realistic failure.
DEFAULT_CEILING_USD = 20.00


class BudgetExceeded(Exception):
    """Raised before a call is made, never after. No spend happens on refusal."""


class LedgerCorrupted(Exception):
    """The spend ledger is unreadable, so the ceiling cannot be enforced.

    Deliberately fatal rather than falling back to zero: an unreadable ledger is
    the one moment when assuming "nothing spent yet" is most dangerous.
    """


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


def cost_usd(usage: Usage, model: str) -> float:
    """Price one call. Unknown models raise rather than silently costing nothing."""
    if model not in PRICING:
        raise ValueError(
            f"no pricing recorded for {model!r}; add it to PRICING before use "
            "so spend is never undercounted"
        )
    rate = PRICING[model]
    per_token_in = rate["input"] / 1_000_000
    per_token_out = rate["output"] / 1_000_000
    return (
        usage.input_tokens * per_token_in
        + usage.output_tokens * per_token_out
        + usage.cache_read_tokens * per_token_in * CACHE_READ_MULTIPLIER
        + usage.cache_write_tokens * per_token_in * CACHE_WRITE_MULTIPLIER
    )


class Budget:
    """A persistent spend ceiling.

    `check()` is called before every LLM request and raises if the ceiling is
    already reached. `record()` is called after, with real usage from the response.
    """

    def __init__(
        self,
        ceiling_usd: float = DEFAULT_CEILING_USD,
        ledger_path: Path = LEDGER_PATH,
    ) -> None:
        self.ceiling_usd = ceiling_usd
        self.ledger_path = ledger_path

    def _read(self) -> dict:
        if not self.ledger_path.exists():
            return {"spent_usd": 0.0, "calls": 0, "by_model": {}}
        raw = self.ledger_path.read_text()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as err:
            # A corrupted ledger must fail as a budget problem with a clear
            # remedy, not as a JSONDecodeError from deep in the call stack.
            # Never silently reset to zero: that would hand back an unlimited
            # budget exactly when the accounting is untrustworthy.
            raise LedgerCorrupted(
                f"{self.ledger_path} is not valid JSON ({err}). Spend so far "
                "cannot be verified, so no further calls are permitted. Inspect "
                "the file and repair or delete it deliberately."
            ) from err

    @property
    def spent_usd(self) -> float:
        return float(self._read()["spent_usd"])

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.ceiling_usd - self.spent_usd)

    def check(self, *, estimated_usd: float = 0.0) -> None:
        """Refuse before spending. Raises BudgetExceeded."""
        spent = self.spent_usd
        if spent >= self.ceiling_usd:
            raise BudgetExceeded(
                f"spend ceiling reached: ${spent:.4f} of ${self.ceiling_usd:.2f}. "
                "Raise the ceiling deliberately or clear the ledger."
            )
        if estimated_usd and spent + estimated_usd > self.ceiling_usd:
            raise BudgetExceeded(
                f"call would exceed ceiling: ${spent:.4f} spent, "
                f"${estimated_usd:.4f} estimated, ceiling ${self.ceiling_usd:.2f}"
            )

    def record(self, usage: Usage, model: str) -> float:
        """Add real usage to the ledger and return this call's cost."""
        cost = cost_usd(usage, model)
        ledger = self._read()
        ledger["spent_usd"] = round(float(ledger["spent_usd"]) + cost, 6)
        ledger["calls"] = int(ledger["calls"]) + 1
        by_model = ledger.setdefault("by_model", {})
        entry = by_model.setdefault(model, {"calls": 0, "usd": 0.0})
        entry["calls"] += 1
        entry["usd"] = round(entry["usd"] + cost, 6)
        ledger["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

        self.ledger_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_atomic(ledger)
        return cost

    def _write_atomic(self, ledger: dict) -> None:
        """Write via a temp file and rename.

        `write_text` is not atomic: a crash or a concurrent writer leaves a
        half-written file, and one stray byte makes every later call fail. Seen
        in practice — the ledger ended up 327 bytes of a 326-byte document with a
        duplicated closing brace. `os.replace` is atomic on POSIX, so a reader
        sees either the old file or the new one.
        """
        tmp = self.ledger_path.with_suffix(f".{os.getpid()}.tmp")
        try:
            tmp.write_text(json.dumps(ledger, indent=2))
            os.replace(tmp, self.ledger_path)
        finally:
            tmp.unlink(missing_ok=True)
