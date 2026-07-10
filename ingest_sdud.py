"""
Week 2: Parameterized, incremental SDUD ingestion.

Loops over (year, state) combinations, skips partitions already downloaded
(idempotent / incremental), and writes raw Parquet with a manifest of what
was fetched and when.

Run examples:
  python ingest_sdud.py                          # default states, all years in registry
  python ingest_sdud.py --years 2023 --states CA TX
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

from registry import DEFAULT_STATES, SDUD_DATASETS

PAGE_SIZE = 2000
RAW_ROOT = Path("data/raw/sdud")
API_TEMPLATE = "https://data.medicaid.gov/api/1/datastore/query/{dataset_id}/0"


def fetch_page(dataset_id: str, state: str, offset: int) -> list[dict]:
    params = {
        "limit": PAGE_SIZE,
        "offset": offset,
        "conditions[0][property]": "state",
        "conditions[0][value]": state,
        "conditions[0][operator]": "=",
    }
    url = API_TEMPLATE.format(dataset_id=dataset_id)
    for attempt in range(5):                                   # was 4
        try:
            resp = requests.get(url, params=params, timeout=120)   # was 60
            resp.raise_for_status()
            return resp.json().get("results", [])
        except (requests.RequestException, ValueError,
                TimeoutError, OSError) as e:                   # wider net
            wait = 2 ** attempt
            print(f"    attempt {attempt + 1} failed ({type(e).__name__}: {e}); retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError(f"fetch failed: {state} offset={offset}")


def ingest_partition(year: int, state: str, force: bool = False) -> None:
    dataset_id = SDUD_DATASETS[year]
    out_dir = RAW_ROOT / f"year={year}" / f"state={state}"
    out_file = out_dir / "sdud.parquet"
    manifest_file = out_dir / "_manifest.json"

    if out_file.exists() and not force:
        print(f"[skip] {year}/{state} already ingested ({out_file})")
        return

    print(f"[ingest] {year}/{state} ...")
    rows: list[dict] = []
    offset = 0
    while True:
        page = fetch_page(dataset_id, state, offset)
        if not page:
            break
        rows.extend(page)
        offset += PAGE_SIZE
        if offset % 20000 == 0:
            print(f"    {len(rows):,} rows so far")
        time.sleep(0.3)

    if not rows:
        print(f"[warn] {year}/{state}: API returned 0 rows — check state code / dataset ID")
        return

    df = pd.DataFrame(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out_file, index=False)

    manifest = {
        "year": year,
        "state": state,
        "dataset_id": dataset_id,
        "row_count": len(df),
        "columns": list(df.columns),
        "ingested_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    manifest_file.write_text(json.dumps(manifest, indent=2))
    print(f"[done] {year}/{state}: {len(df):,} rows -> {out_file}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", nargs="*", type=int, default=sorted(SDUD_DATASETS))
    parser.add_argument("--states", nargs="*", default=DEFAULT_STATES)
    parser.add_argument("--force", action="store_true", help="re-download even if present")
    args = parser.parse_args()

    for year in args.years:
        if year not in SDUD_DATASETS:
            print(f"[error] no dataset ID registered for {year}; add it to registry.py")
            continue
        for state in args.states:
            ingest_partition(year, state, force=args.force)


if __name__ == "__main__":
    main()