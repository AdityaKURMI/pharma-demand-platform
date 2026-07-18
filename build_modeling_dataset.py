"""
Phase 4, Step 1 (v2): Build the modeling dataset — now with LOE features.

v2 addition: two regime features derived from the LOE chapter's OBSERVED
generic-entry quarters (data/modeling/loe_v2_fits.csv):
  - generic_entry_occurred: 0/1 — has this molecule's generic entry
    already happened as of this quarter? (regime indicator)
  - quarters_since_entry: how deep into the post-entry regime we are
    (0 before/without entry — clipped at 0 as a LEAKAGE GUARD: negative
    values would tell the model an entry is coming, information no real
    forecaster has, per finding #19's approval-to-launch gap)

Everything else identical to v1 (see finding #5/#14/#16 rationale).
Output: data/modeling/panel.parquet

Run from repo root: python build_modeling_dataset.py
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

WAREHOUSE = "warehouse/pharma.duckdb"
LOE_FITS = "data/modeling/loe_v2_fits.csv"
OUT = Path("data/modeling/panel.parquet")

TOP_N_INGREDIENTS = 500
MIN_QUARTERS = 16
N_TEST_FOLDS = 4
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
            (f.year - 2018) * 4 + f.quarter - 1  AS quarter_idx,
            f.prescriptions,
            f.n_suppressed_rows
        FROM fact_utilization f
        JOIN dim_drug d USING (drug_key)
        JOIN top_drugs t USING (drug_key)
        ORDER BY f.state, d.ingredient, quarter_idx
    """).df()


def complete_series(df: pd.DataFrame) -> pd.DataFrame:
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

    df["covid_shock"] = ((df["year"] == 2020) & (df["quarter"].isin([1, 2]))).astype(int)
    for q in [2, 3, 4]:
        df[f"q{q}"] = (df["quarter"] == q).astype(int)

    df["fold"] = -1
    for k in range(N_TEST_FOLDS):
        df.loc[df["quarter_idx"] == 24 - N_TEST_FOLDS + k, "fold"] = k
    return df


def add_loe_features(df: pd.DataFrame) -> pd.DataFrame:
    """Join observed generic-entry quarters; derive the two regime features."""
    entry = (pd.read_csv(LOE_FITS)[["ingredient", "entry_quarter_idx"]]
             .rename(columns={"entry_quarter_idx": "entry_q"}))
    df = df.merge(entry, on="ingredient", how="left")

    df["generic_entry_occurred"] = (
        (df["quarter_idx"] >= df["entry_q"]).fillna(False).astype(int))
    df["quarters_since_entry"] = (
        (df["quarter_idx"] - df["entry_q"]).clip(lower=0).fillna(0).astype(int))
    # entry_q kept in the panel: needed for the entry-adjacent evaluation
    # slice (|quarter_idx - entry_q| <= 4), but NOT used as a model feature.
    return df


def main() -> None:
    con = duckdb.connect(WAREHOUSE, read_only=True)
    panel = load_panel(con)
    print(f"warehouse rows (top {TOP_N_INGREDIENTS} ingredients): {len(panel):,}")

    panel = complete_series(panel)
    n_series = panel.groupby(["state", "ingredient"]).ngroups
    print(f"series after completeness filter (>= {MIN_QUARTERS}/24 quarters): {n_series:,}")

    panel = add_features(panel)
    panel = add_loe_features(panel)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT, index=False)

    n_loe = panel.loc[panel["entry_q"].notna()].groupby(["state", "ingredient"]).ngroups
    print(f"series carrying LOE features: {n_loe} "
          f"({panel['generic_entry_occurred'].sum():,} post-entry rows)")
    print(f"panel rows: {len(panel):,} | test rows: {(panel['fold'] >= 0).sum():,}")
    print(f"Saved -> {OUT}")


if __name__ == "__main__":
    main()