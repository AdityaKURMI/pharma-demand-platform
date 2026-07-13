import duckdb
con = duckdb.connect()

print(con.execute("""
    WITH sdud AS (
        SELECT SUBSTR(ndc, 1, 9) AS ndc9, year,
               SUM(number_of_prescriptions) AS rx
        FROM read_parquet('data/staging/sdud/year=*/state=*/sdud.parquet')
        WHERE NOT suppressed
        GROUP BY 1, 2
    ),
    dict AS (
        SELECT DISTINCT ndc9 FROM 'data/reference/fda_ndc/fda_ndc.parquet'
        UNION
        SELECT DISTINCT ndc9 FROM 'data/reference/rxnav_ndc/rxnav_resolved.parquet'
        WHERE rxcui IS NOT NULL
    )
    SELECT
        COALESCE(CAST(year AS VARCHAR), 'ALL YEARS') AS year,
        ROUND(100.0 * SUM(CASE WHEN dict.ndc9 IS NOT NULL THEN rx END) / SUM(rx), 2)
            AS pct_volume_matched
    FROM sdud LEFT JOIN dict USING (ndc9)
    GROUP BY ROLLUP(year)
    ORDER BY year
""").df().to_string())