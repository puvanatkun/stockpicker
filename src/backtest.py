"""3-year monthly-rebalanced backtest of the current top-5 value picks vs benchmark.

Honest caveat (shown in the UI too): free fundamentals from yfinance only cover the
trailing ~4 quarters, so this is a *snapshot backtest* — we take today's top 5 and
ask "what would equal-weight monthly rebalancing of these names have returned over
the last 3 years vs a blended SP500/ASX index?". This is forward-looking-biased
(survivorship + selection on current fundamentals); treat it as directional.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import yfinance as yf

BENCHMARKS = {"SP500": "SPY", "ASX": "^AXJO"}
LOOKBACK_YEARS = 3
TRADING_MONTHS = 12
RISK_FREE = 0.04  # rough annualized cash rate for Sharpe


def _monthly_returns(ticker: str, years: int = LOOKBACK_YEARS) -> pd.Series:
    h = yf.Ticker(ticker).history(period=f"{years}y", interval="1mo", auto_adjust=True)
    if h.empty:
        return pd.Series(dtype=float, name=ticker)
    return h["Close"].pct_change().dropna().rename(ticker)


def _portfolio_returns(tickers: list[str], years: int) -> pd.Series:
    """Equal-weight monthly-rebalanced portfolio of tickers."""
    rets = pd.concat([_monthly_returns(t, years) for t in tickers], axis=1).dropna(how="all")
    if rets.empty:
        return pd.Series(dtype=float)
    # Equal weight monthly rebalance == mean across columns, ignoring missing
    return rets.mean(axis=1, skipna=True).rename("portfolio")


def _blended_benchmark(picks_df: pd.DataFrame, years: int) -> pd.Series:
    """50/50 SPY + ^AXJO if both markets represented; else just the one."""
    markets = picks_df["market"].unique().tolist()
    bm_tickers = [BENCHMARKS[m] for m in markets if m in BENCHMARKS]
    if not bm_tickers:
        bm_tickers = list(BENCHMARKS.values())
    rets = pd.concat([_monthly_returns(t, years) for t in bm_tickers], axis=1).dropna(how="all")
    return rets.mean(axis=1, skipna=True).rename("benchmark")


def _equity_curve(returns: pd.Series, start: float = 10_000.0) -> pd.Series:
    return (1 + returns.fillna(0)).cumprod() * start


def _summary_stats(returns: pd.Series) -> dict:
    r = returns.dropna()
    if r.empty:
        return {"cagr": np.nan, "total_return": np.nan, "vol": np.nan, "sharpe": np.nan,
                "max_drawdown": np.nan, "win_rate": np.nan}
    total = (1 + r).prod() - 1
    years = len(r) / TRADING_MONTHS
    cagr = (1 + total) ** (1 / years) - 1 if years > 0 else np.nan
    vol = r.std() * np.sqrt(TRADING_MONTHS)
    sharpe = ((cagr - RISK_FREE) / vol) if vol and not np.isnan(vol) else np.nan
    curve = (1 + r).cumprod()
    dd = (curve / curve.cummax() - 1).min()
    win_rate = (r > 0).mean()
    return {
        "cagr": cagr,
        "total_return": total,
        "vol": vol,
        "sharpe": sharpe,
        "max_drawdown": dd,
        "win_rate": win_rate,
    }


def run_backtest(picks_df: pd.DataFrame, years: int = LOOKBACK_YEARS) -> dict:
    """Run snapshot backtest on the given picks vs blended benchmark.

    Returns a dict with equity curves (Series) and stat dicts for both portfolio
    and benchmark, plus alpha.
    """
    tickers = picks_df["ticker"].tolist()
    port_r = _portfolio_returns(tickers, years)
    bench_r = _blended_benchmark(picks_df, years)

    # Align on shared dates so comparison is fair
    aligned = pd.concat([port_r, bench_r], axis=1, join="inner").dropna()
    if aligned.empty:
        return {"error": "Insufficient price history for backtest"}

    port_r, bench_r = aligned["portfolio"], aligned["benchmark"]
    port_curve = _equity_curve(port_r)
    bench_curve = _equity_curve(bench_r)

    port_stats = _summary_stats(port_r)
    bench_stats = _summary_stats(bench_r)
    alpha = (port_stats["cagr"] - bench_stats["cagr"]) if not np.isnan(port_stats["cagr"]) else np.nan

    return {
        "portfolio_curve": port_curve,
        "benchmark_curve": bench_curve,
        "portfolio_returns": port_r,
        "benchmark_returns": bench_r,
        "portfolio_stats": port_stats,
        "benchmark_stats": bench_stats,
        "alpha": alpha,
        "tickers": tickers,
    }
