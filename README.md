# Value Stock Picker

A weekly value-stock screener over S&P 500 + ASX 200, with a Streamlit UI and a 3-year snapshot backtest. Deployed on [Streamlit Community Cloud](https://streamlit.io/cloud); weekly data refresh runs as a GitHub Action.

> This is a quantitative screener — **not financial advice.** Always do your own research before investing.

## What it does

- Pulls ~700 tickers from Wikipedia (S&P 500 + ASX 200)
- Fetches fundamentals + 3 years of monthly prices via `yfinance` (free, no API key)
- Scores each name on six value metrics, percentile-ranked **within each market** so USD/AUD differences don't bias the result:
  - EV/EBITDA (30%) · P/B (20%) · P/E (15%) · FCF yield (20%) · ROE (10%) · Dividend yield (5%)
- Applies quality filters (min market cap, positive earnings, sane debt level)
- Surfaces the top 5 **combined across both markets** with a per-stock "why" reason
- 3-year snapshot backtest vs a 50/50 SPY + ^AXJO benchmark

## Deploy to Streamlit Cloud (the mobile-friendly path)

1. **Push this repo to a public GitHub repo** (free Streamlit Cloud tier requires public).
2. Go to <https://share.streamlit.io>, sign in with GitHub, click **New app**.
3. Select your repo, branch `main`, main file `src/app.py`. Click **Deploy**.
4. After deploy completes, open the URL on your phone → tap browser menu → **Add to Home Screen**. You now have an app icon that opens fullscreen.
5. The **Weekly data refresh** GitHub Action (`.github/workflows/weekly-refresh.yml`) runs every Monday and commits the fresh cache back. Streamlit Cloud auto-redeploys on each commit, so the app always shows fresh picks.
6. Trigger it once manually now to verify: GitHub repo → **Actions** tab → **Weekly data refresh** → **Run workflow**.

## Local development

```powershell
cd C:\Users\Entei\stockpicker
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# First-time cache populate (5–10 min, hits ~700 yfinance endpoints)
.\.venv\Scripts\python src\run_weekly.py

# Launch the UI
.\.venv\Scripts\streamlit run src\app.py
```

Opens at <http://localhost:8501>.

## File layout

```
stockpicker/
├── .github/workflows/
│   └── weekly-refresh.yml    GitHub Action: weekly refresh + auto-commit
├── .streamlit/config.toml    Theme + server settings
├── data/                     cache.db, picks_history.csv, latest_picks.json
├── src/
│   ├── universe.py           S&P 500 + ASX 200 ticker lists
│   ├── fetch.py              yfinance wrapper + SQLite cache
│   ├── score.py              Value scoring + reason generation
│   ├── backtest.py           3-year snapshot backtest
│   ├── app.py                Streamlit UI (5 pages)
│   └── run_weekly.py         Headless refresh job
└── requirements.txt
```

## Pages in the app

- **Top Picks** — top 5 with reasons, score bar, metric breakdown
- **Full Ranking** — sortable/filterable table of every scored stock
- **Stock Detail** — pick any ticker, see metric percentiles + 3-year price chart
- **Backtest** — 3-year equity curve vs blended benchmark
- **Settings** — tune the metric weights

## Backtest caveat

Yahoo's free fundamentals are point-in-time-as-of-now only. The backtest takes **today's** top 5 and asks "what would equal-weight monthly rebalancing have returned over the last 3 years?". This has selection and survivorship bias — treat it as directional, not as proof the strategy will work going forward. A truly clean backtest needs a paid point-in-time fundamentals provider.
