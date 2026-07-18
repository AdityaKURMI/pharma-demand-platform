"""
Phase 4, Step 3: LightGBM ablation study.

Runs one experiment at a time, changing exactly one thing vs baseline,
so every effect is attributable. Same 4 rolling-origin folds, same MASE
methodology as run_benchmark.py. Reference to beat: ETS = 0.983.

Experiments:
  A0_baseline      exact config from run_benchmark.py (reproduces 1.114)
  A1_no_ingredient drop the 500-level ingredient categorical (keep state)
  A2_small_model   200 trees, 31 leaves, min_child_samples=50
  A3_downweight20  sample_weight 0.2 for year 2020 rows (COVID shock)
  A4_momentum      add trend features: y_mom1 = y_lag1-y_lag2,
                   y_mom4 = y_lag1-y_lag4, y_drift = y_rollmean4-y_lag8
  A5_combined      best ideas together (edit after seeing A1-A4)

Run: python run_ablations.py
Output: printed table + data/modeling/ablation_results.csv
"""

import warnings
from pathlib import Path

import lightgbm as lgb
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

PANEL = "data/modeling/panel.parquet"
OUT = Path("data/modeling/ablation_results.csv")
N_FOLDS = 4

BASE_FEATURES = [
    "y_lag1", "y_lag2", "y_lag3", "y_lag4", "y_lag8",
    "y_rollmean4", "y_rollstd4",
    "covid_shock", "q2", "q3", "q4",
]
MOMENTUM_FEATURES = ["y_mom1", "y_mom4", "y_drift"]

BASE_PARAMS = dict(n_estimators=600, learning_rate=0.05, num_leaves=63,
                   subsample=0.9, colsample_bytree=0.9,
                   random_state=42, verbose=-1)
SMALL_PARAMS = dict(n_estimators=200, learning_rate=0.05, num_leaves=31,
                    min_child_samples=50, subsample=0.9, colsample_bytree=0.9,
                    random_state=42, verbose=-1)

EXPERIMENTS = {
    "A0_baseline":      dict(params=BASE_PARAMS,  cats=["state", "ingredient"], momentum=False, downweight_2020=None),
    "A1_no_ingredient": dict(params=BASE_PARAMS,  cats=["state"],               momentum=False, downweight_2020=None),
    "A2_small_model":   dict(params=SMALL_PARAMS, cats=["state", "ingredient"], momentum=False, downweight_2020=None),
    "A3_downweight20":  dict(params=BASE_PARAMS,  cats=["state", "ingredient"], momentum=False, downweight_2020=0.2),
    "A4_momentum":      dict(params=BASE_PARAMS,  cats=["state", "ingredient"], momentum=True,  downweight_2020=None),
     "A5a_small_downweight": dict(params=SMALL_PARAMS, cats=["state", "ingredient"],
                                 momentum=False, downweight_2020=0.2),
    "A5b_small_dw_momentum": dict(params=SMALL_PARAMS, cats=["state", "ingredient"],
                                  momentum=True,  downweight_2020=0.2),
    "A5_combined":      dict(params=SMALL_PARAMS, cats=["state"],               momentum=True,  downweight_2020=0.2),
}


def add_momentum(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["y_mom1"] = df["y_lag1"] - df["y_lag2"]
    df["y_mom4"] = df["y_lag1"] - df["y_lag4"]
    df["y_drift"] = df["y_rollmean4"] - df["y_lag8"]
    return df


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


def run_experiment(name: str, cfg: dict, panel: pd.DataFrame,
                   scale: pd.Series) -> dict:
    df = add_momentum(panel) if cfg["momentum"] else panel.copy()
    features = BASE_FEATURES + (MOMENTUM_FEATURES if cfg["momentum"] else [])
    for c in cfg["cats"]:
        df[c] = df[c].astype("category")

    fold_mases = {}
    all_rows = []
    for k in range(N_FOLDS):
        test_q = 24 - N_FOLDS + k
        train = df[(df["quarter_idx"] < test_q) & df["y"].notna() & df["y_lag1"].notna()]
        test = df[df["quarter_idx"] == test_q]

        weights = None
        if cfg["downweight_2020"] is not None:
            weights = np.where(train["year"] == 2020, cfg["downweight_2020"], 1.0)

        model = lgb.LGBMRegressor(**cfg["params"])
        model.fit(train[features + cfg["cats"]], train["y"],
                  sample_weight=weights, categorical_feature=cfg["cats"])

        preds = np.clip(np.expm1(model.predict(test[features + cfg["cats"]])), 0, None)
        out = test[["state", "ingredient", "prescriptions"]].copy()
        out["y_pred"], out["fold"] = preds, k
        all_rows.append(out)

    dfm = pd.concat(all_rows, ignore_index=True)
    dfm = dfm.merge(scale.reset_index(), on=["state", "ingredient"], how="left")
    dfm = dfm[dfm["prescriptions"].notna() & (dfm["mase_scale"] > 0)]
    dfm["ase"] = np.abs(dfm["prescriptions"] - dfm["y_pred"]) / dfm["mase_scale"]

    overall = float(dfm["ase"].mean())
    per_fold = dfm.groupby("fold")["ase"].mean().round(3).to_dict()
    print(f"[{name}] MASE={overall:.3f} | per-fold: {per_fold}")
    return {"experiment": name, "MASE": round(overall, 3),
            **{f"fold{k}": v for k, v in per_fold.items()}}


def main() -> None:
    panel = pd.read_parquet(PANEL)
    scale = mase_scale_factors(panel)

    results = [run_experiment(n, c, panel, scale) for n, c in EXPERIMENTS.items()]
    res = pd.DataFrame(results)
    print("\n=== ABLATION RESULTS (reference: ETS MASE = 0.983) ===")
    print(res.to_string(index=False))
    res.to_csv(OUT, index=False)
    print(f"\nSaved -> {OUT}")


if __name__ == "__main__":
    main()