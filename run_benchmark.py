"""
Phase 4, Step 2: The forecasting benchmark.

Ladder of models, each evaluated identically on the 4 rolling-origin folds
(1-step-ahead, quarters 20-23), metrics computed on the ORIGINAL scale:

  1. naive           y_hat = last observed value          (floor baseline)
  2. seasonal_naive  y_hat = value 4 quarters ago         (the MASE reference)
  3. ets             per-series Holt-Winters (statsmodels), additive trend
                     + seasonality where possible, fit on log scale
  4. lgbm_global     ONE LightGBM across all 1,448 series, using the panel
                     features (lags, rolling stats, quarter dummies, covid
                     flag, state/ingredient as categoricals)

Metrics:
  - MASE: MAE scaled by the in-sample MAE of seasonal-naive on the
    training portion of that series. < 1.0 = beats seasonal naive.
  - sMAPE: symmetric MAPE, scale-free, bounded [0, 200].

Output: results table printed + saved to data/modeling/benchmark_results.csv
        and per-prediction detail to data/modeling/predictions.parquet

Run from repo root (venv):  python run_benchmark.py
Requires: pip install lightgbm statsmodels
"""

import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

PANEL = "data/modeling/panel.parquet"
OUT_DIR = Path("data/modeling")

FEATURES = [
    "y_lag1", "y_lag2", "y_lag3", "y_lag4", "y_lag8",
    "y_rollmean4", "y_rollstd4",
    "covid_shock", "q2", "q3", "q4",
]
CATEGORICALS = ["state", "ingredient"]
N_FOLDS = 4


# ── metrics (original scale) ─────────────────────────────────────────────
def smape(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    denom = (np.abs(y_true) + np.abs(y_pred)) / 2
    ok = denom > 0
    return float(100.0 * np.mean(np.abs(y_true[ok] - y_pred[ok]) / denom[ok]))


def mase_scale_factors(panel: pd.DataFrame) -> pd.Series:
    """Per-series scale: in-sample MAE of seasonal-naive over the training
    region (quarter_idx < 20), on the original scale."""
    def factor(g: pd.DataFrame) -> float:
        tr = g[g["quarter_idx"] < 24 - N_FOLDS].sort_values("quarter_idx")
        vals = tr["prescriptions"].to_numpy(dtype=float)
        if len(vals) <= 4:
            return np.nan
        diffs = np.abs(vals[4:] - vals[:-4])
        diffs = diffs[~np.isnan(diffs)]
        return float(np.mean(diffs)) if len(diffs) else np.nan

    return panel.groupby(["state", "ingredient"]).apply(factor)


# ── models ───────────────────────────────────────────────────────────────
def predict_naive(panel: pd.DataFrame, test_q: int, seasonal: bool) -> pd.DataFrame:
    lag = 4 if seasonal else 1
    src = panel[panel["quarter_idx"] == test_q - lag][
        ["state", "ingredient", "prescriptions"]
    ].rename(columns={"prescriptions": "y_pred"})
    tgt = panel[panel["quarter_idx"] == test_q][["state", "ingredient", "prescriptions"]]
    return tgt.merge(src, on=["state", "ingredient"], how="left")


def predict_ets(panel: pd.DataFrame, test_q: int) -> pd.DataFrame:
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    rows = []
    for (state, ing), g in panel.groupby(["state", "ingredient"], sort=False):
        g = g.sort_values("quarter_idx")
        train = g[g["quarter_idx"] < test_q]["y"].to_numpy(dtype=float)
        actual_row = g[g["quarter_idx"] == test_q]
        if actual_row.empty:
            continue
        train_clean = train[~np.isnan(train)]
        pred = np.nan
        if len(train_clean) >= 10 and len(train_clean) == len(train):
            try:
                fit = ExponentialSmoothing(
                    train, trend="add", seasonal="add", seasonal_periods=4,
                    initialization_method="estimated",
                ).fit(optimized=True)
                pred = float(np.expm1(fit.forecast(1)[0]))
            except Exception:
                pred = np.nan
        if np.isnan(pred) and len(train_clean):
            pred = float(np.expm1(train_clean[-1]))          # fallback: naive
        rows.append({
            "state": state, "ingredient": ing,
            "prescriptions": actual_row["prescriptions"].iloc[0],
            "y_pred": max(pred, 0.0),
        })
    return pd.DataFrame(rows)


def predict_lgbm(panel: pd.DataFrame, test_q: int) -> pd.DataFrame:
    import lightgbm as lgb

    df = panel.copy()
    for c in CATEGORICALS:
        df[c] = df[c].astype("category")

    train = df[(df["quarter_idx"] < test_q) & df["y"].notna() & df["y_lag1"].notna()]
    test = df[df["quarter_idx"] == test_q]

    model = lgb.LGBMRegressor(
        n_estimators=600, learning_rate=0.05, num_leaves=63,
        subsample=0.9, colsample_bytree=0.9, random_state=42, verbose=-1,
    )
    model.fit(train[FEATURES + CATEGORICALS], train["y"],
              categorical_feature=CATEGORICALS)

    preds = np.expm1(model.predict(test[FEATURES + CATEGORICALS]))
    out = test[["state", "ingredient", "prescriptions"]].copy()
    out["y_pred"] = np.clip(preds, 0, None)
    return out


# ── evaluation loop ──────────────────────────────────────────────────────
def main() -> None:
    panel = pd.read_parquet(PANEL)
    scale = mase_scale_factors(panel).rename("mase_scale")

    models = {
        "naive": lambda p, q: predict_naive(p, q, seasonal=False),
        "seasonal_naive": lambda p, q: predict_naive(p, q, seasonal=True),
        "ets": predict_ets,
        "lgbm_global": predict_lgbm,
    }

    all_preds, results = [], []
    for name, fn in models.items():
        fold_frames = []
        for k in range(N_FOLDS):
            test_q = 24 - N_FOLDS + k
            preds = fn(panel, test_q)
            preds["fold"], preds["model"] = k, name
            fold_frames.append(preds)
            print(f"[{name}] fold {k} (quarter_idx={test_q}): {len(preds):,} predictions")

        dfm = pd.concat(fold_frames, ignore_index=True)
        dfm = dfm.merge(scale.reset_index(), on=["state", "ingredient"], how="left")
        dfm = dfm[dfm["prescriptions"].notna() & dfm["y_pred"].notna()]

        ae = np.abs(dfm["prescriptions"] - dfm["y_pred"])
        dfm["ase"] = ae / dfm["mase_scale"]
        mase = float(dfm.loc[dfm["mase_scale"] > 0, "ase"].mean())
        s = smape(dfm["prescriptions"].to_numpy(dtype=float),
                  dfm["y_pred"].to_numpy(dtype=float))
        results.append({"model": name, "MASE": round(mase, 3),
                        "sMAPE": round(s, 2), "n_preds": len(dfm)})
        all_preds.append(dfm)

    res = pd.DataFrame(results)
    print("\n=== BENCHMARK RESULTS (4 rolling-origin folds, original scale) ===")
    print(res.to_string(index=False))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT_DIR / "benchmark_results.csv", index=False)
    pd.concat(all_preds, ignore_index=True).to_parquet(
        OUT_DIR / "predictions.parquet", index=False)
    print(f"\nSaved -> {OUT_DIR / 'benchmark_results.csv'} and predictions.parquet")


if __name__ == "__main__":
    main()