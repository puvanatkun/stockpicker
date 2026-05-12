"""Headless weekly job: refresh data, rescore, snapshot top picks to disk.

Run by Windows Task Scheduler each week. Outputs:
  data/latest_picks.json   — read by the Streamlit app
  data/picks_history.csv   — appended each run
  data/run.log             — stdout+stderr from the run
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
sys.path.insert(0, str(ROOT / "src"))

import fetch
import score
from universe import build_universe

LOG_FILE = DATA / "run.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("weekly")


def main() -> int:
    log.info("=== weekly run start ===")
    try:
        universe = build_universe()
        log.info("universe size: %d", len(universe))
        fetch.refresh(universe)

        fundamentals = fetch.load_fundamentals()
        log.info("fundamentals rows: %d", len(fundamentals))
        scored = score.score_universe(fundamentals)
        top = score.top_n_combined(scored, n=5)

        run_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        out = {
            "run_date": run_date,
            "picks": top.to_dict(orient="records"),
        }
        (DATA / "latest_picks.json").write_text(json.dumps(out, indent=2, default=str))

        history_path = DATA / "picks_history.csv"
        hist_row = top.assign(run_date=run_date)
        if history_path.exists():
            existing = pd.read_csv(history_path)
            combined = pd.concat([existing, hist_row], ignore_index=True)
        else:
            combined = hist_row
        combined.to_csv(history_path, index=False)

        log.info("top 5: %s", ", ".join(top["ticker"].tolist()))
        log.info("=== weekly run done ===")
        return 0
    except Exception:  # noqa: BLE001
        log.exception("weekly run failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
