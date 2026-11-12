"""Rental360 — ETL tests: cleaning rules, edge cases, data-quality checks.

Runs against small synthetic frames (no DB dependency for the cleaning
tests) plus the real warehouse for the quality-check suite.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from etl.load_warehouse import clean_equipment, clean_rentals  # noqa: E402

DB = ROOT / "data" / "warehouse" / "rental360.db"


def _equipment_frame() -> pd.DataFrame:
    return pd.DataFrame([
        {"equipment_id": "EQ-1", "equipment_name": "Cat 320", "category": "Excavator",
         "make": "Caterpillar", "model": "320", "year": 2021, "daily_rate": "300",
         "purchase_date": "2021-03-01", "branch_id": "BR-01", "status": "available"},
        {"equipment_id": "EQ-2", "equipment_name": "Toyota 8FGU25", "category": "Forklift",
         "make": "Toyota", "model": "8FGU25", "year": 2022, "daily_rate": "120",
         "purchase_date": "2022-01-15", "branch_id": "BR-02", "status": "RENTED"},
        # duplicate id + invalid status: both must be handled
        {"equipment_id": "EQ-2", "equipment_name": "Toyota 8FGU25", "category": "Forklift",
         "make": "Toyota", "model": "8FGU25", "year": 2022, "daily_rate": "120",
         "purchase_date": "2022-01-15", "branch_id": "BR-02", "status": "rented"},
        {"equipment_id": "EQ-3", "equipment_name": "Mystery Unit", "category": "Crane",
         "make": "X", "model": "Y", "year": 2020, "daily_rate": "500",
         "purchase_date": "2020-06-01", "branch_id": "BR-01", "status": "flying"},
    ])


def _rentals_frame() -> pd.DataFrame:
    base = {
        "rental_id": "RT-1", "equipment_id": "EQ-1", "customer_id": "CU-1",
        "branch_id": "BR-01", "start_date": "2026-01-05", "end_date": "2026-01-10",
        "rental_days": 5, "daily_rate": "300", "total_revenue": "1500",
        "status": "completed",
    }
    rows = [dict(base)]
    rows.append(dict(base))  # exact duplicate
    rows.append({**base, "rental_id": "RT-2", "daily_rate": "", "total_revenue": "1500"})
    rows.append({**base, "rental_id": "RT-3", "status": "COMPLETED"})  # uppercase status
    rows.append({**base, "rental_id": "RT-4", "start_date": "2026-01-10",
                 "end_date": "2026-01-05"})  # end before start
    rows.append({**base, "rental_id": "RT-5", "equipment_id": "EQ-UNKNOWN"})
    rows.append({**base, "rental_id": "RT-6", "total_revenue": "99999"})  # stale revenue
    rows.append({**base, "rental_id": "RT-7", "customer_id": None})  # missing key
    return pd.DataFrame(rows)


def test_clean_equipment_dedupes_and_normalises_status():
    df = clean_equipment(_equipment_frame())
    assert set(df["equipment_id"]) == {"EQ-1", "EQ-2"}
    assert df.loc[df["equipment_id"] == "EQ-2", "status"].iloc[0] == "rented"
    assert "EQ-3" not in set(df["equipment_id"])  # invalid status dropped


def test_clean_rentals_removes_duplicates_and_bad_rows():
    df, report = clean_rentals(_rentals_frame(), _equipment_frame())
    assert report["duplicates_removed"] == 1
    assert report["invalid_date_rows_removed"] == 1
    assert report["orphan_equipment_rows_removed"] == 1
    assert report["missing_key_rows_removed"] == 1
    assert set(df["rental_id"]) == {"RT-1", "RT-2", "RT-3", "RT-6"}


def test_clean_rentals_fills_missing_rate_from_catalogue():
    df, report = clean_rentals(_rentals_frame(), _equipment_frame())
    assert report["missing_daily_rate_filled"] == 1
    row = df.loc[df["rental_id"] == "RT-2"].iloc[0]
    assert row["daily_rate"] == 300.0


def test_clean_rentals_normalises_uppercase_status():
    df, _ = clean_rentals(_rentals_frame(), _equipment_frame())
    assert df.loc[df["rental_id"] == "RT-3", "status"].iloc[0] == "completed"


def test_clean_rentals_recomputes_stale_revenue():
    df, report = clean_rentals(_rentals_frame(), _equipment_frame())
    assert report["revenue_recomputed"] == 1
    row = df.loc[df["rental_id"] == "RT-6"].iloc[0]
    assert row["total_revenue"] == 1500.0


def test_clean_rentals_keeps_sub_dollar_rounding():
    # A 1-cent rounding artefact must NOT be rewritten.
    eq = _equipment_frame().iloc[:1]
    row = {"rental_id": "RT-9", "equipment_id": "EQ-1", "customer_id": "CU-1",
           "branch_id": "BR-01", "start_date": "2026-02-01", "end_date": "2026-02-03",
           "rental_days": 2, "daily_rate": "300.005", "total_revenue": "600.01",
           "status": "completed"}
    df, report = clean_rentals(pd.DataFrame([row]), eq)
    assert report["revenue_recomputed"] == 0
    assert len(df) == 1


def test_clean_rentals_empty_frame_does_not_crash():
    cols = ["rental_id", "equipment_id", "customer_id", "branch_id", "start_date",
            "end_date", "rental_days", "daily_rate", "total_revenue", "status"]
    df, report = clean_rentals(pd.DataFrame(columns=cols), _equipment_frame())
    assert len(df) == 0
    assert report["raw"] == 0


@pytest.mark.skipif(not DB.exists(), reason="warehouse not built")
def test_warehouse_quality_checks_all_pass():
    result = subprocess.run(
        [sys.executable, str(ROOT / "etl" / "data_quality.py")],
        capture_output=True, text=True, cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout
    assert "15/15 checks passed" in result.stdout


@pytest.mark.skipif(not DB.exists(), reason="warehouse not built")
def test_warehouse_row_counts_match_generator():
    conn = sqlite3.connect(DB)
    try:
        counts = {
            t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in ["dim_branch", "dim_equipment", "dim_customer",
                      "fact_rentals", "fact_maintenance"]
        }
    finally:
        conn.close()
    assert counts["dim_branch"] == 8
    assert counts["dim_equipment"] == 420
    assert counts["dim_customer"] == 320
    assert counts["fact_rentals"] == 7327
    assert counts["fact_maintenance"] == 900


@pytest.mark.skipif(not DB.exists(), reason="warehouse not built")
def test_warehouse_revenue_reconciles():
    conn = sqlite3.connect(DB)
    try:
        total = conn.execute("SELECT ROUND(SUM(total_revenue), 2) FROM fact_rentals").fetchone()[0]
        billable = conn.execute("SELECT ROUND(SUM(billable_revenue), 2) FROM stg_rentals").fetchone()[0]
        cancelled = conn.execute(
            "SELECT ROUND(SUM(total_revenue), 2) FROM fact_rentals WHERE status='cancelled'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert total == 18712625.17
    assert abs((billable + cancelled) - total) < 0.01
