"""
Week 2: Staging step — raw (all-VARCHAR) Parquet -> typed, validated Parquet.

For every raw partition:
  1. Validate the expected columns exist (fail loudly if CMS changes schema)
  2. Cast numeric fields (TRY_CAST): failures become NULL
  3. Split output:
       - staging zone: rows that cast cleanly
       - quarantine zone: rows where a numeric cast failed on an
         unsuppressed row (suppressed rows legitimately have empty numbers)
  4. Print a small data-quality report per partition

Run: python stage_sdud.py
"""

from pathlib import Path

import duckdb

RAW_ROOT = Path("data/raw/sdud")
STAGING_ROOT = Path("data/staging/sdud")
QUARANTINE_ROOT = Path("data/quarantine/sdud")

EXPECTED_COLUMNS = {
    "utilization_type", "state", "ndc", "labeler_code", "product_code",
    "package_size", "year", "quarter", "suppression_used", "product_name",
    "units_reimbursed", "number_of_prescriptions", "total_amount_reimbursed",
    "medicaid_amount_reimbursed", "non_medicaid_amount_reimbursed",
}


def stage_partition(raw_file: Path, con: duckdb.DuckDBPyConnection) -> None:
    rel = raw_file.relative_to(RAW_ROOT).parent          # e.g. year=2023/state=CA
    staging_file = STAGING_ROOT / rel / "sdud.parquet"
    quarantine_file = QUARANTINE_ROOT / rel / "sdud_bad_rows.parquet"
    staging_file.parent.mkdir(parents=True, exist_ok=True)
    quarantine_file.parent.mkdir(parents=True, exist_ok=True)

    # 1. schema check
    cols = {
        r[0] for r in
        con.execute(f"DESCRIBE SELECT * FROM '{raw_file}'").fetchall()
    }
    missing = EXPECTED_COLUMNS - cols
    if missing:
        raise ValueError(f"{raw_file}: missing expected columns {missing} — "
                         f"CMS may have changed the schema; investigate before staging.")

    # 2 & 3. typed view + split good/bad
    con.execute(f"""
        CREATE OR REPLACE TEMP VIEW typed AS
        SELECT
            utilization_type,
            state,
            ndc,
            labeler_code,
            product_code,
            package_size,
            CAST(year AS INTEGER)                                   AS year,
            TRY_CAST(quarter AS INTEGER)                            AS quarter,
            (suppression_used = 'true')                             AS suppressed,
            product_name,
            TRY_CAST(units_reimbursed AS DOUBLE)                    AS units_reimbursed,
            TRY_CAST(number_of_prescriptions AS BIGINT)             AS number_of_prescriptions,
            TRY_CAST(total_amount_reimbursed AS DOUBLE)             AS total_amount_reimbursed,
            TRY_CAST(medicaid_amount_reimbursed AS DOUBLE)          AS medicaid_amount_reimbursed,
            TRY_CAST(non_medicaid_amount_reimbursed AS DOUBLE)      AS non_medicaid_amount_reimbursed,
            -- provenance: keep originals for any row we might question later
            number_of_prescriptions                                 AS _raw_rx,
            total_amount_reimbursed                                 AS _raw_total_amt
        FROM '{raw_file}'
    """)

    # bad = unsuppressed rows where a numeric field failed to cast
    con.execute(f"""
        COPY (
            SELECT * FROM typed
            WHERE NOT suppressed
              AND (number_of_prescriptions IS NULL
                   OR total_amount_reimbursed IS NULL)
        ) TO '{quarantine_file}' (FORMAT PARQUET)
    """)

    con.execute(f"""
        COPY (
            SELECT * EXCLUDE (_raw_rx, _raw_total_amt) FROM typed
            WHERE suppressed
               OR (number_of_prescriptions IS NOT NULL
                   AND total_amount_reimbursed IS NOT NULL)
        ) TO '{staging_file}' (FORMAT PARQUET)
    """)

    # 4. report
    good, bad = con.execute(f"""
        SELECT
          (SELECT COUNT(*) FROM '{staging_file}'),
          (SELECT COUNT(*) FROM '{quarantine_file}')
    """).fetchone()
    pct_bad = 100.0 * bad / max(good + bad, 1)
    print(f"[staged] {rel}: {good:,} rows ok, {bad:,} quarantined ({pct_bad:.2f}%)")


def main() -> None:
    con = duckdb.connect()
    raw_files = sorted(RAW_ROOT.glob("year=*/state=*/sdud.parquet"))
    if not raw_files:
        print("No raw partitions found — run ingest_sdud.py first.")
        return
    for f in raw_files:
        stage_partition(f, con)


if __name__ == "__main__":
    main()