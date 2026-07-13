import duckdb
con = duckdb.connect()

print(con.execute("""
    WITH sdud AS (
        SELECT
            SUBSTR(ndc, 1, 9) AS ndc9,
            SUM(number_of_prescriptions) AS rx
        FROM read_parquet('data/staging/sdud/year=*/state=*/sdud.parquet')
        WHERE NOT suppressed
        GROUP BY 1
    ),
    fda AS (
        SELECT DISTINCT ndc9 FROM 'data/reference/fda_ndc/fda_ndc.parquet'
    )
    SELECT
        COUNT(*)                                              AS n_ndc9,
        COUNT(fda.ndc9)                                       AS n_matched,
        ROUND(100.0 * COUNT(fda.ndc9) / COUNT(*), 1)          AS pct_codes_matched,
        ROUND(100.0 * SUM(CASE WHEN fda.ndc9 IS NOT NULL THEN rx END)
              / SUM(rx), 1)                                   AS pct_volume_matched
    FROM sdud LEFT JOIN fda USING (ndc9)
""").df().to_string())

print("=== Top 20 unmatched NDCs by volume ===")
print(con.execute("""
    WITH sdud AS (
        SELECT
            SUBSTR(ndc, 1, 9) AS ndc9,
            MAX(product_name) AS product_name,      -- truncated but a hint
            SUM(number_of_prescriptions) AS rx,
            MIN(year) AS first_year,
            MAX(year) AS last_year
        FROM read_parquet('data/staging/sdud/year=*/state=*/sdud.parquet')
        WHERE NOT suppressed
        GROUP BY 1
    ),
    fda AS (SELECT DISTINCT ndc9 FROM 'data/reference/fda_ndc/fda_ndc.parquet')
    SELECT s.ndc9, s.product_name, s.rx, s.first_year, s.last_year
    FROM sdud s LEFT JOIN fda USING (ndc9)
    WHERE fda.ndc9 IS NULL
    ORDER BY s.rx DESC
    LIMIT 20
""").df().to_string())

# Is unmatched volume concentrated in older years? (tests the "discontinued drugs" theory)
print("\n=== Match rate by year ===")
print(con.execute("""
    WITH sdud AS (
        SELECT SUBSTR(ndc, 1, 9) AS ndc9, year,
               SUM(number_of_prescriptions) AS rx
        FROM read_parquet('data/staging/sdud/year=*/state=*/sdud.parquet')
        WHERE NOT suppressed
        GROUP BY 1, 2
    ),
    fda AS (SELECT DISTINCT ndc9 FROM 'data/reference/fda_ndc/fda_ndc.parquet')
    SELECT
        year,
        ROUND(100.0 * SUM(CASE WHEN fda.ndc9 IS NOT NULL THEN rx END) / SUM(rx), 1)
            AS pct_volume_matched
    FROM sdud LEFT JOIN fda USING (ndc9)
    GROUP BY year ORDER BY year
""").df().to_string())