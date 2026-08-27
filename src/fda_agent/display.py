"""Presentation helpers. No Streamlit import, so they stay testable."""
from __future__ import annotations

import pandas as pd


def for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Render DATE columns as dates rather than midnight timestamps.

    DuckDB DATE arrives as datetime64, which renders as "2018-04-20 00:00:00".
    The midnight is an artefact of the dtype, not something in the data, and
    showing it invites the reader to think a time was recorded.

    A column with any non-midnight time is left alone — that would be a real
    timestamp.
    """
    out = df.copy()
    midnight = pd.Timestamp("00:00:00").time()
    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            times = out[col].dropna().dt.time
            if len(times) and (times == midnight).all():
                out[col] = out[col].dt.date
    return out
