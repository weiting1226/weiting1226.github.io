#!/usr/bin/env python3
"""Build the market-liquidity-monitor dataset.

Downloads ~10 years of history for every quantifiable indicator listed in
the "市場流動性雙軌監控系統" prompt template, aligns them onto the NYSE
trading-day calendar, forward-fills lower-frequency series so every
trading day has a value, and writes:

  data/raw/<series>.csv                  -- each raw fetched series, native frequency
  data/market_liquidity_indicators.csv   -- merged, trading-day-complete dataset

See data/UNAVAILABLE_INDICATORS.md for indicators from the template that
have no free, automatable data source (bid-ask spread, ETF fund flows,
market breadth, etc.).

Run:
    python -m scraper.build_dataset --years 10
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd
import pandas_market_calendars as mcal

from scraper.fred_client import fetch_fred_series_safe
from scraper.yfinance_client import fetch_yf_series_safe
from scraper.finra_margin import fetch_finra_margin_debt_safe

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"


def _yoy_pct(s: pd.Series, periods: int) -> pd.Series:
    if s.empty:
        return s
    return s.pct_change(periods) * 100.0


def _save_raw(series: pd.Series, name: str) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    if series.empty:
        logger.warning("Raw series %s is empty, skipping raw save", name)
        return
    series.to_frame(name=name).to_csv(RAW_DIR / f"{name}.csv", index_label="date")


def build(years: int = 10) -> pd.DataFrame:
    end = pd.Timestamp.today().normalize()
    # Fetch extra lookback buffer so YoY/rolling calcs have data at the display window's start.
    fetch_start = end - pd.DateOffset(years=years, days=400)
    display_start = end - pd.DateOffset(years=years)
    fetch_start_str, end_str = fetch_start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")

    logger.info("Fetching raw series from %s to %s (display window starts %s)",
                fetch_start_str, end_str, display_start.date())

    # ---- ① 總體貨幣流動性 ----
    walcl = fetch_fred_series_safe("WALCL", fetch_start_str, end_str)          # Fed total assets, weekly, $millions
    rrp = fetch_fred_series_safe("RRPONTSYD", fetch_start_str, end_str)        # ON RRP, daily, $billions
    m2 = fetch_fred_series_safe("M2SL", fetch_start_str, end_str)              # M2, monthly, $billions SA

    # ---- ② 資金成本與信用 ----
    hy_oas = fetch_fred_series_safe("BAMLH0A0HYM2", fetch_start_str, end_str)  # HY OAS, daily, % (FRED/ICE licensing limits public history to ~3yr)
    baa10y = fetch_fred_series_safe("BAA10Y", fetch_start_str, end_str)        # Moody's Baa - 10Y UST spread, daily, % (full free history; proxy for periods hy_oas lacks)
    t10y2y = fetch_fred_series_safe("T10Y2Y", fetch_start_str, end_str)        # 10y-2y, daily, percentage points
    sofr = fetch_fred_series_safe("SOFR", fetch_start_str, end_str)            # SOFR, daily, %
    ioer = fetch_fred_series_safe("IOER", fetch_start_str, end_str)            # discontinued 2021-07-28
    iorb = fetch_fred_series_safe("IORB", fetch_start_str, end_str)            # successor to IOER
    ioer_iorb = pd.concat([ioer, iorb[~iorb.index.isin(ioer.index)]]).sort_index()
    ioer_iorb.name = "ioer_iorb_combined"

    # ---- ④ 風險偏好情緒 ----
    vix = fetch_yf_series_safe("^VIX", fetch_start_str, end_str)
    vix9d = fetch_yf_series_safe("^VIX9D", fetch_start_str, end_str)
    skew = fetch_yf_series_safe("^SKEW", fetch_start_str, end_str)
    margin_debt = fetch_finra_margin_debt_safe()                              # monthly, best-effort HTML scrape

    # ---- ⑤ 跨資產資金流向 ----
    dxy_fred = fetch_fred_series_safe("DTWEXBGS", fetch_start_str, end_str)    # Fed broad USD index, daily
    dxy_yahoo = fetch_yf_series_safe("DX-Y.NYB", fetch_start_str, end_str)     # ICE DXY, daily, cross-check/backup

    # ---- 軌道二哨兵訊號代理 (EOD 收盤近似值，非盤中真實值，見 README 限制說明) ----
    dgs10 = fetch_fred_series_safe("DGS10", fetch_start_str, end_str)          # 10y yield, daily, %
    sp500 = fetch_yf_series_safe("^GSPC", fetch_start_str, end_str)
    nikkei = fetch_yf_series_safe("^N225", fetch_start_str, end_str)
    kospi = fetch_yf_series_safe("^KS11", fetch_start_str, end_str)
    sse = fetch_yf_series_safe("000001.SS", fetch_start_str, end_str)
    wti = fetch_yf_series_safe("CL=F", fetch_start_str, end_str)
    gold = fetch_yf_series_safe("GC=F", fetch_start_str, end_str)

    for s, name in [
        (walcl, "walcl_fed_total_assets"), (rrp, "on_rrp_balance"), (m2, "m2sl"),
        (hy_oas, "hy_oas"), (baa10y, "baa10y"), (t10y2y, "t10y2y"), (sofr, "sofr"), (ioer_iorb, "ioer_iorb"),
        (vix, "vix"), (vix9d, "vix9d"), (skew, "skew"), (margin_debt, "margin_debt"),
        (dxy_fred, "dxy_fred_broad"), (dxy_yahoo, "dxy_yahoo"),
        (dgs10, "dgs10"), (sp500, "sp500"), (nikkei, "nikkei225"), (kospi, "kospi"),
        (sse, "sse_composite"), (wti, "wti_crude"), (gold, "gold"),
    ]:
        _save_raw(s, name)

    # ---------------- derived indicators (computed on native frequency, then reindexed) ----------------
    derived: dict[str, pd.Series] = {}

    derived["fed_bs_yoy_pct"] = _yoy_pct(walcl, periods=52)         # weekly series -> 52 obs/year
    derived["on_rrp_balance_usd_bn"] = rrp
    derived["m2_yoy_pct"] = _yoy_pct(m2, periods=12)                # monthly series -> 12 obs/year

    derived["hy_oas_pct"] = hy_oas
    derived["baa10y_credit_spread_proxy_pct"] = baa10y
    derived["t10y2y_bp"] = t10y2y * 100.0

    sofr_a, ioer_a = sofr.align(ioer_iorb, join="inner")
    derived["sofr_ioer_spread_bp"] = (sofr_a - ioer_a) * 100.0

    derived["vix"] = vix
    derived["vix9d"] = vix9d
    vix9d_a, vix_a = vix9d.align(vix, join="inner")
    derived["vix_term_structure_9d_minus_vix"] = vix9d_a - vix_a
    derived["vix9d_gt_vix_flag"] = (vix9d_a > vix_a).astype(int)
    derived["skew_index"] = skew

    derived["margin_debt_usd_millions"] = margin_debt
    derived["margin_debt_yoy_pct"] = _yoy_pct(margin_debt, periods=12)

    dxy_primary = dxy_fred if not dxy_fred.empty else dxy_yahoo
    derived["dxy_broad_index"] = dxy_primary
    derived["dxy_monthly_change_pct"] = dxy_primary.pct_change(21) * 100.0 if not dxy_primary.empty else dxy_primary

    derived["dgs10_1d_change_bp"] = dgs10.diff() * 100.0 if not dgs10.empty else dgs10
    derived["sp500_daily_pct_chg"] = sp500.pct_change() * 100.0 if not sp500.empty else sp500
    derived["nikkei225_daily_pct_chg"] = nikkei.pct_change() * 100.0 if not nikkei.empty else nikkei
    derived["kospi_daily_pct_chg"] = kospi.pct_change() * 100.0 if not kospi.empty else kospi
    derived["sse_composite_daily_pct_chg"] = sse.pct_change() * 100.0 if not sse.empty else sse
    derived["wti_crude_daily_pct_chg"] = wti.pct_change() * 100.0 if not wti.empty else wti
    derived["gold_daily_pct_chg"] = gold.pct_change() * 100.0 if not gold.empty else gold

    # ---------------- assemble onto NYSE trading-day calendar ----------------
    nyse = mcal.get_calendar("NYSE")
    schedule = nyse.schedule(start_date=display_start.strftime("%Y-%m-%d"), end_date=end_str)
    trading_days = pd.DatetimeIndex(schedule.index.date).normalize()

    out = pd.DataFrame(index=trading_days)
    out.index.name = "date"
    for col, s in derived.items():
        if s is None or len(s) == 0:
            out[col] = pd.NA
            continue
        s = s.copy()
        s.index = pd.DatetimeIndex(s.index).normalize()
        s = s[~s.index.duplicated(keep="last")].sort_index()
        out[col] = s.reindex(out.index, method="ffill")

    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=10, help="Years of history to fetch")
    args = parser.parse_args()

    df = build(years=args.years)
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = DATA_DIR / "market_liquidity_indicators.csv"
    df.to_csv(out_path)
    logger.info("Wrote %s (%d rows, %d columns)", out_path, len(df), len(df.columns))


if __name__ == "__main__":
    main()
