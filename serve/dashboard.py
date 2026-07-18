"""
Dashboard: Streamlit app, four views on top of the forecast API and the
frozen analysis artifacts.

  1. Drug Explorer   — history + ETS forecast with bands (calls the API)
  2. LOE Gallery     — erosion curves in event time + unlaunched contrast
  3. Benchmark       — the frozen model comparison, ablations, tier slice
  4. Data Quality    — coverage, suppression, pipeline facts

Run (repo root, venv, API running in another terminal):
    python -m pip install streamlit plotly
    python -m streamlit run serve/dashboard.py
"""

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

API = "http://127.0.0.1:8000"
WAREHOUSE = "warehouse/pharma.duckdb"

st.set_page_config(page_title="Pharma Demand Platform", layout="wide")
st.title("Pharma Demand Forecasting Platform")
st.caption("US Medicaid claims (CA/TX/NY, 2018–2023) · ETS forecasts · "
           "LOE erosion analysis · fully reproducible pipeline")

view = st.sidebar.radio("View", ["Drug Explorer", "LOE Gallery",
                                 "Benchmark", "Data Quality"])


# ── 1. Drug Explorer ─────────────────────────────────────────────────────
if view == "Drug Explorer":
    c1, c2, c3 = st.columns([1, 2, 1])
    state = c1.selectbox("State", ["CA", "TX", "NY"])
    try:
        drugs = requests.get(f"{API}/drugs", params={"state": state},
                             timeout=30).json()
    except requests.RequestException:
        st.error("API not reachable — start it with: "
                 "`python -m uvicorn serve.api:app`")
        st.stop()
    ingredient = c2.selectbox("Ingredient (by volume)",
                              [d["ingredient"] for d in drugs])
    horizon = c3.slider("Forecast horizon (quarters)", 1, 8, 4)

    fc = requests.get(f"{API}/forecast",
                      params={"state": state, "ingredient": ingredient,
                              "horizon": horizon}, timeout=60).json()

    hist = pd.DataFrame(fc["history"]).dropna(subset=["prescriptions"])
    fut = pd.DataFrame(fc["forecast"])
    fig = go.Figure()
    fig.add_scatter(x=hist["quarter_idx"], y=hist["prescriptions"],
                    mode="lines+markers", name="history")
    fig.add_scatter(x=fut["quarter_idx"], y=fut["forecast_rx"],
                    mode="lines+markers", name="ETS forecast",
                    line=dict(dash="dash"))
    fig.add_scatter(x=pd.concat([fut["quarter_idx"], fut["quarter_idx"][::-1]]),
                    y=pd.concat([fut["hi95"], fut["lo95"][::-1]]),
                    fill="toself", opacity=0.2, line=dict(width=0),
                    name="95% band", showlegend=True)
    # event annotations
    fig.add_vrect(x0=8, x1=9.5, opacity=0.08, fillcolor="red",
                  annotation_text="COVID", annotation_position="top left")
    fig.add_vline(x=21, opacity=0.3, line_dash="dot",
                  annotation_text="Medicaid unwinding begins")
    fig.update_layout(xaxis_title="quarter_idx (0 = Q1-2018)",
                      yaxis_title="prescriptions / quarter",
                      title=f"{ingredient} — {state}")
    st.plotly_chart(fig, use_container_width=True)
    st.caption(fc["model"])


