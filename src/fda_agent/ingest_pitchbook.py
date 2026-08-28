"""Load the PitchBook extracts into DuckDB as `pb_companies`, `pb_deals`, `fda_pb_bridge`.

Same discipline as the FDA loader: deterministic, idempotent, and it refuses to
publish a table that violates a stated assumption rather than emitting a subtly
wrong one.

Cleaning rules on deals are the owner's, recorded 2026-08-27:

1. drop rows with no `Deal Size`
2. keep only `Deal Status == 'Completed'`
3. keep only a qualified `Universe` — one containing 'Pre-venture' or 'Venture Capital'
4. keep only companies whose `Company Financing Status` is in QUALIFIED_FINANCING_STATUS

Rule 4 filters *deals*; `pb_companies` keeps every company and carries an
`in_qualified_universe` flag instead. That distinction matters: it lets the system
say "GE Healthcare is a public corporation, outside the venture universe" rather
than returning nothing and letting the reader infer "no funding".
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from fda_agent.config import DB_PATH, PB_RAW_DIR, SCHEMA_VERSION

QUALIFIED_FINANCING_STATUS = (
    "Accelerator/Incubator Backed",
    "Angel-Backed",
    "Formerly Accelerator/Incubator backed",
    "Formerly Angel backed",
    "Formerly PE-Backed",
    "Formerly VC-backed",
    "Private Equity-Backed",
    "Venture Capital-Backed",
)

# A Universe cell is a comma-joined set of labels; qualify on either token.
QUALIFYING_UNIVERSE_TOKENS = ("Pre-venture", "Venture Capital")

# Deal types that count as capital raised BY the company. Owner ruling 2026-08-27.
#
# Marked as a column rather than a filter. The excluded rows are not junk — an
# acquisition or IPO is exactly what a research brief's corporate-history section
# needs — they simply are not fundraising. Deleting them would cost Phase 3 real
# evidence to save a WHERE clause.
#
# The line is whether money reaches the company. Share Repurchase, Dividend and
# Leveraged Recapitalization, Secondary Transaction, Buyout/LBO and
# Merger/Acquisition move money out of the company or between shareholders;
# counting them as "raised" is the wrong sign, not an approximation.
VENTURE_DEAL_TYPES = (
    "Seed Round",
    "Angel (individual)",
    "Early Stage VC",
    "Later Stage VC",
    "Accelerator/Incubator",
    "PE Growth/Expansion",
)

DEAL_COLUMNS = {
    "Deal ID": "deal_id",
    "Company ID": "company_id",
    "Companies": "company_name_pb",
    "Deal Date": "deal_date",
    "Deal Size": "deal_size_usd_m",
    "Deal Size Status": "deal_size_status",
    "Deal Type": "deal_type",
    "Deal Class": "deal_class",
    "Deal Status": "deal_status",
    "Universe": "universe",
    "Native Currency of Deal": "native_currency",
    "Pre-money Valuation": "pre_money_valuation_usd_m",
    "Post Valuation": "post_valuation_usd_m",
}

COMPANY_COLUMNS = {
    "Company ID": "company_id",
    "Companies": "company_name_pb",
    "Company Legal Name": "company_legal_name",
    "Company Former Name": "company_former_name",
    "Company Financing Status": "financing_status",
    "Business Status": "business_status",
    "Ownership Status": "ownership_status",
    "Year Founded": "year_founded",
    "Total Raised": "total_raised_usd_m",
    "HQ Country/Territory/Region": "hq_country",
    "HQ City": "hq_city",
    "Website": "website",
    "Universe": "universe",
}


def _strip_text_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Trim text columns without tripping the pandas 3 object/str dtype change."""
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            df[col] = df[col].astype("string").str.strip()
    return df


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_qualified_universe(value: object) -> bool:
    """Null Universe is not qualified — absence is not membership."""
    if pd.isna(value):
        return False
    return any(tok in str(value) for tok in QUALIFYING_UNIVERSE_TOKENS)


