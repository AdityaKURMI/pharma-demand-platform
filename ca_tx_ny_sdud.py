import duckdb

con = duckdb.connect()
STAGED = "data/staging/sdud/year=2023/state=*/sdud.parquet"

print("=== FFS vs MCO by state (2023, unsuppressed) ===")
print(
    con.execute(f"""
    SELECT
        state,
        utilization_type,
        COUNT(*)                          AS n_rows,
        SUM(number_of_prescriptions)      AS total_rx,
        ROUND(100.0 * SUM(number_of_prescriptions)
              / SUM(SUM(number_of_prescriptions)) OVER (PARTITION BY state), 1)
                                          AS pct_of_state_rx
    FROM read_parquet('{STAGED}')
    WHERE NOT suppressed
    GROUP BY state, utilization_type
    ORDER BY state, utilization_type
    """).df().to_string()
)

print("\n=== Suppression rate by state and type ===")
print(
    con.execute(f"""
    SELECT
        state,
        utilization_type,
        ROUND(100.0 * SUM(CASE WHEN suppressed THEN 1 END) / COUNT(*), 1) AS pct_suppressed
    FROM read_parquet('{STAGED}')
    GROUP BY state, utilization_type
    ORDER BY state, utilization_type
    """).df().to_string()
)

print(
    con.execute("""
    SELECT
        quarter,
        utilization_type,
        SUM(number_of_prescriptions) AS total_rx,
        ROUND(100.0 * SUM(number_of_prescriptions)
              / SUM(SUM(number_of_prescriptions)) OVER (PARTITION BY quarter), 1)
                                     AS pct_of_quarter
    FROM read_parquet('data/staging/sdud/year=2023/state=NY/sdud.parquet')
    WHERE NOT suppressed
    GROUP BY quarter, utilization_type
    ORDER BY quarter, utilization_type
    """).df().to_string()
)