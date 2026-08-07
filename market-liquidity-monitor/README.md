# 市場流動性雙軌監控系統 — 指標爬蟲

根據 `市場流動性雙軌監控系統` 提示詞模板中列出的所有指標，自動下載過去十年的歷史資料，
並補齊每個 NYSE 交易日的數值（低頻資料以前值往前填補 / forward-fill），輸出成單一 CSV
供後續分析（例如餵給模板中的評分公式）使用。

## 資料來源

全部使用免費、無需付費金鑰的公開來源：

| 類別 | 指標 | 來源 | 原始頻率 |
|---|---|---|---|
| ① 總體貨幣流動性 | Fed資產負債表年增率 | FRED `WALCL` | 週 |
| | ON RRP餘額 | FRED `RRPONTSYD` | 日 |
| | M2貨幣供給年增率 | FRED `M2SL` | 月 |
| ② 資金成本與信用 | HY信用利差OAS | FRED `BAMLH0A0HYM2` | 日 |
| | 2年10年期公債利差 | FRED `T10Y2Y` | 日 |
| | SOFR-IOER/IORB利差 | FRED `SOFR` / `IOER` / `IORB` | 日 |
| ④ 風險偏好情緒 | VIX | Yahoo Finance `^VIX` | 日 |
| | VIX期限結構 (VIX9D) | Yahoo Finance `^VIX9D` | 日 |
| | 融資餘額年增率 | FINRA 官網月報（HTML 爬取） | 月 |
| ⑤ 跨資產資金流向 | DXY美元指數月變動 | FRED `DTWEXBGS`（備援：Yahoo `DX-Y.NYB`） | 日 |
| 軌道二哨兵代理 | SKEW指數 | Yahoo Finance `^SKEW` | 日 |
| | 10年債殖利率單日變動 | FRED `DGS10` | 日 |
| | S&P500 / 日經 / KOSPI / 上證 日變動 | Yahoo `^GSPC` `^N225` `^KS11` `000001.SS` | 日 |
| | 原油 / 黃金 日變動 | Yahoo `CL=F` `GC=F` | 日 |

**③ 市場微觀結構、部分軌道二訊號（買賣價差、期貨基差、ETF資金流、跨貨幣基差互換、市場廣度、
熔斷事件、期貨隔夜盤）沒有免費可自動化的每日歷史資料來源**，詳見
[`data/UNAVAILABLE_INDICATORS.md`](data/UNAVAILABLE_INDICATORS.md)，該檔案說明原因與替代做法。

## 輸出

- `data/market_liquidity_indicators.csv`：合併後、依 NYSE 交易日補齊的主要資料集
- `data/raw/*.csv`：每個指標的原始頻率資料（供稽核/除錯用）
- `data/UNAVAILABLE_INDICATORS.md`：無法自動化的項目清單與原因

`market_liquidity_indicators.csv` 欄位說明：

| 欄位 | 說明 | 單位 |
|---|---|---|
| `fed_bs_yoy_pct` | Fed資產負債表年增率 | % |
| `on_rrp_balance_usd_bn` | ON RRP餘額 | 十億美元 |
| `m2_yoy_pct` | M2貨幣供給年增率 | % |
| `hy_oas_pct` | HY信用利差OAS | % |
| `t10y2y_bp` | 2年10年期公債利差 | bp |
| `sofr_ioer_spread_bp` | SOFR - IOER/IORB利差 | bp |
| `vix` / `vix9d` | VIX / VIX9D 收盤 | 指數點 |
| `vix_term_structure_9d_minus_vix` | VIX9D - VIX | 指數點 |
| `vix9d_gt_vix_flag` | VIX9D > VIX（term structure 倒掛）| 0/1 |
| `skew_index` | CBOE SKEW指數 | 指數點 |
| `margin_debt_usd_millions` / `margin_debt_yoy_pct` | 融資餘額與年增率 | 百萬美元 / % |
| `dxy_broad_index` / `dxy_monthly_change_pct` | 美元指數與近似月變動 | 指數點 / % |
| `dgs10_1d_change_bp` | 10年公債殖利率單日變動 | bp |
| `sp500_daily_pct_chg` 等 | 各市場/商品日收盤變動（軌道二訊號的日頻近似值）| % |

## 執行方式

```bash
cd market-liquidity-monitor
pip install -r requirements.txt
python -m scraper.build_dataset --years 10
```

## 自動化排程

`.github/workflows/update-market-indicators.yml` 會每日於 GitHub Actions 上執行本爬蟲，
並將更新後的 `data/` 目錄提交回本分支，讓資料集持續保持最新，也可以在 GitHub Actions
頁面手動觸發（workflow_dispatch）立即重跑。

> 注意：本專案的沙盒開發環境的對外連線政策會擋掉 `fred.stlouisfed.org`／Yahoo Finance 等網域，
> 所以無法在該環境內直接執行驗證；GitHub Actions 執行環境有正常對外連線，排程執行後即可在
> `data/` 目錄看到實際下載的十年歷史資料。
