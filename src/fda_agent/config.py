"""Paths and constants. No logic here."""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_FDA_DIR = DATA_DIR / "raw" / "fda"
PB_RAW_DIR = DATA_DIR / "raw" / "pitchbook"
PROCESSED_DIR = DATA_DIR / "processed"

DB_PATH = PROCESSED_DIR / "fda.duckdb"

# Bumped whenever the shape of `fda_510k` changes.
SCHEMA_VERSION = "1.0.0"

# V1 is 510(k) only. See CLAUDE.md "V1 scope".
V1_PATHWAY = "510(k)"

# Tables the text-to-SQL layer is permitted to read.
TABLE_ALLOWLIST = frozenset({"fda_510k"})

# LLM. Opus 5 is the default; change deliberately, and update PRICING in
# llm/budget.py in the same edit so spend is never undercounted.
# Override with FDA_MODEL to compare models on the eval set. The model is part
# of the LLM cache key, so switching does not serve another model's answers.
MODEL_ID = os.environ.get("FDA_MODEL", "claude-opus-5")

# Enforced on every generated query.
MAX_ROWS = 1000
STATEMENT_TIMEOUT_S = 30

# Company-name precedence, highest authority first. When one applicant_raw carries
# several clean_name values, the one from the earliest source in this tuple wins.
# Owner ruling 2026-08-27: ming-mapping is authoritative.
SOURCE_PRECEDENCE = (
    "ming-mapping",
    "manual_review",
    "pre-existing",
    "cleaning_script",
    "AI_suggested",
)

# Applicants the precedence rule cannot settle, because the top-precedence source
# disagrees with itself. Awaiting owner ruling; see
# docs/open-questions/company-mapping.md. The load asserts this set exactly, so a
# new ambiguity fails the build instead of passing silently.
# Updated 2026-08-27: the PitchBook bridge resolved ITERATIVE SCOPES and SOFTWARE
# NEMOTEC (both merged — same PitchBook company). Samsung remains, and correctly
# so: PitchBook holds Samsung Electronics (59366-98) and Samsung Medison
# (51387-94) as different companies, so one applicant string legitimately spans
# two firms.
KNOWN_UNRESOLVED_APPLICANTS = frozenset({
    "SAMSUNG ELECTRONICS CO., LTD.",
})
