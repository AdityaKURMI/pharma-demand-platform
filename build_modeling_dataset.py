"""
Phase 4, Step 1: Build the modeling dataset from the warehouse.

Produces one Parquet panel ready for the benchmark: top-N ingredients by
volume (finding #5: top 500 ~= 92%+ of demand), each series indexed by a
continuous quarter index, with features and rolling-origin fold labels.

Key design decisions (paper methodology section):
  - Target: log1p(prescriptions). Series span ~1000x volume; log makes a
    global model's errors relative, not absolute. Invert with expm1;
    evaluate with MASE/sMAPE on the original scale.
  - Series completeness: a series must be present in >= MIN_QUARTERS of
    the 24 quarters. Gaps are filled with explicit rows (prescriptions=0
    -> treated as missing target, features still computed) so lags align.
  - COVID flag: 2020 Q1-Q2 dummy (finding #14: structural shock).
  - Rolling-origin evaluation folds: the last N_TEST_FOLDS quarters each
    serve once as a 1-step-ahead test point, training on everything
    strictly before it. No random splits — time series leakage is the #1
    reviewer kill-shot.

Output: data/modeling/panel.parquet

Run from repo root: python build_modeling_dataset.py
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

WAREHOUSE = "warehouse/pharma.duckdb"
OUT = Path("data/modeling/panel.parquet")

TOP_N_INGREDIENTS = 500
MIN_QUARTERS = 16          # series must exist in >= 16 of 24 quarters
N_TEST_FOLDS = 4           # last 4 quarters = 4 rolling-origin test points
LAGS = [1, 2, 3, 4, 8]
ROLL_WINDOWS = [4]


def load_panel(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    return con.execute(f"""
        WITH top_drugs AS (
            SELECT drug_key
            FROM fact_utilization
            GROUP BY drug_key
            ORDER BY SUM(prescriptions) DESC NULLS LAST
            LIMIT {TOP_N_INGREDIENTS}
        )
        SELECT
            f.state,
            d.ingredient,
            f.year,
            f.quarter,
            (f.year - 2018) * 4 + f.quarter - 1  AS quarter_idx,   -- 0..23
            f.prescriptions,
            f.n_suppressed_rows
        FROM fact_utilization f
        JOIN dim_drug d USING (drug_key)
        JOIN top_drugs t USING (drug_key)
        ORDER BY f.state, d.ingredient, quarter_idx
    """).df()


def complete_series(df: pd.DataFrame) -> pd.DataFrame:
    """Reindex every (state, ingredient) onto the full 0..23 quarter grid so
    lag features align; drop series present in too few quarters."""
    full_idx = pd.DataFrame({"quarter_idx": range(24)})
    out = []
    for (state, ing), g in df.groupby(["state", "ingredient"], sort=False):
        if g["prescriptions"].notna().sum() < MIN_QUARTERS:
            continue
        merged = full_idx.merge(g, on="quarter_idx", how="left")
        merged["state"] = state
        merged["ingredient"] = ing
        merged["year"] = 2018 + merged["quarter_idx"] // 4
        merged["quarter"] = merged["quarter_idx"] % 4 + 1
        out.append(merged)
    return pd.concat(out, ignore_index=True)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.sort_values(["state", "ingredient", "quarter_idx"]).copy()
    df["y"] = np.log1p(df["prescriptions"])

    g = df.groupby(["state", "ingredient"], sort=False)["y"]
    for lag in LAGS:
        df[f"y_lag{lag}"] = g.shift(lag)
    for w in ROLL_WINDOWS:
        df[f"y_rollmean{w}"] = g.shift(1).rolling(w).mean().reset_index(drop=True)
        df[f"y_rollstd{w}"] = g.shift(1).rolling(w).std().reset_index(drop=True)

    # calendar + shock features
    df["covid_shock"] = ((df["year"] == 2020) & (df["quarter"].isin([1, 2]))).astype(int)
    for q in [2, 3, 4]:
        df[f"q{q}"] = (df["quarter"] == q).astype(int)

    # rolling-origin fold labels: fold k tests quarter_idx = 24 - N_TEST_FOLDS + k
    df["fold"] = -1                                    # -1 = training-only rows
    for k in range(N_TEST_FOLDS):
        test_q = 24 - N_TEST_FOLDS + k                 # 20, 21, 22, 23
        df.loc[df["quarter_idx"] == test_q, "fold"] = k
    return df


def main() -> None:
    con = duckdb.connect(WAREHOUSE, read_only=True)
    panel = load_panel(con)
    print(f"warehouse rows (top {TOP_N_INGREDIENTS} ingredients): {len(panel):,}")

    panel = complete_series(panel)
    n_series = panel.groupby(["state", "ingredient"]).ngroups
    print(f"series after completeness filter (>= {MIN_QUARTERS}/24 quarters): {n_series:,}")

    panel = add_features(panel)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT, index=False)

    n_test_rows = (panel["fold"] >= 0).sum()
    print(f"panel rows: {len(panel):,} | test rows across {N_TEST_FOLDS} folds: {n_test_rows:,}")
    print(f"features: {[c for c in panel.columns if c.startswith(('y_', 'q', 'covid'))]}")
    print(f"Saved -> {OUT}")


if __name__ == "__main__":
    main()