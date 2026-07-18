"""
DAG: sdud_pipeline (v3 — full platform)

The complete graph, raw sources -> modeling dataset:

  ingest_sdud_raw ─► stage_sdud_typed ─────────────┬──────────────────► validate_quality
                            │                      │                          │
  ingest_fda_ndc ───────────┴► resolve_rxnav ─► build_crosswalk ─►            ▼
                                                normalize_ingredients ─► dbt_build
                                                       │                      │
  ingest_orange_book ──────────────────────────────────┴──────────► loe_analysis
                                                                          │
                                                                          ▼
                                                              build_modeling_dataset

Notes:
- ingest_orange_book fails gracefully with a clear message if the FDA zip
  hasn't been manually placed (fda.gov bot protection — see notes.md).
- dbt runs INSIDE the container via `python -m dbt.cli.main` (the dbt.exe
  shim issue is a Windows-host problem; in the Linux container this is
  simply the robust invocation).
- All heavy tasks are idempotent/checkpointed; a full re-run of this DAG
  on already-ingested data completes in a few minutes.
"""

from datetime import timedelta

import pendulum
from airflow import DAG
from airflow.providers.standard.operators.bash import BashOperator

REPO = "/opt/airflow/repo"
WH = f"{REPO}/warehouse"
YEARS = "2018 2019 2020 2021 2022 2023"
STATES = "CA TX NY"

default_args = {
    "owner": "aditya",
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
}

with DAG(
    dag_id="sdud_pipeline",
    description="Full pharma demand platform: sources -> warehouse -> modeling dataset",
    default_args=default_args,
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["sdud", "reference", "warehouse", "modeling"],
) as dag:

    # ── ingestion branches ───────────────────────────────────────
    ingest_sdud = BashOperator(
        task_id="ingest_sdud_raw",
        bash_command=f"cd {REPO} && python ingest_sdud.py --years {YEARS} --states {STATES}",
    )
    stage_sdud = BashOperator(
        task_id="stage_sdud_typed",
        bash_command=f"cd {REPO} && python stage_sdud.py",
    )
    ingest_fda = BashOperator(
        task_id="ingest_fda_ndc",
        bash_command=f"cd {REPO} && python ingest_fda_ndc.py",
    )
    ingest_orange_book = BashOperator(
        task_id="ingest_orange_book",
        bash_command=(
            f"cd {REPO} && "
            "if [ ! -f data/reference/orange_book/EOBZIP.zip ]; then "
            "echo 'ERROR: Orange Book zip missing. fda.gov blocks scripted "
            "downloads; fetch manually from "
            "https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files "
            "and place at data/reference/orange_book/EOBZIP.zip' && exit 1; "
            "fi && python ingest_orange_book.py"
        ),
    )

    # ── reference chain ──────────────────────────────────────────
    resolve_rxnav = BashOperator(
        task_id="resolve_rxnav",
        bash_command=f"cd {REPO} && python resolve_ndc_rxnav.py",
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

    # ── quality gate + warehouse ─────────────────────────────────
    validate = BashOperator(
        task_id="validate_quality",
        bash_command=f"cd {REPO} && python validate_sdud.py",
    )
    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=(
            f"cd {WH} && "
            "python -m dbt.cli.main deps --profiles-dir . && "
            "python -m dbt.cli.main run --profiles-dir . && "
            "python -m dbt.cli.main test --profiles-dir ."
        ),
    )

    # ── analysis + modeling dataset ──────────────────────────────
    loe_analysis = BashOperator(
        task_id="loe_analysis",
        bash_command=f"cd {REPO} && python analyze_loe_erosion_v2.py",
    )
    build_modeling = BashOperator(
        task_id="build_modeling_dataset",
        bash_command=f"cd {REPO} && python build_modeling_dataset.py",
    )

    # ── dependencies ─────────────────────────────────────────────
    ingest_sdud >> stage_sdud
    ingest_fda >> resolve_rxnav
    stage_sdud >> resolve_rxnav                       # cross-branch (unmatched-code list)
    resolve_rxnav >> build_crosswalk >> normalize_ingredients

    [stage_sdud, normalize_ingredients] >> validate
    validate >> dbt_build

    [dbt_build, ingest_orange_book, normalize_ingredients] >> loe_analysis
    loe_analysis >> build_modeling