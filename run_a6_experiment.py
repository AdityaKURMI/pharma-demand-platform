"""
Phase 4, Step 4 (final experiment before freeze): A5a vs A6.

A5a: frozen best GBM (small model + 2020 down-weight)         — control
A6:  A5a + the two LOE regime features                        — treatment

Evaluated two ways:
  1. Overall MASE (expect ~no movement: only ~21 of 1,448 series carry
     non-zero LOE features)
  2. ENTRY-ADJACENT slice: test predictions for series whose molecule is
     within +/-4 quarters of its observed generic entry at test time —
     the turbulence window where the features could plausibly help.

Pre-committed interpretations (written BEFORE seeing results):
  (a) A6 < A5a on slice  -> regime features improve entry-adjacent forecasts
  (b) no difference      -> 7 events too sparse for a global model; more
                            states/years = future work
  (c) A6 worse overall   -> constant-zero features added noise; reported

After this: ALL NUMBERS FROZEN.

Run: python run_a6_experiment.py
"""

import warnings

import lightgbm as lgb
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

PANEL = "data/modeling/panel.parquet"
N_FOLDS = 4

BASE_FEATURES = [
    "y_lag1", "y_lag2", "y_lag3", "y_lag4", "y_lag8",
    "y_rollmean4", "y_rollstd4",
    "covid_shock", "q2", "q3", "q4",
]
LOE_FEATURES = ["generic_entry_occurred", "quarters_since_entry"]
CATS = ["state", "ingredient"]

SMALL_PARAMS = dict(n_estimators=200, learning_rate=0.05, num_leaves=31,
                    min_child_samples=50, subsample=0.9, colsample_bytree=0.9,
                    random_state=42, verbose=-1)


def mase_scale_factors(panel: pd.DataFrame) -> pd.Series:
    def factor(g):
        tr = g[g["quarter_idx"] < 24 - N_FOLDS].sort_values("quarter_idx")
        vals = tr["prescriptions"].to_numpy(dtype=float)
        if len(vals) <= 4:
            return np.nan
        diffs = np.abs(vals[4:] - vals[:-4])
        diffs = diffs[~np.isnan(diffs)]
        return float(np.mean(diffs)) if len(diffs) else np.nan
    return panel.groupby(["state", "ingredient"]).apply(factor).rename("mase_scale")


def run(name: str, features: list[str], panel: pd.DataFrame,
        scale: pd.Series) -> pd.DataFrame:
    df = panel.copy()
    for c in CATS:
        df[c] = df[c].astype("category")

    frames = []
    for k in range(N_FOLDS):
        test_q = 24 - N_FOLDS + k
        train = df[(df["quarter_idx"] < test_q) & df["y"].notna() & df["y_lag1"].notna()]
        test = df[df["quarter_idx"] == test_q]
        weights = np.where(train["year"] == 2020, 0.2, 1.0)

        model = lgb.LGBMRegressor(**SMALL_PARAMS)
        model.fit(train[features + CATS], train["y"],
                  sample_weight=weights, categorical_feature=CATS)
        out = test[["state", "ingredient", "prescriptions",
                    "quarter_idx", "entry_q"]].copy()
        out["y_pred"] = np.clip(np.expm1(model.predict(test[features + CATS])), 0, None)
        out["fold"] = k
        frames.append(out)

    dfm = pd.concat(frames, ignore_index=True)
    dfm = dfm.merge(scale.reset_index(), on=["state", "ingredient"], how="left")
    dfm = dfm[dfm["prescriptions"].notna() & (dfm["mase_scale"] > 0)]
    dfm["ase"] = np.abs(dfm["prescriptions"] - dfm["y_pred"]) / dfm["mase_scale"]
    dfm["model"] = name
    return dfm


def report(dfm: pd.DataFrame) -> None:
    name = dfm["model"].iloc[0]
    overall = dfm["ase"].mean()
    adj = dfm[(dfm["entry_q"].notna())
              & ((dfm["quarter_idx"] - dfm["entry_q"]).abs() <= 4)]
    line = f"[{name}] overall MASE={overall:.3f}"
    if len(adj):
        line += (f" | entry-adjacent slice MASE={adj['ase'].mean():.3f} "
                 f"(n={len(adj)} predictions, "
                 f"{adj.groupby(['state','ingredient']).ngroups} series)")
    else:
        line += " | entry-adjacent slice: no test rows in window"
    print(line)


def main() -> None:
    panel = pd.read_parquet(PANEL)
    scale = mase_scale_factors(panel)

    a5a = run("A5a_control", BASE_FEATURES, panel, scale)
    a6 = run("A6_loe_features", BASE_FEATURES + LOE_FEATURES, panel, scale)

    print("=== A5a vs A6 (pre-committed interpretations in docstring) ===")
    report(a5a)
    report(a6)

    both = pd.concat([a5a, a6], ignore_index=True)
    both.to_parquet("data/modeling/a6_predictions.parquet", index=False)
    print("\nSaved -> data/modeling/a6_predictions.parquet")
    print("\n*** NUMBERS ARE NOW FROZEN. ***")


if __name__ == "__main__":
    main()