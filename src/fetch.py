"""Fetch fundamentals + price history via yfinance with SQLite caching."""
from __future__ import annotations

import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "cache.db"
CACHE_TTL_DAYS = 7

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("fetch")


SCHEMA = """
CREATE TABLE IF NOT EXISTS fundamentals (
    ticker TEXT PRIMARY KEY,
    market TEXT,
    name TEXT,
    sector TEXT,
    market_cap REAL,
    price REAL,
    pe REAL,
    pb REAL,
    ev_ebitda REAL,
    debt_equity REAL,
    roe REAL,
    fcf_yield REAL,
    dividend_yield REAL,
    payout_ratio REAL,
    years_paid_dividends INTEGER,
    price_52w_high REAL,
    drawdown_52w REAL,
    fetched_at TEXT
);
CREATE TABLE IF NOT EXISTS prices (
    ticker TEXT,
    date TEXT,
    close REAL,
    PRIMARY KEY (ticker, date)
);
CREATE TABLE IF NOT EXISTS picks_history (
    run_date TEXT,
    rank INTEGER,
    ticker TEXT,
    market TEXT,
    score REAL,
    reason TEXT,
    PRIMARY KEY (run_date, ticker)
);
"""


_NEW_COLS = [
    ("payout_ratio", "REAL"),
    ("years_paid_dividends", "INTEGER"),
    ("price_52w_high", "REAL"),
    ("drawdown_52w", "REAL"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    """Add new fundamentals columns to existing DBs that pre-date them."""
    for col, typ in _NEW_COLS:
        try:
            conn.execute(f"ALTER TABLE fundamentals ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # column already exists


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def _safe(d: dict, key: str) -> float | None:
    v = d.get(key)
    if v is None or v == "" or (isinstance(v, float) and pd.isna(v)):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_fundamentals(ticker: str, market: str, info: dict, hist: pd.DataFrame | None) -> dict:
    market_cap = _safe(info, "marketCap")
    fcf = _safe(info, "freeCashflow")
    fcf_yield = (fcf / market_cap) if (fcf and market_cap and market_cap > 0) else None

    # yfinance returns `dividendYield` in percent form (e.g. 2.0 = 2%); normalize to decimal.
    div_yield = _safe(info, "dividendYield")
    if div_yield is not None:
        div_yield = div_yield / 100

    payout_ratio = _safe(info, "payoutRatio")

    # Derived from price/dividend history
    years_paid = None
    price_52w_high = None
    drawdown_52w = None
    if hist is not None and not hist.empty:
        closes = hist["Close"].dropna()
        if len(closes) >= 6:
            last_12 = closes.tail(12)
            price_52w_high = float(last_12.max())
            current = float(closes.iloc[-1])
            drawdown_52w = (current - price_52w_high) / price_52w_high if price_52w_high > 0 else None
        if "Dividends" in hist.columns:
            divs = hist["Dividends"].fillna(0)
            annual = divs.groupby(divs.index.year).sum()
            recent_5 = annual.tail(5)
            years_paid = int((recent_5 > 0).sum())

    return {
        "ticker": ticker,
        "market": market,
        "name": info.get("shortName") or info.get("longName") or "",
        "sector": info.get("sector") or "",
        "market_cap": market_cap,
        "price": _safe(info, "currentPrice") or _safe(info, "regularMarketPrice"),
        "pe": _safe(info, "trailingPE"),
        "pb": _safe(info, "priceToBook"),
        "ev_ebitda": _safe(info, "enterpriseToEbitda"),
        "debt_equity": _safe(info, "debtToEquity"),
        "roe": _safe(info, "returnOnEquity"),
        "fcf_yield": fcf_yield,
        "dividend_yield": div_yield,
        "payout_ratio": payout_ratio,
        "years_paid_dividends": years_paid,
        "price_52w_high": price_52w_high,
        "drawdown_52w": drawdown_52w,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


def _fetch_one(ticker: str, market: str, want_history: bool) -> tuple[dict | None, pd.DataFrame | None]:
    try:
        t = yf.Ticker(ticker)
        info = t.info or {}
        if not info or not info.get("symbol"):
            return None, None
        # 5y monthly history with dividends so we can compute consistency + drawdown
        h = t.history(period="5y", interval="1mo", auto_adjust=False, actions=True)
        fund = _extract_fundamentals(ticker, market, info, h if not h.empty else None)
        hist_out = None
        if want_history and not h.empty:
            hist_out = pd.DataFrame({
                "ticker": ticker,
                "date": h.index.strftime("%Y-%m-%d"),
                "close": h["Close"].values,
            })
        return fund, hist_out
    except Exception as e:  # noqa: BLE001
        log.warning("fetch %s failed: %s", ticker, e)
        return None, None


def _stale_tickers(conn: sqlite3.Connection, universe: pd.DataFrame) -> set[str]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=CACHE_TTL_DAYS)).isoformat()
    cached = pd.read_sql(
        "SELECT ticker, fetched_at FROM fundamentals WHERE fetched_at >= ?",
        conn, params=(cutoff,),
    )
    fresh = set(cached["ticker"])
    return set(universe["ticker"]) - fresh


def refresh(universe: pd.DataFrame, *, force: bool = False, want_history: bool = True, max_workers: int = 8) -> None:
    """Fetch fundamentals for any ticker missing or older than CACHE_TTL_DAYS."""
    with connect() as conn:
        todo = set(universe["ticker"]) if force else _stale_tickers(conn, universe)
        if not todo:
            log.info("cache fresh, nothing to refresh")
            return

        log.info("refreshing %d / %d tickers", len(todo), len(universe))
        market_map = dict(zip(universe["ticker"], universe["market"]))

        funds: list[dict] = []
        hists: list[pd.DataFrame] = []
        done = 0
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(_fetch_one, t, market_map[t], want_history): t for t in todo}
            for fut in as_completed(futures):
                fund, hist = fut.result()
                if fund:
                    funds.append(fund)
                if hist is not None:
                    hists.append(hist)
                done += 1
                if done % 25 == 0:
                    log.info("  %d/%d", done, len(todo))

        if funds:
            pd.DataFrame(funds).to_sql("fundamentals", conn, if_exists="append", index=False, method="multi", chunksize=500)
            # dedupe: keep latest row per ticker
            conn.execute("""
                DELETE FROM fundamentals
                WHERE rowid NOT IN (
                    SELECT MAX(rowid) FROM fundamentals GROUP BY ticker
                )
            """)
        if hists:
            big = pd.concat(hists, ignore_index=True)
            big.to_sql("prices", conn, if_exists="append", index=False, method="multi", chunksize=500)
            conn.execute("""
                DELETE FROM prices
                WHERE rowid NOT IN (
                    SELECT MAX(rowid) FROM prices GROUP BY ticker, date
                )
            """)
        conn.commit()
        log.info("refresh complete: %d fundamentals, %d price rows", len(funds), sum(len(h) for h in hists))


def load_fundamentals() -> pd.DataFrame:
    with connect() as conn:
        return pd.read_sql("SELECT * FROM fundamentals", conn)


def load_prices(tickers: list[str] | None = None) -> pd.DataFrame:
    with connect() as conn:
        if tickers:
            placeholders = ",".join("?" * len(tickers))
            df = pd.read_sql(f"SELECT * FROM prices WHERE ticker IN ({placeholders})", conn, params=tickers)
        else:
            df = pd.read_sql("SELECT * FROM prices", conn)
    df["date"] = pd.to_datetime(df["date"])
    return df


def last_refresh() -> datetime | None:
    with connect() as conn:
        row = conn.execute("SELECT MAX(fetched_at) FROM fundamentals").fetchone()
    if not row or not row[0]:
        return None
    return datetime.fromisoformat(row[0])


if __name__ == "__main__":
    from universe import build_universe
    u = build_universe()
    refresh(u)
