"""Value scoring: percentile-rank metrics within each market, then weighted blend."""
from __future__ import annotations

import pandas as pd
import numpy as np

DEFAULT_WEIGHTS = {
    "ev_ebitda": 0.30,
    "pb":        0.20,
    "pe":        0.15,
    "fcf_yield": 0.20,
    "roe":       0.10,
    "dividend_yield": 0.05,
}

# True = lower raw value is better (cheap); False = higher is better (quality/yield).
LOWER_IS_BETTER = {
    "ev_ebitda": True,
    "pb": True,
    "pe": True,
    "fcf_yield": False,
    "roe": False,
    "dividend_yield": False,
}

# Pretty labels for the "why this pick" string.
LABELS = {
    "ev_ebitda": "EV/EBITDA",
    "pb": "P/B",
    "pe": "P/E",
    "fcf_yield": "FCF yield",
    "roe": "ROE",
    "dividend_yield": "Div yield",
}


def apply_quality_filters(df: pd.DataFrame) -> pd.DataFrame:
    """Drop rows that are likely value traps or have unusable data.

    Market-cap thresholds differ by market (USD vs AUD)."""
    df = df.copy()
    for col in ("market_cap", "pe", "pb", "ev_ebitda", "debt_equity", "price"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    cap_floor = df["market"].map({"SP500": 500e6, "ASX": 200e6}).fillna(200e6)

    mask = (
        df["market_cap"].fillna(0).ge(cap_floor)
        & df["pe"].fillna(-1).gt(0)
        # Negative P/B (negative book value) and negative EV/EBITDA aren't "deep value" —
        # they signal balance-sheet damage. Allow NaN through so we don't drop too many.
        & ~(df["pb"].lt(0).fillna(False))
        & ~(df["ev_ebitda"].lt(0).fillna(False))
        & df["debt_equity"].fillna(0).lt(300)  # yfinance reports D/E as percent (e.g. 150 = 1.5x)
        & df["price"].fillna(0).gt(1)
    )
    return df.loc[mask].reset_index(drop=True)


def _percentile_score(series: pd.Series, lower_is_better: bool) -> pd.Series:
    """Return 0..1 score where 1 = most attractive. NaNs get 0.5 (neutral)."""
    s = pd.to_numeric(series, errors="coerce")
    ranked = s.rank(pct=True, ascending=not lower_is_better)
    return ranked.fillna(0.5)


def score_universe(df: pd.DataFrame, weights: dict | None = None) -> pd.DataFrame:
    """Add per-metric percentile columns and a combined `score` column.

    Ranking is computed *within each market* so USD/AUD differences don't bias things,
    then the final combined score lives on the same 0..1 scale across both."""
    weights = weights or DEFAULT_WEIGHTS
    df = apply_quality_filters(df)
    if df.empty:
        return df.assign(score=[])

    pieces = []
    for market, group in df.groupby("market", sort=False):
        g = group.copy()
        score = pd.Series(0.0, index=g.index)
        for metric, w in weights.items():
            col = f"{metric}_pct"
            g[col] = _percentile_score(g[metric], LOWER_IS_BETTER[metric])
            score = score + w * g[col]
        g["score"] = score
        pieces.append(g)

    out = pd.concat(pieces, ignore_index=True)
    return out.sort_values("score", ascending=False).reset_index(drop=True)


def _fmt(metric: str, value: float | None) -> str:
    if value is None or pd.isna(value):
        return "n/a"
    if metric in ("roe", "fcf_yield", "dividend_yield"):
        # All three are stored as decimal (0.02 = 2%).
        return f"{value * 100:.1f}%"
    return f"{value:.1f}"


def build_reason(row: pd.Series, top_n: int = 3) -> str:
    """Pick the metrics where this stock scores highest and describe them."""
    pct_cols = [(m, f"{m}_pct") for m in DEFAULT_WEIGHTS if f"{m}_pct" in row.index]
    ranked = sorted(pct_cols, key=lambda mc: row[mc[1]], reverse=True)
    parts = []
    for metric, pct_col in ranked[:top_n]:
        if row[pct_col] < 0.6:  # not actually a strength
            continue
        pct = row[pct_col] * 100
        top_pct = max(1, round(100 - pct))
        parts.append(f"{LABELS[metric]} {_fmt(metric, row[metric])} (top {top_pct}%)")
    if not parts:
        return "Balanced mid-tier value profile"
    return " · ".join(parts)


def top_n_combined(scored: pd.DataFrame, n: int = 5) -> pd.DataFrame:
    """Top N tickers across both markets, with a human-readable reason column."""
    top = scored.head(n).copy()
    top["reason"] = top.apply(build_reason, axis=1)
    cols = ["ticker", "market", "name", "sector", "score", "reason",
            "pe", "pb", "ev_ebitda", "roe", "fcf_yield", "dividend_yield",
            "price", "market_cap"]
    return top[[c for c in cols if c in top.columns]]
