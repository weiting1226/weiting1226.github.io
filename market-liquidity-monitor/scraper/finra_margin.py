"""Scrape FINRA monthly margin debt statistics.

FINRA publishes member firms' aggregate margin debt as an HTML table,
updated monthly with roughly a one-month lag. There is no CSV/API
endpoint, so this parses the published table with pandas.read_html.

This page's markup has changed before and may change again; failures
here are caught by the caller (build_dataset.py) so the rest of the
pipeline keeps working even if this scraper needs an update.
"""
from __future__ import annotations

import logging
import re
from io import StringIO

import pandas as pd
import requests

logger = logging.getLogger(__name__)

FINRA_URL = "https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; market-liquidity-monitor/1.0)"}


def fetch_finra_margin_debt() -> pd.Series:
    """Return a monthly, date-indexed series of total margin debt (USD millions)."""
    resp = requests.get(FINRA_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))

    logger.info("FINRA page: found %d HTML tables; shapes=%s",
                len(tables), [t.shape for t in tables])

    target = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any("debit" in c for c in cols) and (
            any("year" in c for c in cols) or any("month" in c for c in cols)
        ):
            target = t
            break
    if target is None:
        for i, t in enumerate(tables):
            logger.error("FINRA table[%d] columns: %s", i, list(t.columns))
        raise ValueError("Could not locate the margin debt table on the FINRA page")

    target = target.rename(columns=lambda c: str(c).strip())
    logger.info("FINRA matched table columns: %s", list(target.columns))
    logger.info("FINRA matched table head:\n%s", target.head(5).to_string())
    year_col = next(c for c in target.columns if "year" in c.lower())
    month_col = next(c for c in target.columns if "month" in c.lower())
    debit_col = next(c for c in target.columns if "debit" in c.lower())

    def _to_month_num(m):
        m = str(m).strip()
        if m.isdigit():
            return int(m)
        return pd.to_datetime(m, format="%B", errors="coerce").month or \
            pd.to_datetime(m, format="%b", errors="coerce").month

    rows = []
    for _, row in target.iterrows():
        try:
            year = int(re.sub(r"[^0-9]", "", str(row[year_col])))
            month = _to_month_num(row[month_col])
            value = float(re.sub(r"[^0-9.\-]", "", str(row[debit_col])))
            rows.append((pd.Timestamp(year=year, month=int(month), day=1), value))
        except (ValueError, TypeError):
            continue

    if not rows:
        logger.error("FINRA row-parse failure. year_col=%r month_col=%r debit_col=%r; "
                      "raw values: year=%s month=%s debit=%s",
                      year_col, month_col, debit_col,
                      target[year_col].tolist()[:5], target[month_col].tolist()[:5],
                      target[debit_col].tolist()[:5])
        raise ValueError("Parsed FINRA table but found no valid rows")

    s = pd.Series({d: v for d, v in rows}).sort_index()
    s.name = "margin_debt_usd_millions"
    logger.info("FINRA margin debt: %d monthly observations (%s to %s)",
                len(s), s.index.min(), s.index.max())
    return s


def fetch_finra_margin_debt_safe() -> pd.Series:
    try:
        return fetch_finra_margin_debt()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch/parse FINRA margin statistics")
        return pd.Series(name="margin_debt_usd_millions", dtype=float)
