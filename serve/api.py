"""
Serving layer: FastAPI forecast API on top of the warehouse.

Design decisions (each defensible in one sentence):
- Serves ETS, the benchmark WINNER (MASE 0.983), not the model we hoped
  would win. Fit at request time: quarterly ETS on 24 points fits in
  milliseconds, which removes an entire model-artifact subsystem.
- Prediction intervals from in-sample residual std (honest, simple).
- assume_entry_quarter does NOT dampen demand: findings #8 and #21 show
  total molecule demand persists through entry — entry shifts brand/
  generic mix and PRICE. The scenario therefore returns an annotation
  plus an expected price-index trajectory from the median erosion curve.

Run (from repo root, venv active):
    python -m pip install fastapi uvicorn
    python -m uvicorn serve.api:app --reload
Then open http://127.0.0.1:8000/docs for the interactive API explorer.
"""

import warnings

import duckdb
import numpy as np
from fastapi import FastAPI, HTTPException, Query

warnings.filterwarnings("ignore")

WAREHOUSE = "warehouse/pharma.duckdb"

# Median price-index trajectory post-entry (finding #21, t=0..8, trimmed).
EROSION_MEDIAN = [1.0, 0.85, 0.83, 0.85, 0.82, 0.81, 0.86, 0.85, 0.76]

app = FastAPI(
    title="Pharma Demand Forecast API",
    description="Quarterly Medicaid prescription demand forecasts "
                "(CA/TX/NY, 2018-2023 panel). ETS per-series; LOE-aware "
                "scenario annotation.",
    version="1.0",
)


def query_series(state: str, ingredient: str) -> list[dict]:
    con = duckdb.connect(WAREHOUSE, read_only=True)
    rows = con.execute("""
        SELECT f.year, f.quarter,
               (f.year - 2018) * 4 + f.quarter - 1 AS quarter_idx,
               f.prescriptions
        FROM fact_utilization f JOIN dim_drug d USING (drug_key)
        WHERE f.state = ? AND d.ingredient = ?
        ORDER BY quarter_idx
    """, [state, ingredient]).df()
    con.close()
    return rows.to_dict("records")


@app.get("/drugs")
def list_drugs(state: str = Query("CA", pattern="^(CA|TX|NY)$"),
               limit: int = Query(100, le=1000)):
    """Forecastable ingredients for a state, by total volume."""
    con = duckdb.connect(WAREHOUSE, read_only=True)
    rows = con.execute("""
        SELECT d.ingredient, SUM(f.prescriptions) AS total_rx,
               COUNT(*) AS n_quarters
        FROM fact_utilization f JOIN dim_drug d USING (drug_key)
        WHERE f.state = ?
        GROUP BY d.ingredient
        HAVING COUNT(*) >= 16 AND SUM(f.prescriptions) IS NOT NULL
        ORDER BY total_rx DESC
        LIMIT ?
    """, [state, limit]).df()
    con.close()
    return rows.to_dict("records")


@app.get("/forecast")
def forecast(
    state: str = Query(..., pattern="^(CA|TX|NY)$"),
    ingredient: str = Query(..., min_length=2),
    horizon: int = Query(4, ge=1, le=8),
    assume_entry_quarter: int | None = Query(
        None, ge=24, le=40,
        description="Scenario: quarter_idx of assumed future generic entry "
                    "(24 = Q1-2024). Returns annotation + expected price "
                    "trajectory; does NOT alter demand forecast (finding #8/#21)."),
):
    """History + ETS forecast with intervals for one (state, ingredient)."""
    from statsmodels.tsa.holtwinters import ExponentialSmoothing

    history = query_series(state, ingredient.lower())
    obs = [r for r in history if r["prescriptions"] is not None]
    if len(obs) < 12:
        raise HTTPException(404, f"Series {state}/{ingredient} not found "
                                 f"or too short ({len(obs)} quarters; need 12).")

    y = np.log1p([r["prescriptions"] for r in obs])
    try:
        fit = ExponentialSmoothing(
            y, trend="add", seasonal="add", seasonal_periods=4,
            initialization_method="estimated").fit(optimized=True)
    except Exception:
        fit = ExponentialSmoothing(
            y, trend="add", initialization_method="estimated").fit()

    fc_log = np.asarray(fit.forecast(horizon))
    resid_std = float(np.std(fit.resid)) if len(fit.resid) else 0.0
    last_q = obs[-1]["quarter_idx"]

    points = []
    for h in range(horizon):
        qi = last_q + 1 + h
        band = 1.96 * resid_std * np.sqrt(h + 1)   # widening with horizon
        points.append({
            "quarter_idx": qi,
            "year": 2018 + qi // 4,
            "quarter": qi % 4 + 1,
            "forecast_rx": round(float(np.expm1(fc_log[h]))),
            "lo95": round(float(np.expm1(fc_log[h] - band))),
            "hi95": round(float(np.expm1(fc_log[h] + band))),
        })

    resp = {
        "state": state, "ingredient": ingredient.lower(),
        "model": "ETS (Holt-Winters, log scale) — benchmark winner, MASE 0.983",
        "history": history, "forecast": points,
    }

    if assume_entry_quarter is not None:
        resp["loe_scenario"] = {
            "assumed_entry_quarter_idx": assume_entry_quarter,
            "note": ("Demand forecast unchanged: total molecule demand "
                     "persists through generic entry (findings #8, #21); "
                     "entry shifts brand/generic mix and price."),
            "expected_price_index": [
                {"quarters_after_entry": t, "price_index": v}
                for t, v in enumerate(EROSION_MEDIAN)
            ],
            "price_caveat": "Gross (pre-rebate) index from a 7-event "
                            "median trajectory; see paper limitations.",
        }
    return resp