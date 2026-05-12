"""Build the combined S&P 500 + ASX 200 universe from Wikipedia."""
from __future__ import annotations

import io
from pathlib import Path

import pandas as pd
import requests

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
ASX200_URL = "https://en.wikipedia.org/wiki/S%26P/ASX_200"

UA = {"User-Agent": "Mozilla/5.0 (stockpicker)"}


def _read_html(url: str) -> list[pd.DataFrame]:
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return pd.read_html(io.StringIO(r.text))


def fetch_sp500() -> pd.DataFrame:
    tables = _read_html(SP500_URL)
    df = tables[0].rename(columns={"Symbol": "ticker", "Security": "name", "GICS Sector": "sector"})
    df = df[["ticker", "name", "sector"]].copy()
    df["ticker"] = df["ticker"].str.replace(".", "-", regex=False)
    df["market"] = "SP500"
    return df


def fetch_asx200() -> pd.DataFrame:
    tables = _read_html(ASX200_URL)
    target = None
    for t in tables:
        cols = {str(c).lower() for c in t.columns}
        if any("code" in c or "ticker" in c or "symbol" in c for c in cols) and len(t) > 50:
            target = t
            break
    if target is None:
        raise RuntimeError("ASX 200 constituents table not found on Wikipedia")

    code_col = next(c for c in target.columns if any(k in str(c).lower() for k in ("code", "ticker", "symbol")))
    name_col = next((c for c in target.columns if "company" in str(c).lower() or "name" in str(c).lower()), code_col)
    sector_col = next((c for c in target.columns if "sector" in str(c).lower() or "industry" in str(c).lower()), None)

    df = pd.DataFrame({
        "ticker": target[code_col].astype(str).str.upper().str.strip() + ".AX",
        "name": target[name_col].astype(str),
        "sector": target[sector_col].astype(str) if sector_col else "",
    })
    df["market"] = "ASX"
    return df


def build_universe(force: bool = False) -> pd.DataFrame:
    """Build (or load cached) combined universe. Cache is per-market CSV."""
    sp_path = DATA_DIR / "sp500.csv"
    asx_path = DATA_DIR / "asx200.csv"

    if force or not sp_path.exists():
        fetch_sp500().to_csv(sp_path, index=False)
    if force or not asx_path.exists():
        fetch_asx200().to_csv(asx_path, index=False)

    sp = pd.read_csv(sp_path)
    asx = pd.read_csv(asx_path)
    return pd.concat([sp, asx], ignore_index=True)


if __name__ == "__main__":
    u = build_universe(force=True)
    print(u.groupby("market").size())
    print(u.head())
