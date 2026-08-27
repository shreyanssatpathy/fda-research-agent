"""Paths and constants. No logic here."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
RAW_FDA_DIR = DATA_DIR / "raw" / "fda"
PROCESSED_DIR = DATA_DIR / "processed"

DB_PATH = PROCESSED_DIR / "fda.duckdb"

# Bumped whenever the shape of `fda_510k` changes.
SCHEMA_VERSION = "1.0.0"

# V1 is 510(k) only. See CLAUDE.md "V1 scope".
V1_PATHWAY = "510(k)"

# Tables the text-to-SQL layer is permitted to read.
TABLE_ALLOWLIST = frozenset({"fda_510k"})

# Enforced on every generated query.
MAX_ROWS = 1000
STATEMENT_TIMEOUT_S = 30
