"""Scrape FINRA monthly margin debt statistics.

FINRA publishes member firms' aggregate margin debt in two places used
here:

1. A live HTML table on FINRA_URL, updated monthly, but only showing a
   rolling window of the most recent ~13 months.
2. A downloadable xlsx archive (FINRA_HISTORICAL_XLSX_URL, discovered by
   scanning FINRA_URL's page links) that appears to hold much more
   history. FINRA seems to update this file in place rather than
   changing its URL, so the "2021-03" segment in the path is stale and
   not indicative of the file's actual data coverage.

fetch_finra_margin_debt_safe() merges both: the xlsx for older months,
the live page for the most recent ones (it's more likely to be current).
Column layouts differ between the two sources and have changed before,
so parsing is best-effort with diagnostic logging; failures are caught
by the caller (build_dataset.py) so the rest of the pipeline keeps
working even if this scraper needs an update.
"""
from __future__ import annotations

import logging
import re
from io import BytesIO, StringIO

import pandas as pd
import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

FINRA_URL = "https://www.finra.org/investors/learn-to-invest/advanced-investing/margin-statistics"
FINRA_HISTORICAL_XLSX_URL = "https://www.finra.org/sites/default/files/2021-03/margin-statistics.xlsx"
_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; market-liquidity-monitor/1.0)"}


def _extract_debit_series(df: pd.DataFrame, source_label: str) -> pd.Series | None:
    """Best-effort extraction of a (date -> debit balance) series from a table
    whose exact column layout is unknown (varies between the live HTML page and
    the downloadable xlsx archive)."""
    cols = {str(c).strip(): c for c in df.columns}
    debit_key = next((orig for norm, orig in cols.items() if "debit" in norm.lower()), None)
    if debit_key is None:
        return None

    date_col = next(
        (orig for norm, orig in cols.items()
         if "month" in norm.lower() and "year" in norm.lower()),
        None,
    )
    if date_col is not None:
        dates = pd.to_datetime(df[date_col].astype(str).str.strip(), format="%b-%y", errors="coerce")
        mask = dates.isna()
        if mask.any():
            dates.loc[mask] = pd.to_datetime(df.loc[mask, date_col], errors="coerce")
    else:
        dt_col = next((c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])), None)
        if dt_col is not None:
            dates = pd.to_datetime(df[dt_col], errors="coerce")
        else:
            year_key = next((orig for norm, orig in cols.items() if "year" in norm.lower()), None)
            month_key = next((orig for norm, orig in cols.items() if "month" in norm.lower()), None)
            if year_key is None or month_key is None:
                return None
            year = pd.to_numeric(df[year_key].astype(str).str.extract(r"(\d{4})")[0], errors="coerce")
            month_raw = df[month_key].astype(str).str.strip()
            month = pd.to_numeric(month_raw, errors="coerce")
            need_name_parse = month.isna()
            if need_name_parse.any():
                parsed = pd.to_datetime(month_raw[need_name_parse], format="%B", errors="coerce")
                parsed = parsed.fillna(pd.to_datetime(month_raw[need_name_parse], format="%b", errors="coerce"))
                month.loc[need_name_parse] = parsed.dt.month
            dates = pd.to_datetime(
                year.astype("Int64").astype(str) + "-" + month.astype("Int64").astype(str) + "-01",
                format="%Y-%m-%d", errors="coerce",
            )

    values = pd.to_numeric(
        df[debit_key].astype(str).str.replace(r"[^0-9.\-]", "", regex=True), errors="coerce"
    )
    valid = dates.notna() & values.notna()
    if not valid.any():
        logger.info("%s: matched a debit column but extracted 0 valid rows", source_label)
        return None

    s = pd.Series(
        values[valid].to_numpy(),
        index=pd.DatetimeIndex(dates[valid]).normalize().map(lambda d: d.replace(day=1)),
    )
    s = s[~s.index.duplicated(keep="last")].sort_index()
    logger.info("%s: extracted %d rows (%s to %s)", source_label, len(s), s.index.min(), s.index.max())
    return s


def fetch_finra_margin_debt_live_page() -> pd.Series | None:
    """Parse the live HTML table (most recent ~13 months, most up to date)."""
    resp = requests.get(FINRA_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    tables = pd.read_html(StringIO(resp.text))
    logger.info("FINRA live page: found %d HTML tables; shapes=%s",
                len(tables), [t.shape for t in tables])

    for t in tables:
        t = t.rename(columns=lambda c: str(c).strip())
        s = _extract_debit_series(t, "FINRA live page table")
        if s is not None:
            return s
    return None


def fetch_finra_margin_debt_historical_xlsx() -> pd.Series | None:
    """Best-effort fetch of FINRA's downloadable margin-statistics archive,
    which (unlike the live HTML page) may cover much more than ~13 months."""
    resp = requests.get(FINRA_HISTORICAL_XLSX_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    sheets = pd.read_excel(BytesIO(resp.content), sheet_name=None, header=None)
    logger.info("FINRA xlsx: sheets=%s", {k: v.shape for k, v in sheets.items()})

    best: pd.Series | None = None
    for name, raw in sheets.items():
        header_row = None
        for i in range(min(10, len(raw))):
            if raw.iloc[i].astype(str).str.contains("debit", case=False, na=False).any():
                header_row = i
                break
        if header_row is None:
            logger.info("FINRA xlsx sheet %r: no header row with 'debit' found in first 10 rows", name)
            continue
        df = raw.iloc[header_row + 1:].copy()
        df.columns = raw.iloc[header_row]
        logger.info("FINRA xlsx sheet %r: header_row=%d columns=%s", name, header_row, list(df.columns))
        s = _extract_debit_series(df, f"FINRA xlsx sheet {name!r}")
        if s is not None and (best is None or len(s) > len(best)):
            best = s
    return best


def fetch_finra_margin_debt_safe() -> pd.Series:
    """Merge the xlsx archive (older history) with the live page (most recent,
    most reliably up to date), falling back gracefully if either source fails
    or FINRA changes its page/file structure again."""
    historical = None
    try:
        historical = fetch_finra_margin_debt_historical_xlsx()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch/parse FINRA historical xlsx archive")

    live = None
    try:
        live = fetch_finra_margin_debt_live_page()
    except Exception:  # noqa: BLE001
        logger.exception("Failed to fetch/parse FINRA live page table")

    parts = [s for s in (historical, live) if s is not None]
    if not parts:
        return pd.Series(name="margin_debt_usd_millions", dtype=float)

    # Live page wins on overlapping months (more likely to be current/corrected).
    combined = pd.concat(parts)
    combined = combined[~combined.index.duplicated(keep="last")].sort_index()
    combined.name = "margin_debt_usd_millions"
    logger.info("FINRA margin debt (merged): %d monthly observations (%s to %s)",
                len(combined), combined.index.min(), combined.index.max())
    return combined
