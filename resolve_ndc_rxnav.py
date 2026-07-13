"""
Phase 2, Step 2: Resolve NDCs missing from the FDA directory via RxNav.

RxNorm (US National Library of Medicine) keeps HISTORICAL NDC mappings —
retired codes still resolve to their drug concept (RxCUI). We batch-query
the RxNav ndcstatus endpoint for every SDUD ndc9 that the FDA directory
could not identify.

Engineering notes (learned the hard way in Week 2):
  - CHECKPOINTING: progress saved every CHECKPOINT_EVERY lookups; the run
    is resumable — already-resolved NDCs are never re-queried.
  - Rate-limited: RxNav asks for <= 20 requests/sec; we stay way below.
  - One representative 11-digit NDC per ndc9 (the highest-volume one) —
    drug identity lives at the 9-digit level, package code doesn't matter.

Output: data/reference/rxnav_ndc/rxnav_resolved.parquet
  columns: ndc9, ndc11_queried, status, active, rxcui, concept_name

Run: python resolve_ndc_rxnav.py
"""

import time
from pathlib import Path

import duckdb
import pandas as pd
import requests

STAGING_GLOB = "data/staging/sdud/year=*/state=*/sdud.parquet"
FDA_PARQUET = "data/reference/fda_ndc/fda_ndc.parquet"
OUT_DIR = Path("data/reference/rxnav_ndc")
OUT_FILE = OUT_DIR / "rxnav_resolved.parquet"

API_URL = "https://rxnav.nlm.nih.gov/REST/ndcstatus.json"
CHECKPOINT_EVERY = 500
SLEEP_BETWEEN = 0.1          # ~10 req/sec, under RxNav's 20/sec guidance


def get_unmatched_ndcs(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """ndc9s absent from FDA directory + their highest-volume 11-digit NDC."""
    return con.execute(f"""
        WITH sdud AS (
            SELECT
                SUBSTR(ndc, 1, 9) AS ndc9,
                ndc               AS ndc11,
                SUM(number_of_prescriptions) AS rx
            FROM read_parquet('{STAGING_GLOB}')
            WHERE NOT suppressed
            GROUP BY 1, 2
        ),
        ranked AS (
            SELECT ndc9, ndc11, rx,
                   ROW_NUMBER() OVER (PARTITION BY ndc9 ORDER BY rx DESC) AS rn
            FROM sdud
        ),
        fda AS (SELECT DISTINCT ndc9 FROM '{FDA_PARQUET}')
        SELECT r.ndc9, r.ndc11, r.rx
        FROM ranked r LEFT JOIN fda USING (ndc9)
        WHERE r.rn = 1 AND fda.ndc9 IS NULL
        ORDER BY r.rx DESC
    """).df()


def query_rxnav(ndc11: str, session: requests.Session) -> dict:
    for attempt in range(4):
        try:
            resp = session.get(API_URL, params={"ndc": ndc11}, timeout=30)
            resp.raise_for_status()
            js = resp.json().get("ndcStatus", {}) or {}
            return {
                "status": js.get("status"),
                "active": js.get("active"),
                "rxcui": js.get("rxcui") or None,
                "concept_name": js.get("conceptName") or None,
            }
        except (requests.RequestException, ValueError, TimeoutError, OSError) as e:
            wait = 2 ** attempt
            print(f"    {ndc11}: attempt {attempt+1} failed ({type(e).__name__}); retry in {wait}s")
            time.sleep(wait)
    return {"status": "QUERY_FAILED", "active": None, "rxcui": None, "concept_name": None}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    todo = get_unmatched_ndcs(con)
    print(f"{len(todo):,} unmatched ndc9 codes to resolve")

    # Resume support: load previous checkpoint, skip what's done
    done: pd.DataFrame
    if OUT_FILE.exists():
        done = pd.read_parquet(OUT_FILE)
        todo = todo[~todo["ndc9"].isin(done["ndc9"])]
        print(f"checkpoint found: {len(done):,} already resolved, {len(todo):,} remaining")
    else:
        done = pd.DataFrame()

    session = requests.Session()
    buffer: list[dict] = []

    for i, row in enumerate(todo.itertuples(index=False), start=1):
        result = query_rxnav(row.ndc11, session)
        buffer.append({
            "ndc9": row.ndc9,
            "ndc11_queried": row.ndc11,
            **result,
        })
        time.sleep(SLEEP_BETWEEN)

        if i % CHECKPOINT_EVERY == 0 or i == len(todo):
            done = pd.concat([done, pd.DataFrame(buffer)], ignore_index=True)
            done.to_parquet(OUT_FILE, index=False)
            buffer = []
            n_hit = done["rxcui"].notna().sum()
            print(f"[checkpoint] {len(done):,} resolved so far "
                  f"({100.0 * n_hit / len(done):.1f}% got an rxcui)")

    if len(done):
        n_hit = done["rxcui"].notna().sum()
        print(f"\nDone. {len(done):,} queried, {n_hit:,} resolved to an RxCUI "
              f"({100.0 * n_hit / len(done):.1f}%). Saved -> {OUT_FILE}")


if __name__ == "__main__":
    main()