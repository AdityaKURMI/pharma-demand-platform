"""
LOE chapter, Step 2: Price-erosion analysis (definition (b): cost per rx).

For each LOE candidate that carries real Medicaid volume in our panel:
  1. Normalize Orange Book ingredient names with the SAME strip_salts()
     used for the crosswalk (single source of truth for name identity).
  2. Join to the warehouse at molecule level; require pre-LOE volume
     >= MIN_PRE_RX prescriptions/quarter (averaged) across CA+TX+NY.
  3. Build event-time series: quarters t = -8..+8 relative to the LOE
     quarter, tracking cost_per_rx = amount_reimbursed / prescriptions
     (pooled across states), INDEXED to the pre-LOE mean (t in -4..-1)
     so every drug starts at ~1.0 and curves are comparable.
  4. Fit a pooled exponential decay to the post-LOE index:
        price_index(t) = floor + (1 - floor) * exp(-rate * t)
     reporting rate, implied floor, and half-life in quarters.

Outputs:
  data/modeling/loe_event_panel.parquet   (event-time series, all events)
  data/modeling/loe_fit_summary.csv       (per-drug + pooled fit results)
  printed cohort summary

Run: python analyze_loe_erosion.py
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from normalize_ingredients import strip_salts

WAREHOUSE = "warehouse/pharma.duckdb"
CANDIDATES = "data/reference/orange_book/loe_candidates.parquet"
OUT_DIR = Path("data/modeling")

MIN_PRE_RX = 10_000        # avg prescriptions/quarter pre-LOE, 3 states pooled
EVENT_WINDOW = range(-8, 9)


def decay(t, rate, floor):
    return floor + (1 - floor) * np.exp(-rate * t)


def main() -> None:
    cand = pd.read_parquet(CANDIDATES)
    cand["ingredient_norm"] = cand["ingredient"].map(strip_salts)

    con = duckdb.connect(WAREHOUSE, read_only=True)
    fact = con.execute("""
        SELECT d.ingredient, f.year, f.quarter,
               (f.year - 2018) * 4 + f.quarter - 1 AS quarter_idx,
               SUM(f.prescriptions)      AS rx,
               SUM(f.amount_reimbursed)  AS amt
        FROM fact_utilization f JOIN dim_drug d USING (drug_key)
        GROUP BY 1, 2, 3, 4
    """).df()

    rows, fits = [], []
    for _, ev in cand.iterrows():
        g = fact[fact["ingredient"] == ev["ingredient_norm"]].copy()
        if g.empty:
            continue
        g["t"] = g["quarter_idx"] - ev["loe_quarter_idx"]
        g = g[g["t"].isin(EVENT_WINDOW)]

        pre = g[g["t"].between(-4, -1)]
        if len(pre) < 3 or pre["rx"].mean() < MIN_PRE_RX:
            continue

        pre_price = (pre["amt"].sum() / pre["rx"].sum())
        if not np.isfinite(pre_price) or pre_price <= 0:
            continue

        g["cost_per_rx"] = g["amt"] / g["rx"]
        g["price_index"] = g["cost_per_rx"] / pre_price
        g["ingredient_norm"] = ev["ingredient_norm"]
        g["loe_quarter_idx"] = ev["loe_quarter_idx"]
        rows.append(g[["ingredient_norm", "t", "rx", "amt",
                       "cost_per_rx", "price_index", "loe_quarter_idx"]])

        # per-drug fit on post-LOE points
        post = g[(g["t"] >= 0) & g["price_index"].notna()]
        if len(post) >= 4:
            try:
                (rate, floor), _ = curve_fit(
                    decay, post["t"], post["price_index"],
                    p0=[0.3, 0.3], bounds=([0.0, 0.0], [5.0, 1.5]), maxfev=5000)
                fits.append({"ingredient": ev["ingredient_norm"],
                             "pre_rx_per_q": round(pre["rx"].mean()),
                             "pre_price": round(pre_price, 2),
                             "erosion_rate": round(rate, 3),
                             "price_floor": round(floor, 3),
                             "half_life_q": round(np.log(2) / rate, 2)
                                            if rate > 0 else np.inf,
                             "n_post_points": len(post)})
            except RuntimeError:
                pass

    panel = pd.concat(rows, ignore_index=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(OUT_DIR / "loe_event_panel.parquet", index=False)

    fit_df = pd.DataFrame(fits).sort_values("pre_rx_per_q", ascending=False)
    n_events = panel["ingredient_norm"].nunique()
    print(f"LOE events surviving volume filter (>= {MIN_PRE_RX:,} rx/q pre-LOE): {n_events}")
    print(f"\n=== Per-drug erosion fits (top by volume) ===")
    print(fit_df.head(15).to_string(index=False))

    # pooled fit + mean trajectory
    pooled = panel[(panel["t"] >= 0) & panel["price_index"].notna()]
    (rate, floor), _ = curve_fit(decay, pooled["t"], pooled["price_index"],
                                 p0=[0.3, 0.3], bounds=([0.0, 0.0], [5.0, 1.5]),
                                 maxfev=5000)
    print(f"\n=== Pooled erosion curve ({n_events} events) ===")
    print(f"rate={rate:.3f}/quarter | implied floor={floor:.1%} of pre-LOE price "
          f"| half-life={np.log(2)/rate:.1f} quarters")

    traj = (panel.groupby("t")["price_index"]
            .agg(["mean", "median", "count"]).round(3))
    print("\n=== Mean/median price index by event-time quarter ===")
    print(traj.to_string())

    fit_df.to_csv(OUT_DIR / "loe_fit_summary.csv", index=False)
    print(f"\nSaved -> {OUT_DIR / 'loe_event_panel.parquet'} and loe_fit_summary.csv")


if __name__ == "__main__":
    main()