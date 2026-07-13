import duckdb
con = duckdb.connect()
print(con.execute("""
    SELECT
        x.ingredient,
        COUNT(DISTINCT x.drug_name) AS n_name_variants,
        COUNT(DISTINCT s.ndc)       AS n_ndc11_codes,
        SUM(s.number_of_prescriptions) AS total_rx
    FROM read_parquet('data/staging/sdud/year=*/state=*/sdud.parquet') s
    JOIN 'data/reference/ndc_crosswalk/ndc_crosswalk_enriched.parquet' x
      ON SUBSTR(s.ndc, 1, 9) = x.ndc9
    WHERE NOT s.suppressed
    GROUP BY 1
    ORDER BY total_rx DESC
    LIMIT 15
""").df().to_string())