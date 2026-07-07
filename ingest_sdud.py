"""
Day 1: Ingest Medicaid State Drug Utilization Data (SDUD) for one state + year.

What this does:
  1. Calls the Medicaid Open Data API for the 2023 SDUD dataset
  2. Pulls all rows for one state (default: CA), page by page
  3. Saves the raw data as a Parquet file, partition-style: data/raw/sdud/year=2023/state=CA/

Run:  python ingest_sdud.py
Then: python explore_sdud.py
"""

import time
from pathlib import Path

import pandas as pd
import requests

# ── Config ────────────────────────────────────────────────────────────────
# Each SDUD year is a separate dataset on data.medicaid.gov with its own ID.
# 2023 dataset ID (from https://data.medicaid.gov/dataset/d890d3a9-6b00-43fd-8b31-fcba4c8e2909)
DATASET_ID = "d890d3a9-6b00-43fd-8b31-fcba4c8e2909"
STATE = "CA"          # start small: one state
PAGE_SIZE = 2000      # rows per API call
OUT_DIR = Path(f"data/raw/sdud/year=2023/state={STATE}")

API_URL = f"https://data.medicaid.gov/api/1/datastore/query/{DATASET_ID}/0"


def fetch_page(offset: int) -> list[dict]:
    """Fetch one page of rows for our state, with basic retry."""
    params = {
        "limit": PAGE_SIZE,
        "offset": offset,
        "conditions[0][property]": "state",
        "conditions[0][value]": STATE,
        "conditions[0][operator]": "=",
    }
    for attempt in range(3):
        try:
            resp = requests.get(API_URL, params=params, timeout=60)
            resp.raise_for_status()
            return resp.json().get("results", [])
        except (requests.RequestException, ValueError) as e:
            wait = 2 ** attempt
            print(f"  attempt {attempt + 1} failed ({e}), retrying in {wait}s...")
            time.sleep(wait)
    raise RuntimeError(f"Failed to fetch offset {offset} after 3 attempts")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_rows: list[dict] = []
    offset = 0
    while True:
        rows = fetch_page(offset)
        if not rows:
            break
        all_rows.extend(rows)
        print(f"fetched {len(all_rows):,} rows so far...")
        offset += PAGE_SIZE
        time.sleep(0.3)  # be polite to a free government API

    df = pd.DataFrame(all_rows)
    print(f"\nTotal rows for {STATE}: {len(df):,}")
    print(f"Columns: {list(df.columns)}")

    out_path = OUT_DIR / "sdud.parquet"
    df.to_parquet(out_path, index=False)
    print(f"Saved -> {out_path}")


if __name__ == "__main__":
    main()