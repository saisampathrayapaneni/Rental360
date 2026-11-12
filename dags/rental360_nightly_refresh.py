"""
Rental360 — Airflow DAG: nightly refresh of the warehouse and forecast.

Schedule: daily at 02:00 UTC.
Pipeline: generate synthetic data (seeded) -> ETL -> dbt-style models ->
data-quality checks -> retrain forecast -> refresh forecast artifact.

This DAG is the production-shaped orchestration for the project. Airflow is
NOT required to run the app locally — the same steps run as plain commands
(see README "Pipeline"). To run the DAG for real:

    pip install apache-airflow==2.9.3
    airflow dags list   # with this file on the Airflow dags path
    # or: airflow dags trigger rental360_nightly_refresh

Each task shells out to the repo's own scripts, so the DAG stays thin and
the logic stays testable outside Airflow.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator

REPO = "/opt/rental360"  # adjust to your checkout path
VENV_PY = f"{REPO}/.venv/bin/python"

default_args = {
    "owner": "rental360",
    "depends_on_past": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "start_date": datetime(2026, 11, 12),
}

with DAG(
    dag_id="rental360_nightly_refresh",
    default_args=default_args,
    description="Nightly synthetic-data refresh, ETL, models, quality checks, forecast retrain",
    schedule_interval="0 2 * * *",
    catchup=False,
    tags=["rental360", "etl", "forecast"],
) as dag:

    generate = BashOperator(
        task_id="generate_synthetic_data",
        bash_command=f"cd {REPO} && {VENV_PY} data/generate_data.py",
    )

    etl = BashOperator(
        task_id="load_warehouse",
        bash_command=f"cd {REPO} && {VENV_PY} etl/load_warehouse.py",
    )

    models = BashOperator(
        task_id="run_dbt_models",
        bash_command=f"cd {REPO} && {VENV_PY} models/run_models.py",
    )

    quality = BashOperator(
        task_id="data_quality_checks",
        bash_command=f"cd {REPO} && {VENV_PY} etl/data_quality.py",
    )

    train = BashOperator(
        task_id="retrain_forecast",
        bash_command=f"cd {REPO} && {VENV_PY} ml/train.py",
    )

    anomalies = BashOperator(
        task_id="detect_anomalies",
        bash_command=f"cd {REPO} && {VENV_PY} ml/anomaly.py",
    )

    generate >> etl >> models >> quality >> [train, anomalies]
