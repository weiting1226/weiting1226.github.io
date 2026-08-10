"""Scrape FINRA monthly margin debt statistics.

FINRA publishes member firms' aggregate margin debt as an HTML table,
updated monthly with roughly a one-month lag. There is no CSV/API
endpoint, so this parses the published table with pandas.read_html.

The live page (as of 2026-08) shows a single combined "Month/Year"
column formatted like "Jun-26" (abbreviated month + 2-digit year)
rather than separate year/month columns, and only exposes a rolling
window of the most recent ~13 months -- FINRA does not appear to
publish the full historical series on this page for free. This scraper
handles both the combined-column format and a separate year/month
column format (in case the page reverts), but the output series will
only cover whatever window the page currently shows.

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
from bs4 import BeautifulSoup

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

    soup = BeautifulSoup(resp.text, "html.parser")
    candidate_links = [
        a["href"] for a in soup.find_all("a", href=True)
        if re.search(r"(xls|xlsx|csv|histor|archive|download|full)", a["href"], re.I)
        or re.search(r"(xls|xlsx|csv|histor|archive|download|full)", a.get_text(" ", strip=True), re.I)
    ]
    logger.info("FINRA page: %d candidate historical-data links: %s",
                len(candidate_links), candidate_links)

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
    debit_col = next(c for c in target.columns if "debit" in c.lower())

    # The page currently exposes one combined "Month/Year" column (e.g. "Jun-26")
    # rather than separate year/month columns. Detect and handle both shapes.
    combined_col = next(
        (c for c in target.columns if "month" in c.lower() and "year" in c.lower()),
        None,
    )

    rows = []
    if combined_col is not None:
        for _, row in target.iterrows():
            raw = str(row[combined_col]).strip()
            ts = pd.to_datetime(raw, format="%b-%y", errors="coerce")
            if pd.isna(ts):
                ts = pd.to_datetime(raw, errors="coerce")
            if pd.isna(ts):
                continue
            try:
                value = float(re.sub(r"[^0-9.\-]", "", str(row[debit_col])))
            except ValueError:
                continue
            rows.append((pd.Timestamp(year=ts.year, month=ts.month, day=1), value))
    else:
        year_col = next(c for c in target.columns if "year" in c.lower())
        month_col = next(c for c in target.columns if "month" in c.lower())

        def _to_month_num(m):
            m = str(m).strip()
            if m.isdigit():
                return int(m)
            for fmt in ("%B", "%b"):
                parsed = pd.to_datetime(m, format=fmt, errors="coerce")
                if not pd.isna(parsed):
                    return parsed.month
            return None

        for _, row in target.iterrows():
            try:
                year = int(re.sub(r"[^0-9]", "", str(row[year_col])))
                month = _to_month_num(row[month_col])
                if month is None:
                    continue
                value = float(re.sub(r"[^0-9.\-]", "", str(row[debit_col])))
                rows.append((pd.Timestamp(year=year, month=int(month), day=1), value))
            except (ValueError, TypeError):
                continue

    if not rows:
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
