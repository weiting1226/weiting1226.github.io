"""Fetch time series from Yahoo Finance via yfinance.

Used for series that FRED does not carry: VIX9D, CBOE SKEW, and daily
closes for Asian equity indices / commodities used as Track-2 sentinels.
"""
from __future__ import annotations

import logging

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


def fetch_yf_series(ticker: str, start: str, end: str, field: str = "Close") -> pd.Series:
    """Return a date-indexed float series of the requested field for a Yahoo ticker."""
    df = yf.download(ticker, start=start, end=end, progress=False, auto_adjust=False)
    if df.empty:
        raise ValueError(f"No data returned for ticker {ticker}")
    if isinstance(df.columns, pd.MultiIndex):
        s = df[field][ticker] if ticker in df[field].columns else df[field].iloc[:, 0]
    else:
        s = df[field]
    s = pd.to_numeric(s, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    s.name = ticker
    logger.info("Yahoo %s: %d observations (%s to %s)", ticker, len(s),
                s.index.min(), s.index.max())
    return s


def fetch_yf_series_safe(ticker: str, start: str, end: str, field: str = "Close") -> pd.Series:
    try:
        return fetch_yf_series(ticker, start, end, field=field)
    except Exception:  # noqa: BLE001 - keep pipeline running even if one source fails
        logger.exception("Failed to fetch Yahoo Finance ticker %s", ticker)
        return pd.Series(name=ticker, dtype=float)
