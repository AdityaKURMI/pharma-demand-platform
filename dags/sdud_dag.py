"""
DAG: sdud_pipeline (v2 — Phase 2 complete)

Two parallel branches converging on a shared quality gate:

  SDUD branch:        ingest_sdud_raw -> stage_sdud_typed ----------\
                                                                     >-> validate_quality
  Reference branch:   ingest_fda_ndc -> resolve_rxnav                /
                        -> build_crosswalk -> normalize_ingredients /

Why parallel: the FDA/RxNorm reference chain has no dependency on SDUD
staging (RxNav resolution reads staged SDUD to find unmatched codes, so
resolve_rxnav actually needs staging too — see cross-branch edge below).

All heavy steps are idempotent/checkpointed, so re-runs are cheap:
  - SDUD ingest skips existing partitions
  - RxNav resolver + ingredient normalizer resume from checkpoints
  - FDA ingest re-downloads (bulk file, ~30s) — acceptable; could add
    an If-Modified-Since check later
"""

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

REPO = "/opt/airflow/repo"
YEARS = "2020 2021 2022 2023"
STATES = "CA TX NY"

default_args = {
    "owner": "aditya",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="sdud_pipeline",
    description="SDUD ingestion + FDA/RxNorm drug reference chain + quality gate",
    default_args=default_args,
    schedule=None,                      # manual trigger while developing
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["sdud", "ingestion", "reference"],
) as dag:

    # ── SDUD branch ──────────────────────────────────────────────
    ingest_sdud = BashOperator(
        task_id="ingest_sdud_raw",
        bash_command=f"cd {REPO} && python ingest_sdud.py --years {YEARS} --states {STATES}",
    )

    stage_sdud = BashOperator(
        task_id="stage_sdud_typed",
        bash_command=f"cd {REPO} && python stage_sdud.py",
    )

    # ── Reference-data branch ────────────────────────────────────
    ingest_fda = BashOperator(
        task_id="ingest_fda_ndc",
        bash_command=f"cd {REPO} && python ingest_fda_ndc.py",
    )

    resolve_rxnav = BashOperator(
        task_id="resolve_rxnav",
        bash_command=f"cd {REPO} && python resolve_ndc_rxnav.py",
        # long API batch job; give it one extra retry
        retries=3,
    )

    build_crosswalk = BashOperator(
        task_id="build_crosswalk",
        bash_command=f"cd {REPO} && python build_ndc_crosswalk.py",
    )

    normalize_ingredients = BashOperator(
        task_id="normalize_ingredients",
        bash_command=f"cd {REPO} && python normalize_ingredients.py",
    )

    # ── Shared quality gate ──────────────────────────────────────
    validate = BashOperator(
        task_id="validate_quality",
        bash_command=f"cd {REPO} && python validate_sdud.py",
    )

    # ── Dependencies ─────────────────────────────────────────────
    # SDUD chain
    ingest_sdud >> stage_sdud

    # Reference chain
    ingest_fda >> resolve_rxnav >> build_crosswalk >> normalize_ingredients

    # Cross-branch edge: the RxNav resolver reads STAGED SDUD to compute
    # the unmatched-code list, so it needs staging done first.
    stage_sdud >> resolve_rxnav

    # Both branches must finish before validation
    [stage_sdud, normalize_ingredients] >> validate