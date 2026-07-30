"""Fetch time series from FRED (Federal Reserve Economic Data).

Uses the public fredgraph.csv endpoint, which requires no API key.
"""
from __future__ import annotations

import io
import logging

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def fetch_fred_series(series_id: str, start: str, end: str) -> pd.Series:
    """Return a date-indexed float series for a single FRED series ID."""
    params = {"id": series_id, "cosd": start, "coed": end}
    resp = requests.get(FRED_CSV_URL, params=params, timeout=30)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text))
    df.columns = ["date", series_id]
    df["date"] = pd.to_datetime(df["date"])
    df = df.set_index("date")
    s = pd.to_numeric(df[series_id], errors="coerce").dropna()
    s.name = series_id
    logger.info("FRED %s: %d observations (%s to %s)", series_id, len(s),
                s.index.min(), s.index.max())
    return s


def fetch_fred_series_safe(series_id: str, start: str, end: str) -> pd.Series:
    """Like fetch_fred_series but returns an empty series on failure instead of raising."""
    try:
        return fetch_fred_series(series_id, start, end)
    except Exception:  # noqa: BLE001 - keep pipeline running even if one source fails
        logger.exception("Failed to fetch FRED series %s", series_id)
        return pd.Series(name=series_id, dtype=float)