# ── 2. LOE Gallery ───────────────────────────────────────────────────────
elif view == "LOE Gallery":
    st.subheader("Price erosion after observed generic entry (7 launched events)")
    panel = pd.read_parquet("data/modeling/loe_v2_panel.parquet")
    fig = go.Figure()
    for ing, g in panel.groupby("ingredient_norm"):
        g = g.sort_values("t")
        fig.add_scatter(x=g["t"], y=g["price_index"], mode="lines",
                        name=ing, opacity=0.5)
    med = panel[panel["t"].between(-8, 12)].groupby("t")["price_index"].median()
    fig.add_scatter(x=med.index, y=med.values, mode="lines+markers",
                    name="MEDIAN", line=dict(width=4, color="black"))
    fig.add_vline(x=0, line_dash="dot", annotation_text="observed entry")
    fig.update_layout(xaxis_title="quarters since generic entry",
                      yaxis_title="price index (pre-entry = 1.0)",
                      yaxis_range=[0, 2])
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("Contrast: approved-but-UNLAUNCHED brands kept raising prices")
    contrast = pd.DataFrame({
        "molecule": ["empagliflozin", "apixaban", "linaclotide", "linagliptin",
                     "brexpiprazole", "budesonide;formoterol", "cariprazine",
                     "fluticasone;salmeterol"],
        "price_ratio_end_vs_start": [2.149, 1.992, 1.952, 1.894,
                                     1.572, 1.501, 1.322, 1.134]})
    st.bar_chart(contrast.set_index("molecule"))
    st.caption("Gross (pre-rebate) prices. Finding #22: patent settlements "
               "make approval ≠ launch — these molecules' generics were "
               "approved but never entered during the panel.")


# ── 3. Benchmark ─────────────────────────────────────────────────────────
elif view == "Benchmark":
    st.subheader("Frozen benchmark: 4 rolling-origin folds, 1,448 series")
    st.dataframe(pd.read_csv("data/modeling/benchmark_results.csv"),
                 hide_index=True)
    st.markdown("**Ablations (one change at a time; reference ETS = 0.983):**")
    st.dataframe(pd.read_csv("data/modeling/ablation_results.csv"),
                 hide_index=True)

    st.markdown("**MASE by volume tier** — ETS's edge is largest on the "
                "high-volume drugs commercial teams care about (finding #18):")
    p = pd.read_parquet("data/modeling/predictions.parquet")
    p = p[p.mase_scale > 0].copy()
    p["ase"] = (p.prescriptions - p.y_pred).abs() / p.mase_scale
    vol = (p.groupby(["state", "ingredient"])["prescriptions"]
           .sum().rename("vol").reset_index())
    p = p.merge(vol, on=["state", "ingredient"])
    p["tier"] = pd.qcut(p["vol"], q=[0, .5, .9, 1],
                        labels=["small", "mid", "large"])
    st.dataframe(p.groupby(["model", "tier"], observed=True)["ase"]
                 .mean().unstack().round(3))
    st.caption("Headline: per-series ETS (MASE 0.983) beats the best global "
               "GBM (1.080) under a test period containing the 2023 Medicaid "
               "unwinding volatility. LOE regime features: pre-registered "
               "null (finding #23).")


# ── 4. Data Quality ──────────────────────────────────────────────────────
else:
    st.subheader("Pipeline & data quality")
    con = duckdb.connect(WAREHOUSE, read_only=True)
    c1, c2, c3, c4 = st.columns(4)
    facts = con.execute("SELECT COUNT(*), COUNT(DISTINCT drug_key) "
                        "FROM fact_utilization").fetchone()
    c1.metric("Fact rows", f"{facts[0]:,}")
    c2.metric("Molecules", f"{facts[1]:,}")
    c3.metric("NDC volume resolved", "> 99.99%")
    c4.metric("dbt tests passing", "11 / 11")

    st.markdown("**Suppression is structural, not random** (finding #4): "
                "privacy redaction concentrates in each state's minor "
                "payment channel.")
    sup = pd.DataFrame({
        "state": ["CA", "CA", "NY", "NY", "TX", "TX"],
        "channel": ["FFS", "MCO"] * 3,
        "pct_suppressed": [31.4, 54.9, 31.9, 51.7, 76.4, 34.6]})
    st.dataframe(sup, hide_index=True)

    st.markdown("**Payment architecture differs radically by state** "
                "(finding #7) — the reason per-state modeling matters:")
    arch = pd.DataFrame({
        "state": ["CA", "NY", "TX"],
        "FFS share of rx": ["91.8%", "74.7%", "1.1%"],
        "context": ["Medi-Cal Rx carve-out (2022)",
                    "NYRx carve-out mid-2023 (structural break detected)",
                    "fully managed care"]})
    st.dataframe(arch, hide_index=True)
    st.caption("Full findings log: notes.md (23 findings). "
               "Pipeline: Airflow DAG, 11 tasks, idempotent end-to-end.")