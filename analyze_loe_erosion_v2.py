"""
LOE chapter, Step 3 (v2): Re-anchor erosion analysis on OBSERVED generic
entry instead of ANDA approval dates (fixes finding #19's proxy problem).

Method:
  1. For each of the 134 Orange Book candidates, compute the molecule's
     quarterly GENERIC SHARE of prescriptions directly from staged SDUD
     joined to the brand/generic-tagged crosswalk.
  2. Observed entry = first quarter where generic share >= ENTRY_SHARE
     (10%). This is the market event, not the regulatory event.
  3. Cohorts:
       LAUNCHED   — entry observed with >= 4 quarters of runway both sides
       UNLAUNCHED — generic share never crosses threshold in the panel
                    (retained as the contrast group)
  4. Re-fit price erosion (decay to floor) on the launched cohort in
     entry-anchored event time; report the unlaunched cohort's price
     drift over the same calendar window as contrast.

Outputs printed + data/modeling/loe_v2_fits.csv, loe_v2_panel.parquet

Run: python analyze_loe_erosion_v2.py
"""

from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
from scipy.optimize import curve_fit

from normalize_ingredients import strip_salts

STAGED = "data/staging/sdud/year=*/state=*/sdud.parquet"
XWALK = "data/reference/ndc_crosswalk/ndc_crosswalk_enriched.parquet"
CANDIDATES = "data/reference/orange_book/loe_candidates.parquet"
OUT_DIR = Path("data/modeling")

ENTRY_SHARE = 0.10
MIN_PRE_RX = 10_000
MIN_RUNWAY = 4          # quarters required on each side of entry


def decay(t, rate, floor):
    return floor + (1 - floor) * np.exp(-rate * t)


def main() -> None:
    cand = pd.read_parquet(CANDIDATES)
    cand["ingredient_norm"] = cand["ingredient"].map(strip_salts)
    targets = set(cand["ingredient_norm"])

    con = duckdb.connect()
    q = con.execute(f"""
        SELECT
            x.ingredient,
            (s.year - 2018) * 4 + s.quarter - 1                 AS quarter_idx,
            SUM(s.number_of_prescriptions)                      AS rx,
            SUM(s.total_amount_reimbursed)                      AS amt,
            SUM(CASE WHEN x.brand_generic = 'generic'
                     THEN s.number_of_prescriptions ELSE 0 END) AS rx_generic,
            SUM(CASE WHEN x.brand_generic = 'brand'
                     THEN s.number_of_prescriptions ELSE 0 END) AS rx_brand
        FROM read_parquet('{STAGED}') s
        JOIN '{XWALK}' x ON SUBSTR(s.ndc, 1, 9) = x.ndc9
        WHERE NOT s.suppressed
        GROUP BY 1, 2
    """).df()
    q = q[q["ingredient"].isin(targets)].copy()
    q["generic_share"] = q["rx_generic"] / q["rx"]

    fits, panel_rows, unlaunched = [], [], []
    for ing, g in q.groupby("ingredient"):
        g = g.sort_values("quarter_idx").reset_index(drop=True)
        crossed = g[g["generic_share"] >= ENTRY_SHARE]

        if crossed.empty:
            unlaunched.append(ing)
            continue
        entry_q = int(crossed["quarter_idx"].iloc[0])
        if not (MIN_RUNWAY <= entry_q <= 23 - MIN_RUNWAY):
            continue                          # entry too close to panel edge
        # skip molecules already generic at panel start (not a fresh entry)
        pre_share = g[g["quarter_idx"] < entry_q]["generic_share"]
        if len(pre_share) and pre_share.iloc[0] >= ENTRY_SHARE:
            continue

        g["t"] = g["quarter_idx"] - entry_q
        pre = g[g["t"].between(-MIN_RUNWAY, -1)]
        if pre["rx"].sum() == 0 or pre["rx"].mean() < MIN_PRE_RX:
            continue
        pre_price = pre["amt"].sum() / pre["rx"].sum()
        if not np.isfinite(pre_price) or pre_price <= 0:
            continue

        g["price_index"] = (g["amt"] / g["rx"]) / pre_price
        g["ingredient_norm"], g["entry_q"] = ing, entry_q
        panel_rows.append(g)

        post = g[(g["t"] >= 0) & g["price_index"].notna()]
        if len(post) >= 4:
            try:
                (rate, floor), _ = curve_fit(
                    decay, post["t"], post["price_index"],
                    p0=[0.5, 0.3], bounds=([0.0, 0.0], [5.0, 1.5]), maxfev=5000)
                fits.append({
                    "ingredient": ing, "entry_quarter_idx": entry_q,
                    "pre_rx_per_q": round(pre["rx"].mean()),
                    "pre_price": round(pre_price, 2),
                    "share_at_entry": round(
                        float(crossed["generic_share"].iloc[0]), 3),
                    "share_end": round(float(g["generic_share"].iloc[-1]), 3),
                    "erosion_rate": round(rate, 3),
                    "price_floor": round(floor, 3),
                    "half_life_q": round(np.log(2) / rate, 2) if rate > 0 else np.inf,
                })
            except RuntimeError:
                pass

    fit_df = pd.DataFrame(fits).sort_values("pre_rx_per_q", ascending=False)
    print(f"=== LAUNCHED cohort (observed entry, share >= {ENTRY_SHARE:.0%}, "
          f"runway >= {MIN_RUNWAY}q, volume >= {MIN_PRE_RX:,}/q): {len(fit_df)} ===")
    print(fit_df.to_string(index=False))

    if panel_rows:
        panel = pd.concat(panel_rows, ignore_index=True)
        pooled = panel[(panel["t"] >= 0) & panel["price_index"].notna()]
        (rate, floor), _ = curve_fit(decay, pooled["t"], pooled["price_index"],
                                     p0=[0.5, 0.3],
                                     bounds=([0.0, 0.0], [5.0, 1.5]), maxfev=5000)
        print(f"\n=== Pooled (launched only): rate={rate:.3f}/q | "
              f"floor={floor:.1%} | half-life={np.log(2)/rate:.1f}q ===")
        traj = panel.groupby("t")["price_index"].agg(["mean", "median", "count"]).round(3)
        print(traj.to_string())
        panel.to_parquet(OUT_DIR / "loe_v2_panel.parquet", index=False)

    # contrast group: approved but never launched in panel
    un = q[q["ingredient"].isin(unlaunched)]
    if len(un):
        drift = (un.sort_values("quarter_idx").groupby("ingredient")
                 .apply(lambda g: g["amt"].iloc[-4:].sum() / g["rx"].iloc[-4:].sum()
                        / (g["amt"].iloc[:4].sum() / g["rx"].iloc[:4].sum())
                        if g["rx"].iloc[:4].sum() > 0 and g["rx"].iloc[-4:].sum() > 0
                        else np.nan)
                 .dropna())
        big = un.groupby("ingredient")["rx"].mean()
        drift = drift[big[drift.index] >= MIN_PRE_RX]
        print(f"\n=== UNLAUNCHED contrast (never crossed {ENTRY_SHARE:.0%} share; "
              f"volume-filtered): {len(drift)} molecules ===")
        print("price ratio (last year / first year of panel):")
        print(drift.round(3).sort_values(ascending=False).to_string())

    fit_df.to_csv(OUT_DIR / "loe_v2_fits.csv", index=False)
    print(f"\nSaved -> {OUT_DIR / 'loe_v2_fits.csv'}")


if __name__ == "__main__":
    main()