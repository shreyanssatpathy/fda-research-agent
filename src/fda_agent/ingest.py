"""Load the FDA AI/ML device extract into DuckDB as the V1 `fda_510k` table.

Deterministic and idempotent: same input file gives the same database. The raw
spreadsheet is never modified. See docs/data-profile-v1.md for why each
transform below exists.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from fda_agent.config import (
    DB_PATH,
    PB_RAW_DIR,
    KNOWN_UNRESOLVED_APPLICANTS,
    RAW_FDA_DIR,
    SCHEMA_VERSION,
    SOURCE_PRECEDENCE,
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


def resolve_company_names(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """Make `company_name` a function of `applicant_raw`.

    The supplied mapping was built by unioning several passes without a precedence
    rule, so 16 applicant strings carry more than one clean_name and company-level
    aggregates split. Owner ruling 2026-08-27: the highest-precedence source in
    SOURCE_PRECEDENCE wins.

    Where the top-precedence source disagrees with itself the rule cannot decide,
    and those rows are left untouched rather than resolved arbitrarily. Returns the
    frame and the sorted list of applicants still unresolved.
    """
    rank = {src: i for i, src in enumerate(SOURCE_PRECEDENCE)}
    unknown = set(df["company_name_source"].dropna()) - set(rank)
    if unknown:
        raise ValueError(
            f"company_name_source values missing from SOURCE_PRECEDENCE: {sorted(unknown)}"
        )

    ambiguous = df.groupby("applicant_raw")["company_name"].nunique()
    ambiguous = set(ambiguous[ambiguous > 1].index)

    df = df.copy()
    unresolved: list[str] = []

    for applicant in sorted(ambiguous):
        rows = df[df["applicant_raw"] == applicant]
        best = min(rows["company_name_source"], key=lambda s: rank[s])
        winners = rows.loc[rows["company_name_source"] == best, "company_name"].unique()
        if len(winners) != 1:
            # The authoritative pass contradicts itself; no defensible winner.
            unresolved.append(applicant)
            continue
        df.loc[df["applicant_raw"] == applicant, "company_name"] = winners[0]
        df.loc[df["applicant_raw"] == applicant, "company_name_source"] = best

    return df, unresolved


# PitchBook company IDs that roll several *distinct legal filers* up to a parent.
# Excluded from the merge: GE Healthcare and its subsidiaries file separately, as
# do Fujifilm and Fujifilm Healthcare, and collapsing them is a corporate-hierarchy
# decision rather than a name-variant fix. Pending owner ruling — see
# docs/open-questions/company-mapping.md.
ROLLUP_COMPANY_IDS = frozenset({"18862-03", "11951-47"})


def _pick_canonical(names: pd.Series, pb_name: str | None) -> str:
    """Choose the surviving spelling for a merged company.

    Frequency first — the spelling most filings use is the one people recognise.
    Ties are common though (two variants, one clearance each), and breaking them
    by whichever pandas saw first produced bad winners: `Corvista Health` over
    `CorVista Health`, and the stale `Bay Labs` over `Caption Health`.

    So ties fall back to PitchBook's current company name, which is independent
    evidence of what the company calls itself now, and finally to alphabetical
    order so the result is deterministic.
    """
    counts = names.value_counts()
    top = counts.max()
    candidates = sorted(counts[counts == top].index)
    if len(candidates) == 1:
        return candidates[0]

    if pb_name:
        norm = lambda s: "".join(ch for ch in str(s).lower() if ch.isalnum())
        target = norm(pb_name)
        exact = [c for c in candidates if norm(c) == target]
        if exact:
            return exact[0]
        scored = sorted(
            candidates,
            key=lambda c: (-len(os.path.commonprefix([norm(c), target])), c),
        )
        return scored[0]
    return candidates[0]


def resolve_company_identity(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """Merge FDA company names that the PitchBook bridge proves are one company.

    The precedence rule fixed applicants that mapped to several names. It could not
    fix the reverse: several *different* applicant strings that are in fact the same
    company — `Ischema View` and `Ischemaview`, `Bay Labs` and `Caption Health`.
    Nothing inside the FDA data distinguishes those from two genuinely different
    firms.

    PitchBook company IDs are independent evidence of entity identity, so where the
    bridge assigns one ID to several FDA names, they are one company.

    The surviving name is the **most frequent FDA spelling**, not PitchBook's
    current name: users type what appears in FDA filings, and contract rule 10
    matches against this column. Renaming `Ischema View` to `RapidAI` would merge
    the rows correctly and then make them unfindable.

    Returns the frame plus a record of every merge performed.
    """
    bridge_path = PB_RAW_DIR / "fda-pb-mapping.xlsx"
    company_path = PB_RAW_DIR / "pitchbook_company_level.xlsx"
    if not bridge_path.exists():
        df["pb_company_id"] = pd.Series([pd.NA] * len(df), dtype="string")
        return df, []

    bridge = pd.read_excel(bridge_path, sheet_name="Sheet1")
    bridge.columns = ["regnumber", "pb_company_id"]
    bridge = bridge.dropna(subset=["pb_company_id"]).drop_duplicates("regnumber")
    bridge["regnumber"] = bridge["regnumber"].astype("string").str.strip()
    bridge["pb_company_id"] = bridge["pb_company_id"].astype("string").str.strip()

    df = df.merge(bridge, on="regnumber", how="left")

    pb_names: dict[str, str] = {}
    if company_path.exists():
        comp = pd.read_excel(company_path, sheet_name="Sheet1")
        comp = comp.dropna(subset=["Company ID"]).drop_duplicates("Company ID")
        pb_names = dict(
            zip(comp["Company ID"].astype(str).str.strip(), comp["Companies"].astype(str))
        )

    merges: list[dict] = []
    grouped = df.dropna(subset=["pb_company_id"]).groupby("pb_company_id")
    for pb_id, rows in grouped:
        names = rows["company_name"].unique()
        if len(names) < 2 or pb_id in ROLLUP_COMPANY_IDS:
            continue
        winner = _pick_canonical(rows["company_name"], pb_names.get(pb_id))
        merges.append(
            {
                "pb_company_id": pb_id,
                "canonical": winner,
                "merged": sorted(n for n in names if n != winner),
                "rows": int(len(rows)),
            }
        )
        df.loc[df["pb_company_id"] == pb_id, "company_name"] = winner

    return df, merges


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

    out = out.sort_values("regnumber").reset_index(drop=True)
    out, _ = resolve_company_names(out)
    out, merges = resolve_company_identity(out)
    out.attrs["identity_merges"] = merges

    # Evaluate the guard AFTER both passes. Precedence cannot settle every case on
    # its own — ITERATIVE SCOPES and SOFTWARE NEMOTEC are settled by the bridge in
    # the step above — so checking earlier would fail on ambiguities that the very
    # next line resolves.
    still_split = out.groupby("applicant_raw")["company_name"].nunique()
    unresolved = sorted(still_split[still_split > 1].index)

    unexpected = set(unresolved) - KNOWN_UNRESOLVED_APPLICANTS
    if unexpected:
        raise ValueError(
            "company-name ambiguity that neither precedence nor the PitchBook "
            f"bridge can settle: {sorted(unexpected)} — rule on it in "
            "docs/open-questions/company-mapping.md"
        )
    out.attrs["unresolved_applicants"] = unresolved
    return out


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
        "identity_merges": len(df.attrs.get("identity_merges", [])),
        "bridged_clearances": int(df["pb_company_id"].notna().sum()),
        "unresolved_applicants": ";".join(df.attrs.get("unresolved_applicants", [])) or "none",
    }

    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Replace this loader's own tables only. Deleting the database file would take
    # the PitchBook tables with it, and because their tests skip when the tables
    # are absent, the loss would show up as skipped tests rather than failures.
    con = duckdb.connect(str(db_path))
    try:
        con.execute("DROP TABLE IF EXISTS fda_510k")
        con.execute(
            "CREATE TABLE IF NOT EXISTS ingest_metadata (key TEXT, value TEXT)"
        )
        con.execute("DELETE FROM ingest_metadata WHERE key NOT LIKE 'pb_%'")
        con.register("staged", df)
        # Cast explicitly. A bare CREATE AS SELECT infers VARCHAR for the date
        # columns, which silently breaks every date function in generated SQL.
        cols = ", ".join(
            f"CAST({c} AS DATE) AS {c}" if c in ("date_received", "decision_date") else c
            for c in df.columns
        )
        con.execute(f"CREATE TABLE fda_510k AS SELECT {cols} FROM staged")
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