def clean_deals(raw: pd.DataFrame, companies: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Apply the four owner rules, recording what each one removed.

    The funnel is returned rather than logged away: a filter that silently drops
    40% of the rows is a fact about the dataset, not an implementation detail.
    """
    funnel = {"raw": len(raw)}

    d = raw[raw["Deal Size"].notna()]
    funnel["after_drop_missing_size"] = len(d)

    d = d[d["Deal Status"] == "Completed"]
    funnel["after_completed_only"] = len(d)

    d = d[d["Universe"].map(is_qualified_universe)]
    funnel["after_qualified_universe"] = len(d)

    qualified_ids = set(
        companies.loc[
            companies["financing_status"].isin(QUALIFIED_FINANCING_STATUS), "company_id"
        ].dropna()
    )
    d = d[d["Company ID"].isin(qualified_ids)]
    funnel["after_qualified_financing_status"] = len(d)

    out = d[list(DEAL_COLUMNS)].rename(columns=DEAL_COLUMNS)
    out["deal_date"] = pd.to_datetime(out["deal_date"], errors="coerce").dt.date
    out["is_venture_round"] = out["deal_type"].isin(VENTURE_DEAL_TYPES)
    funnel["venture_rounds"] = int(out["is_venture_round"].sum())
    out = _strip_text_columns(out)
    return out.sort_values("deal_id").reset_index(drop=True), funnel


def clean_companies(raw: pd.DataFrame) -> pd.DataFrame:
    """One row per company.

    The export is denormalised to FDA clearance grain — 1,483 rows for 475
    companies — so every company-level aggregate would otherwise be weighted by
    how many devices the company cleared.
    """
    out = raw[list(COMPANY_COLUMNS)].rename(columns=COMPANY_COLUMNS)
    out = out.dropna(subset=["company_id"]).drop_duplicates(subset=["company_id"])
    out["in_qualified_universe"] = out["financing_status"].isin(
        QUALIFIED_FINANCING_STATUS
    )
    out = _strip_text_columns(out)
    return out.sort_values("company_id").reset_index(drop=True)


def clean_bridge(raw: pd.DataFrame) -> pd.DataFrame:
    out = raw.rename(columns={"REGNUMBER": "regnumber", "Company ID": "company_id"})
    out = out.dropna(subset=["company_id"]).drop_duplicates()
    out = _strip_text_columns(out)
    return out.sort_values("regnumber").reset_index(drop=True)


def validate(deals: pd.DataFrame, companies: pd.DataFrame, bridge: pd.DataFrame) -> None:
    if deals.empty:
        raise ValueError("no deals survived cleaning")
    if not deals["deal_id"].is_unique:
        raise ValueError("deal_id must be unique — one row per deal, or sums fan out")
    if deals["deal_size_usd_m"].isna().any():
        raise ValueError("deal_size_usd_m must be non-null after rule 1")
    if set(deals["deal_status"].unique()) != {"Completed"}:
        raise ValueError("deal_status must be Completed after rule 2")
    if not deals["universe"].map(is_qualified_universe).all():
        raise ValueError("every deal must have a qualified universe after rule 3")

    flagged = set(deals.loc[deals["is_venture_round"], "deal_type"].unique())
    if not flagged <= set(VENTURE_DEAL_TYPES):
        raise ValueError(f"is_venture_round set on non-venture types: {flagged}")

    if not companies["company_id"].is_unique:
        raise ValueError("company_id must be unique — the export is denormalised")

    dupes = bridge.groupby("regnumber")["company_id"].nunique()
    if (bad := dupes[dupes > 1]).any():
        raise ValueError(
            f"bridge is not a function: {len(bad)} regnumbers map to several "
            f"company_ids, e.g. {list(bad.index[:3])}"
        )

    orphans = set(deals["company_id"]) - set(companies["company_id"])
    if orphans:
        raise ValueError(f"{len(orphans)} deals reference unknown companies")


def load(
    deals_path: Path,
    companies_path: Path,
    bridge_path: Path,
    db_path: Path = DB_PATH,
    *,
    echo: bool = True,
) -> dict:
    raw_deals = pd.read_excel(deals_path, sheet_name="Data").dropna(how="all")
    raw_companies = pd.read_excel(companies_path, sheet_name="Sheet1")
    raw_bridge = pd.read_excel(bridge_path, sheet_name="Sheet1")

    companies = clean_companies(raw_companies)
    deals, funnel = clean_deals(raw_deals, companies)
    bridge = clean_bridge(raw_bridge)
    validate(deals, companies, bridge)

    meta = {
        "schema_version": SCHEMA_VERSION,
        "ingested_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "deals_file": deals_path.name,
        "deals_sha256": _sha256(deals_path),
        "companies_file": companies_path.name,
        "bridge_file": bridge_path.name,
        "deals_loaded": len(deals),
        "deals_excluded": funnel["raw"] - len(deals),
        "companies_loaded": len(companies),
        "companies_in_qualified_universe": int(companies["in_qualified_universe"].sum()),
        "venture_rounds": int(deals["is_venture_round"].sum()),
        "venture_capital_usd_m": round(
            float(deals.loc[deals["is_venture_round"], "deal_size_usd_m"].sum()), 1
        ),
        "bridge_rows": len(bridge),
        "deal_date_min": str(deals["deal_date"].min()),
        "deal_date_max": str(deals["deal_date"].max()),
        **{f"funnel_{k}": v for k, v in funnel.items()},
    }

    con = duckdb.connect(str(db_path))
    try:
        for name, df in (
            ("pb_deals", deals),
            ("pb_companies", companies),
            ("fda_pb_bridge", bridge),
        ):
            con.register("staged", df)
            con.execute(f"DROP TABLE IF EXISTS {name}")
            cols = ", ".join(
                f"CAST({c} AS DATE) AS {c}" if c.endswith("_date") else c
                for c in df.columns
            )
            con.execute(f"CREATE TABLE {name} AS SELECT {cols} FROM staged")
            con.unregister("staged")
        con.execute("DELETE FROM ingest_metadata WHERE key LIKE 'pb_%'")
        con.executemany(
            "INSERT INTO ingest_metadata VALUES (?, ?)",
            [(f"pb_{k}", str(v)) for k, v in meta.items()],
        )
    finally:
        con.close()

    if echo:
        print("cleaning funnel:")
        for k, v in funnel.items():
            print(f"  {k:36} {v:>6}")
        print()
        for k, v in meta.items():
            if not k.startswith("funnel_"):
                print(f"  {k}: {v}")
    return meta


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--deals", type=Path, default=PB_RAW_DIR / "pb_deals_latest.xlsx")
    ap.add_argument(
        "--companies", type=Path, default=PB_RAW_DIR / "pitchbook_company_level.xlsx"
    )
    ap.add_argument("--bridge", type=Path, default=PB_RAW_DIR / "fda-pb-mapping.xlsx")
    ap.add_argument("--db", type=Path, default=DB_PATH)
    args = ap.parse_args(argv)

    for p in (args.deals, args.companies, args.bridge):
        if not p.exists():
            print(f"missing input: {p}", file=sys.stderr)
            return 2
    load(args.deals, args.companies, args.bridge, args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
