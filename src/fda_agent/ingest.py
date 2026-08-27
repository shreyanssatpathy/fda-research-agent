"""Load the FDA AI/ML device extract into DuckDB as the V1 `fda_510k` table.

Deterministic and idempotent: same input file gives the same database. The raw
spreadsheet is never modified. See docs/data-profile-v1.md for why each
transform below exists.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from fda_agent.config import (
    DB_PATH,
    PROCESSED_DIR,
    RAW_FDA_DIR,
    SCHEMA_VERSION,
    V1_PATHWAY,
)

# Source column -> modelled column. Columns absent here are dropped, and the
# reason is recorded in DROPPED below.
COLUMN_MAP = {
    "REGNUMBER": "regnumber",
    "PATHWAY": "pathway",
    "APPLICANT": "applicant_raw",
    "clean_name": "company_name",
    "clean_name_source": "company_name_source",
    "STREET_1": "street",
    "CITY": "city",
    "STATE": "state",
    "COUNTRY": "country",
    "ZIP": "zip",
    "COMMITTEE CODE": "committee_code",
    "MEDICAL SPECIALTY": "medical_specialty",
    "PRODUCT CODE": "product_code",
    "SUBMISSION TYPE": "submission_type",
    "DATE RECEIVED": "date_received",
    "DECISION DATE": "decision_date",
    "DECISION CODE": "decision_code",
    # These two differ by one space in the source and mean different things.
    "DEVICE NAME": "device_trade_name",
    "DEVICENAME": "device_classification_name",
    "DEVICECLASS": "device_class",
    "REGULATIONNUMBER": "regulation_number",
    "THIRDPARTY": "third_party",
    "YEAR RECEIVED": "year_received",
    "DECISION YEAR": "decision_year",
}

DROPPED = {
    "SUPPLEMENTTYPE": "PMA-only; all null within 510(k)",
    "SUPPLEMENTREASON": "PMA-only; all null within 510(k)",
    "SUPPLEMENT NUMBER": "PMA-only; all null within 510(k)",
    "REVIEWGRANTEDYN": "De Novo-only; all null within 510(k)",
    "DOCKETNUMBER": "De Novo-only; all null within 510(k)",
    "FEDREGNOTICEDATE": "De Novo-only; all null within 510(k)",
    "AI_ML_submission": "constant 1 across the whole file; carries no information",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _recover_regulation_number(value: object) -> str | None:
    """CFR device regulation numbers are <part>.<4-digit section>.

    The source stores them as floats, so 892.2050 arrives as 892.205 with the
    trailing zero lost. Zero-padding the fraction to four digits recovers the
    canonical form exactly; every distinct value in the extract round-trips.
    """
    if pd.isna(value):
        return None
    return f"{float(value):.4f}"


def transform(df: pd.DataFrame) -> pd.DataFrame:
    """Full-file frame -> the V1 510(k) table."""
    missing = set(COLUMN_MAP) - set(df.columns)
    if missing:
        raise ValueError(f"source file is missing expected columns: {sorted(missing)}")

    out = df[df["PATHWAY"] == V1_PATHWAY].copy()
    out["REGULATIONNUMBER"] = out["REGULATIONNUMBER"].map(_recover_regulation_number)
    out = out[list(COLUMN_MAP)].rename(columns=COLUMN_MAP)

    out["date_received"] = pd.to_datetime(out["date_received"], errors="raise").dt.date
    out["decision_date"] = pd.to_datetime(out["decision_date"], errors="raise").dt.date
    out["third_party"] = out["third_party"].map({"Y": True, "N": False})
    out["device_class"] = out["device_class"].astype("string")

    for col in out.select_dtypes(include="object").columns:
        out[col] = out[col].astype("string").str.strip()

    return out.sort_values("regnumber").reset_index(drop=True)


def validate(df: pd.DataFrame) -> None:
    """Fail the load rather than publish a table that violates a V1 assumption."""
    if df.empty:
        raise ValueError("no 510(k) rows found")

    dupes = df["regnumber"][df["regnumber"].duplicated()].tolist()
    if dupes:
        raise ValueError(
            f"regnumber must be unique for 510(k) — one row per clearance; "
            f"got duplicates: {dupes[:5]}"
        )

    if (bad := df["pathway"].ne(V1_PATHWAY).sum()):
        raise ValueError(f"{bad} rows are not {V1_PATHWAY}")

    for col in ("regnumber", "company_name", "applicant_raw", "decision_date", "product_code"):
        if (n := df[col].isna().sum()):
            raise ValueError(f"{col} must never be null; {n} nulls found")

    if (df["decision_date"] > df["decision_date"].max()).any():
        raise ValueError("decision_date exceeds observed maximum")

    reg = df["regulation_number"].dropna()
    if not reg.str.fullmatch(r"\d{3}\.\d{4}").all():
        raise ValueError("regulation_number failed <part>.<4-digit section> recovery")


def load(source: Path, db_path: Path = DB_PATH, *, echo: bool = True) -> dict:
    raw = pd.read_excel(source, sheet_name="Sheet1")
    df = transform(raw)
    validate(df)

    meta = {
        "schema_version": SCHEMA_VERSION,
        "source_file": source.name,
        "source_sha256": _sha256(source),
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_rows": len(raw),
        "loaded_rows": len(df),
        "decision_date_min": str(df["decision_date"].min()),
        "decision_date_max": str(df["decision_date"].max()),
        "distinct_companies": int(df["company_name"].nunique()),
    }

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()

    con = duckdb.connect(str(db_path))
    try:
        con.register("staged", df)
        con.execute("CREATE TABLE fda_510k AS SELECT * FROM staged")
        con.execute("CREATE TABLE ingest_metadata (key TEXT, value TEXT)")
        con.executemany(
            "INSERT INTO ingest_metadata VALUES (?, ?)",
            [(k, str(v)) for k, v in meta.items()],
        )
    finally:
        con.close()

    if echo:
        for k, v in meta.items():
            print(f"  {k}: {v}")
        print(f"  dropped columns: {len(DROPPED)}")
        print(f"\nwrote {db_path}")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source",
        type=Path,
        default=None,
        help="source .xlsx (default: the single file in data/raw/fda/)",
    )
    ap.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args(argv)

    source = args.source
    if source is None:
        candidates = sorted(RAW_FDA_DIR.glob("*.xlsx"))
        if len(candidates) != 1:
            print(
                f"expected exactly one .xlsx in {RAW_FDA_DIR}, found {len(candidates)}; "
                f"pass --source explicitly",
                file=sys.stderr,
            )
            return 2
        source = candidates[0]

    if not source.exists():
        print(f"source not found: {source}", file=sys.stderr)
        return 2

    load(source, args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
