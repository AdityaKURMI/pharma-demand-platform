"""
DAG: sdud_pipeline
Orchestrates the SDUD flow:  ingest -> stage -> validate

- ingest: pulls raw data from the Medicaid API (idempotent — skips
  partitions already on disk, so re-runs are cheap and safe)
- stage:  raw VARCHAR parquet -> typed, validated, quarantined
- validate: data-quality gate; non-zero exit turns the task red

Retries are now the orchestrator's job: each task retries twice with a
5-minute delay — on top of the per-request retries inside the script.
Two layers of resilience: request-level and task-level.

Schedule: None (trigger manually from the UI while developing).
Later, switch to e.g. "@monthly" to check for fresh quarters automatically.
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
    description="Ingest, stage and validate Medicaid SDUD data",
    default_args=default_args,
    schedule=None,                      # manual trigger for now
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    tags=["sdud", "ingestion"],
) as dag:

    ingest = BashOperator(
        task_id="ingest_raw",
        bash_command=f"cd {REPO} && python ingest_sdud.py --years {YEARS} --states {STATES}",
    )

    stage = BashOperator(
        task_id="stage_typed",
        bash_command=f"cd {REPO} && python stage_sdud.py",
    )

    validate = BashOperator(
        task_id="validate_quality",
        bash_command=f"cd {REPO} && python validate_sdud.py",
    )

    ingest >> stage >> validate