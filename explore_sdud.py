"""
Day 2-3: Explore the raw SDUD data with SQL (DuckDB reads Parquet directly).

Answers this week's three questions:
  Q1. Top 10 drugs by total prescriptions
  Q2. Quarterly trend of total prescriptions
  Q3. How much data is suppressed (privacy-redacted)?

Run: python explore_sdud.py
"""

import duckdb

PARQUET = "data/raw/sdud/year=2023/state=CA/sdud.parquet"

con = duckdb.connect()

print("=== Schema ===")
print(con.execute(f"DESCRIBE SELECT * FROM '{PARQUET}'").df().to_string())

print("\n=== Q1: Top 10 drugs by total prescriptions ===")
print(
    con.execute(f"""
    SELECT
        product_name,
        SUM(TRY_CAST(number_of_prescriptions AS BIGINT)) AS total_rx,
        ROUND(SUM(TRY_CAST(total_amount_reimbursed AS DOUBLE)) / 1e6, 1) AS total_reimbursed_musd
    FROM '{PARQUET}'
    WHERE suppression_used = 'false'
    GROUP BY product_name
    ORDER BY total_rx DESC
    LIMIT 10
    """).df().to_string()
)

print("\n=== Q2: Quarterly trend ===")
print(
    con.execute(f"""
    SELECT
        quarter,
        SUM(TRY_CAST(number_of_prescriptions AS BIGINT)) AS total_rx,
        COUNT(*) AS n_rows
    FROM '{PARQUET}'
    WHERE suppression_used = 'false'
    GROUP BY quarter
    ORDER BY quarter
    """).df().to_string()
)

print("\n=== Q3: Suppression - how much data is hidden? ===")
print(
    con.execute(f"""
    SELECT
        suppression_used,
        COUNT(*) AS n_rows,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS pct
    FROM '{PARQUET}'
    GROUP BY suppression_used
    """).df().to_string()
)

print("\n=== Bonus: FFS vs Managed Care split ===")
print(
    con.execute(f"""
    SELECT
        utilization_type,
        COUNT(*) AS n_rows,
        SUM(TRY_CAST(number_of_prescriptions AS BIGINT)) AS total_rx
    FROM '{PARQUET}'
    WHERE suppression_used = 'false'
    GROUP BY utilization_type
    """).df().to_string()
)

# ── Investigation 1: Suppression rate by utilization_type ──────────────
print("=== Suppression rate by utilization_type ===")
print(
    con.execute(f"""
    SELECT
        utilization_type,
        COUNT(*)                                              AS total_rows,
        SUM(CASE WHEN suppression_used = 'true' THEN 1 END)   AS suppressed_rows,
        ROUND(100.0 * SUM(CASE WHEN suppression_used = 'true' THEN 1 END)
              / COUNT(*), 1)                                  AS pct_suppressed
    FROM '{PARQUET}'
    GROUP BY utilization_type
    ORDER BY utilization_type
    """).df().to_string()
)

# ── Investigation 2: What % of unsuppressed volume do the top-500 drugs cover? ──
# Note: "drug" here = product_name (truncated to 10 chars — crude, but it's
# what we have until the NDC-resolution phase gives us proper drug identities).
print("\n=== Top-N drug concentration (unsuppressed volume only) ===")
print(
    con.execute(f"""
    WITH drug_totals AS (
        SELECT
            product_name,
            SUM(TRY_CAST(number_of_prescriptions AS BIGINT)) AS rx
        FROM '{PARQUET}'
        WHERE suppression_used = 'false'
        GROUP BY product_name
    ),
    ranked AS (
        SELECT
            product_name,
            rx,
            ROW_NUMBER() OVER (ORDER BY rx DESC) AS rank
        FROM drug_totals
    )
    SELECT
        COUNT(*) FILTER (WHERE rank <= 500)                          AS n_top500,
        COUNT(*)                                                     AS n_total_drugs,
        ROUND(100.0 * SUM(rx) FILTER (WHERE rank <= 500) / SUM(rx), 1)
                                                                     AS pct_volume_top500,
        ROUND(100.0 * SUM(rx) FILTER (WHERE rank <= 100) / SUM(rx), 1)
                                                                     AS pct_volume_top100,
        ROUND(100.0 * SUM(rx) FILTER (WHERE rank <= 50)  / SUM(rx), 1)
                                                                     AS pct_volume_top50
    FROM ranked
    """).df().to_string()
)