"""
Phase 2, Step 3 (v2): Build the NDC crosswalk — one authoritative row per ndc9.

v2 additions (LOE chapter): carries marketing_category + application_number
from the FDA directory and derives a brand_generic flag:
  - FDA rows:   marketing_category ANDA -> generic; NDA/BLA -> brand
  - RxNav rows: RxNorm embeds the application number at the start of the
    concept name (e.g. "NDA021457 200 ACTUAT albuterol ... [ProAir]"),
    so a prefix regex classifies historical codes too
  - everything else (OTC monograph, unapproved) -> 'other'

Combines two dictionaries with source priority:
  1. FDA NDC Directory (richer structured fields) — deduplicated:
     prefer rows with a generic_name, then latest marketing_start
  2. RxNav historical resolution (covers retired codes FDA dropped)

Output: data/reference/ndc_crosswalk/ndc_crosswalk.parquet

Run: python build_ndc_crosswalk.py
NOTE: requires ingest_fda_ndc.py re-run first with the v2 fields
      (marketing_category, application_number) captured.
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
                -- Duplicate ndc9 keys exist (re-registrations, multiple
                -- marketing periods). Rule: rows that actually have a
                -- generic_name win; tie-break latest marketing_start.
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
                f.marketing_category                     AS marketing_category,
                f.application_number                     AS application_number,
                CASE
                    WHEN f.marketing_category ILIKE 'ANDA%' THEN 'generic'
                    WHEN f.marketing_category ILIKE 'NDA%'
                      OR f.marketing_category ILIKE 'BLA%' THEN 'brand'
                    WHEN regexp_matches(r.concept_name, '^ANDA') THEN 'generic'
                    WHEN regexp_matches(r.concept_name, '^NDA')  THEN 'brand'
                    ELSE 'other'
                END                                      AS brand_generic,
                CASE WHEN f.ndc9 IS NOT NULL THEN 'fda' ELSE 'rxnav' END AS source
            FROM fda_deduped f
            FULL OUTER JOIN rxnav_hits r USING (ndc9)
        ) TO '{OUT}' (FORMAT PARQUET)
    """)

    # report
    stats = con.execute(f"""
        SELECT
            source,
            COUNT(*)                                           AS n_rows,
            SUM(CASE WHEN drug_name IS NULL THEN 1 ELSE 0 END) AS n_missing_name
        FROM '{OUT}'
        GROUP BY source ORDER BY source
    """).df()
    flag_stats = con.execute(f"""
        SELECT brand_generic, COUNT(*) AS n_rows
        FROM '{OUT}'
        GROUP BY brand_generic ORDER BY n_rows DESC
    """).df()
    total, dupes = con.execute(f"""
        SELECT COUNT(*), COUNT(*) - COUNT(DISTINCT ndc9) FROM '{OUT}'
    """).fetchone()

    print(stats.to_string(index=False))
    print("\nbrand_generic distribution:")
    print(flag_stats.to_string(index=False))
    print(f"\nTotal crosswalk rows: {total:,}  (duplicate ndc9 keys: {dupes} — must be 0)")
    print(f"Saved -> {OUT}")

    if dupes != 0:
        raise SystemExit("FAIL: crosswalk has duplicate keys — dedup logic broken")


if __name__ == "__main__":
    main()