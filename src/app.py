"""Streamlit UI for the stock picker."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

# Allow running via `streamlit run src/app.py` from project root
sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch
import score
from backtest import run_backtest
from universe import build_universe

st.set_page_config(page_title="Value Stock Picker", layout="wide", page_icon=":chart_with_upwards_trend:")


@st.cache_data(ttl=3600, show_spinner=False)
def _load_scored(weights_tuple: tuple, _refresh_token: float):
    weights = dict(weights_tuple)
    df = fetch.load_fundamentals()
    if df.empty:
        return df
    return score.score_universe(df, weights=weights)


def _format_pct(v):
    if v is None or pd.isna(v):
        return "—"
    return f"{v * 100:.1f}%" if abs(v) < 5 else f"{v:.1f}%"


def _format_num(v, digits=1):
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.{digits}f}"


def _format_mc(v):
    if v is None or pd.isna(v):
        return "—"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:.0f}"


# ---------- Sidebar ----------
st.sidebar.title("Value Stock Picker")
st.sidebar.caption("Top 5 combined picks from S&P 500 + ASX 200")

last = fetch.last_refresh()
if last:
    age = datetime.now(timezone.utc) - last
    st.sidebar.metric("Cache age", f"{age.days}d {age.seconds // 3600}h")
else:
    st.sidebar.warning("No data cached yet — refresh first")

with st.sidebar.expander("Manual refresh"):
    st.caption(
        "Fetches ~700 tickers — takes 5–10 min. On Streamlit Cloud the "
        "weekly GitHub Action handles this automatically; you usually don't "
        "need to click this."
    )
    if st.button("Refresh now", use_container_width=True):
        with st.spinner("Refreshing fundamentals (this takes a few minutes)…"):
            universe = build_universe()
            fetch.refresh(universe)
        st.cache_data.clear()
        st.rerun()

page = st.sidebar.radio(
    "Page",
    ["Top Picks", "Full Ranking", "Stock Detail", "Backtest", "Settings"],
    label_visibility="collapsed",
)

st.sidebar.markdown("---")
st.sidebar.info(
    "This is a quantitative screener — not financial advice. "
    "Always do your own research before investing."
)

# Persist weights across pages via session state
if "weights" not in st.session_state:
    st.session_state["weights"] = dict(score.DEFAULT_WEIGHTS)

# Use last_refresh timestamp as cache key so manual refresh re-triggers compute
refresh_token = last.timestamp() if last else 0.0
weights_tuple = tuple(sorted(st.session_state["weights"].items()))
scored = _load_scored(weights_tuple, refresh_token)

if scored.empty:
    st.title("No data yet")
    st.write(
        "No cached data found. If you just deployed to Streamlit Cloud, trigger the "
        "**Weekly data refresh** workflow once from the GitHub Actions tab to populate "
        "the cache (~5–10 min). On your local machine, run `python src/run_weekly.py`."
    )
    st.stop()


# ---------- Page: Top Picks ----------
if page == "Top Picks":
    st.title("This week's top 5 picks")
    st.caption(f"Combined ranking across S&P 500 + ASX 200. Last refresh: "
               f"{last.strftime('%Y-%m-%d %H:%M UTC') if last else 'never'}")

    top = score.top_n_combined(scored, n=5)
    for i, row in top.iterrows():
        with st.container(border=True):
            st.markdown(f"### #{i + 1} · `{row['ticker']}` — {row['name']}")
            st.caption(f"{row['market']} · {row.get('sector', '')}")
            st.markdown(f"**Why:** {row['reason']}")
            st.progress(float(row["score"]), text=f"Value score: {row['score']:.2f}")

            mc1, mc2 = st.columns(2)
            mc1.metric("Price", _format_num(row.get("price"), 2))
            mc2.metric("Market cap", _format_mc(row.get("market_cap")))

            with st.expander("Metric breakdown"):
                mc1, mc2, mc3 = st.columns(3)
                mc1.metric("P/E", _format_num(row.get("pe")))
                mc2.metric("P/B", _format_num(row.get("pb")))
                mc3.metric("EV/EBITDA", _format_num(row.get("ev_ebitda")))
                mc1.metric("ROE", _format_pct(row.get("roe")))
                mc2.metric("FCF yield", _format_pct(row.get("fcf_yield")))
                mc3.metric("Div yield", _format_pct(row.get("dividend_yield")))

    st.download_button(
        "Download picks as CSV",
        top.to_csv(index=False).encode(),
        file_name=f"picks_{datetime.now().strftime('%Y%m%d')}.csv",
        mime="text/csv",
    )


# ---------- Page: Full Ranking ----------
elif page == "Full Ranking":
    st.title("Full ranking")
    market_filter = st.multiselect("Market", ["SP500", "ASX"], default=["SP500", "ASX"])
    sectors = sorted(scored["sector"].dropna().unique().tolist())
    sector_filter = st.multiselect("Sector", sectors, default=[])

    filtered = scored[scored["market"].isin(market_filter)]
    if sector_filter:
        filtered = filtered[filtered["sector"].isin(sector_filter)]

    display = filtered[["ticker", "market", "name", "sector", "score",
                        "pe", "pb", "ev_ebitda", "roe", "fcf_yield",
                        "dividend_yield", "price", "market_cap"]].copy()
    display["score"] = display["score"].round(3)
    display["roe"] = (display["roe"] * 100).round(1)
    display["fcf_yield"] = (display["fcf_yield"] * 100).round(1)
    display["dividend_yield"] = (display["dividend_yield"] * 100).round(1)

    st.dataframe(display, use_container_width=True, height=600, hide_index=True)


# ---------- Page: Stock Detail ----------
elif page == "Stock Detail":
    st.title("Stock detail")
    ticker = st.selectbox("Ticker", scored["ticker"].tolist())
    row = scored[scored["ticker"] == ticker].iloc[0]

    st.subheader(f"{ticker} — {row['name']}")
    st.caption(f"{row['market']} · {row.get('sector', '')}")

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Value score", f"{row['score']:.2f}")
    c2.metric("Price", _format_num(row.get("price"), 2))
    c3.metric("Market cap", _format_mc(row.get("market_cap")))
    overall_rank = scored.index[scored["ticker"] == ticker][0] + 1
    c4.metric("Rank (combined)", f"#{overall_rank} / {len(scored)}")

    st.markdown("### Metric percentiles within market (higher = more attractive)")
    pct_cols = {m: row[f"{m}_pct"] for m in score.DEFAULT_WEIGHTS}
    pct_df = pd.DataFrame({
        "metric": [score.LABELS[m] for m in pct_cols],
        "percentile": list(pct_cols.values()),
        "raw": [row[m] for m in pct_cols],
    })
    fig = px.bar(pct_df, x="percentile", y="metric", orientation="h",
                 range_x=[0, 1], text=pct_df["raw"].apply(lambda v: f"{v:.2f}" if pd.notna(v) else "n/a"))
    fig.update_layout(height=320, margin=dict(l=20, r=20, t=20, b=20))
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("### 3-year price")
    prices = fetch.load_prices([ticker])
    if not prices.empty:
        fig2 = px.line(prices.sort_values("date"), x="date", y="close")
        fig2.update_layout(height=380, margin=dict(l=20, r=20, t=20, b=20))
        st.plotly_chart(fig2, use_container_width=True)
    else:
        st.info("No cached price history for this ticker.")


# ---------- Page: Backtest ----------
elif page == "Backtest":
    st.title("Backtest — 3-year snapshot")
    st.info(
        "**Caveat:** Free data only gives current fundamentals, so this is a "
        "*snapshot backtest* — it asks 'what would today's top 5 have returned "
        "with monthly equal-weight rebalancing over the last 3 years?'. "
        "It has selection and survivorship bias. Use it as a sanity check, "
        "not as proof the strategy will work going forward."
    )

    top = score.top_n_combined(scored, n=5)
    st.write("Backtesting current top 5:", ", ".join(top["ticker"].tolist()))

    if st.button("Run backtest", type="primary"):
        with st.spinner("Pulling 3-year monthly prices…"):
            result = run_backtest(top, years=3)

        if "error" in result:
            st.error(result["error"])
        else:
            ps, bs = result["portfolio_stats"], result["benchmark_stats"]
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Portfolio CAGR", _format_pct(ps["cagr"]),
                      delta=_format_pct(result["alpha"]) + " vs bench")
            c2.metric("Max drawdown", _format_pct(ps["max_drawdown"]))
            c3.metric("Sharpe", _format_num(ps["sharpe"], 2))
            c4.metric("Win rate", _format_pct(ps["win_rate"]))

            curve_df = pd.DataFrame({
                "Date": result["portfolio_curve"].index,
                "Portfolio": result["portfolio_curve"].values,
                "Benchmark (SPY+ASX200 blend)": result["benchmark_curve"].values,
            }).melt(id_vars="Date", var_name="Series", value_name="Value")

            fig = px.line(curve_df, x="Date", y="Value", color="Series",
                          title="$10,000 invested 3 years ago")
            fig.update_layout(height=440, margin=dict(l=20, r=20, t=40, b=20))
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("### Stats comparison")
            stats_df = pd.DataFrame({
                "Metric": ["CAGR", "Total return", "Volatility", "Sharpe", "Max drawdown", "Win rate"],
                "Portfolio": [_format_pct(ps["cagr"]), _format_pct(ps["total_return"]),
                              _format_pct(ps["vol"]), _format_num(ps["sharpe"], 2),
                              _format_pct(ps["max_drawdown"]), _format_pct(ps["win_rate"])],
                "Benchmark": [_format_pct(bs["cagr"]), _format_pct(bs["total_return"]),
                              _format_pct(bs["vol"]), _format_num(bs["sharpe"], 2),
                              _format_pct(bs["max_drawdown"]), _format_pct(bs["win_rate"])],
            })
            st.dataframe(stats_df, use_container_width=True, hide_index=True)


# ---------- Page: Settings ----------
elif page == "Settings":
    st.title("Settings — metric weights")
    st.caption("Weights are normalized automatically. Reload the Top Picks page after changing.")

    new_weights = {}
    for metric, default in score.DEFAULT_WEIGHTS.items():
        new_weights[metric] = st.slider(
            score.LABELS[metric],
            min_value=0.0, max_value=1.0,
            value=st.session_state["weights"].get(metric, default),
            step=0.05,
        )

    total = sum(new_weights.values())
    if total > 0:
        normalized = {k: v / total for k, v in new_weights.items()}
        st.write("Normalized weights:", {k: f"{v:.2f}" for k, v in normalized.items()})
        if st.button("Apply weights", type="primary"):
            st.session_state["weights"] = normalized
            st.cache_data.clear()
            st.success("Weights updated.")
            st.rerun()
    else:
        st.warning("At least one weight must be > 0.")
