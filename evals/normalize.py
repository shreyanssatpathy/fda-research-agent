"""Shared cell normalization for the eval set.

Used by both the builder that froze the expected answers and the scorer that
compares against them. They must agree exactly — if the scorer normalizes
differently from the builder, correct SQL scores as wrong, which is worse than
no scoring at all.

Normalizes representation only, never value: a date renders as a date rather than
a midnight timestamp, and a whole number as an integer rather than 11.0.
"""
from __future__ import annotations


def normalize_cell(v) -> object:
    if v is None or (isinstance(v, float) and v != v):  # NaN
        return None
    if hasattr(v, "item"):  # numpy scalar
        v = v.item()
    if hasattr(v, "date") and not isinstance(v, str):
        return str(v.date()) if hasattr(v, "hour") else str(v)
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v if isinstance(v, (int, float, bool)) else str(v)


def normalize_row(row: dict) -> tuple:
    """Row values as an ordered tuple, ignoring column names.

    Column naming is style (`n` vs `total`); values and ordering are not.
    """
    return tuple("" if (c := normalize_cell(v)) is None else str(c) for v in row.values())
