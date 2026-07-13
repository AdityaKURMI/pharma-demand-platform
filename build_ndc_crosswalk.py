"""
Phase 2, Step 3: Build the NDC crosswalk — one authoritative row per ndc9.

Combines two dictionaries with source priority:
  1. FDA NDC Directory (richer structured fields) — deduplicated:
     prefer latest marketing_start, tie-break on having a generic_name
  2. RxNav historical resolution (covers retired codes FDA dropped)

Every downstream query joins SDUD -> this crosswalk to get drug identity.

Output: data/reference/ndc_crosswalk/ndc_crosswalk.parquet
  ndc9         9-digit normalized NDC key (labeler+product)
  drug_name    best available drug name (FDA generic_name, else RxNav concept)
  brand_name   FDA brand name where available
  dosage_form  FDA dosage form where available
  route        FDA route where available
  rxcui        RxNorm concept id where available (RxNav-sourced rows)
  source       'fda' | 'rxnav' — provenance of the identity

Run: python build_ndc_crosswalk.py
"""

from pathlib import Path

import duckdb

FDA = "data/reference/fda_ndc/fda_ndc.parquet"
RXNAV = "data/reference/rxnav_ndc/rxnav_resolved.parquet"
OUT_DIR = Path("data/reference/ndc_crosswalk")
OUT = OUT_DIR / "ndc_crosswalk.parquet"


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect()

    con.execute(f"""
        COPY (
            WITH fda_deduped AS (
                -- 2,403 duplicate ndc9 keys exist (re-registrations, multiple
                -- marketing periods). Rule: latest marketing_start wins;
                -- tie-break: rows that actually have a generic_name.
                SELECT *
                FROM (
                    SELECT *,
                        ROW_NUMBER() OVER (
                            PARTITION BY ndc9
                            ORDER BY
                                (generic_name IS NOT NULL) DESC,
                                marketing_start DESC NULLS LAST
                        ) AS rn
                    FROM '{FDA}'
                    WHERE ndc9 IS NOT NULL
                )
                WHERE rn = 1
            ),
            rxnav_hits AS (
                SELECT ndc9, rxcui, concept_name
                FROM '{RXNAV}'
                WHERE rxcui IS NOT NULL
            )
            SELECT
                COALESCE(f.ndc9, r.ndc9)                 AS ndc9,
                COALESCE(f.generic_name, r.concept_name) AS drug_name,
                f.brand_name                             AS brand_name,
                f.dosage_form                            AS dosage_form,
                f.route                                  AS route,
                r.rxcui                                  AS rxcui,
                CASE WHEN f.ndc9 IS NOT NULL THEN 'fda' ELSE 'rxnav' END AS source
            FROM fda_deduped f
            FULL OUTER JOIN rxnav_hits r USING (ndc9)
        ) TO '{OUT}' (FORMAT PARQUET)
    """)

    # report
    stats = con.execute(f"""
        SELECT
            source,
            COUNT(*)                                   AS n_rows,
            SUM(CASE WHEN drug_name IS NULL THEN 1 ELSE 0 END) AS n_missing_name
        FROM '{OUT}'
        GROUP BY source ORDER BY source
    """).df()
    total, dupes = con.execute(f"""
        SELECT COUNT(*), COUNT(*) - COUNT(DISTINCT ndc9) FROM '{OUT}'
    """).fetchone()

    print(stats.to_string())
    print(f"\nTotal crosswalk rows: {total:,}  (duplicate ndc9 keys: {dupes} — must be 0)")
    print(f"Saved -> {OUT}")

    if dupes != 0:
        raise SystemExit("FAIL: crosswalk has duplicate keys — dedup logic broken")


if __name__ == "__main__":
    main()