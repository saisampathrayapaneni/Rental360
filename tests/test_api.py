"""API tests for the Rental360 FastAPI backend.

Covers: health, KPIs (values recomputed from the warehouse with direct
SQL in the test), branch-performance ordering, equipment category filter,
assistant intents (top branch, lowest utilization, total revenue,
unknown question, SQL-injection rejection), and the forecast endpoint's
missing-file behaviour.
"""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.db import get_db_path
from app.main import app

client = TestClient(app)


def _direct(sql, params=()):
    conn = sqlite3.connect(get_db_path())
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "synthetic_data": True}


def test_kpis_match_direct_sql():
    r = client.get("/api/kpis")
    assert r.status_code == 200
    body = r.json()

    total_revenue, total_rentals = _direct(
        "SELECT SUM(total_revenue), COUNT(*) FROM fact_rentals"
    )[0]
    avg_util, idle = _direct(
        "SELECT ROUND(AVG(utilization_pct), 2), "
        "SUM(CASE WHEN utilization_pct < 10 THEN 1 ELSE 0 END) "
        "FROM mart_equipment_utilization"
    )[0]
    equip, cust, branch = _direct(
        "SELECT (SELECT COUNT(*) FROM dim_equipment), "
        "(SELECT COUNT(*) FROM dim_customer), "
        "(SELECT COUNT(*) FROM dim_branch)"
    )[0]
    top_name, top_rev = _direct(
        "SELECT branch_name, total_revenue FROM mart_revenue_by_branch "
        "ORDER BY total_revenue DESC LIMIT 1"
    )[0]

    assert body["total_revenue"] == pytest.approx(total_revenue)
    assert body["total_rentals"] == total_rentals
    assert body["avg_utilization_pct"] == pytest.approx(avg_util)
    assert body["idle_equipment_count"] == idle
    assert body["equipment_count"] == equip
    assert body["customer_count"] == cust
    assert body["branch_count"] == branch
    assert body["top_branch"] == {"name": top_name, "revenue": pytest.approx(top_rev)}
    assert body["period"] == {"start": "2024-01-01", "end": "2026-10-31"}


def test_branch_performance_ordering():
    r = client.get("/api/branch-performance")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 8
    revenues = [row["total_revenue"] for row in rows]
    assert revenues == sorted(revenues, reverse=True)
    assert rows[0]["branch_name"] == "Charlotte Queen City"


def test_equipment_filter_by_category():
    r = client.get("/api/equipment", params={"category": "Excavator"})
    assert r.status_code == 200
    rows = r.json()
    assert rows, "expected at least one Excavator"
    assert all(row["category"] == "Excavator" for row in rows)
    utils = [row["utilization_pct"] for row in rows]
    assert utils == sorted(utils)  # default sort: utilization ascending


def test_equipment_min_utilization_filter():
    r = client.get("/api/equipment", params={"min_utilization": 50})
    assert r.status_code == 200
    rows = r.json()
    assert rows
    assert all(row["utilization_pct"] >= 50 for row in rows)


def test_revenue_timeseries_month():
    r = client.get("/api/revenue-timeseries", params={"granularity": "month"})
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["period"] == "2024-01"
    assert rows[-1]["period"] == "2026-10"
    assert len(rows) == 34
    assert all(set(row) == {"period", "revenue", "rentals"} for row in rows)


def test_revenue_timeseries_quarter():
    r = client.get("/api/revenue-timeseries", params={"granularity": "quarter"})
    assert r.status_code == 200
    rows = r.json()
    assert rows[0]["period"] == "2024-Q1"
    assert rows[-1]["period"] == "2026-Q4"


def test_top_customers_limit():
    r = client.get("/api/top-customers", params={"limit": 3})
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 3
    revs = [row["lifetime_revenue"] for row in rows]
    assert revs == sorted(revs, reverse=True)
    assert rows[0]["customer_name"] == "Evergreen Development"


def test_branches_and_categories():
    r = client.get("/api/branches")
    assert r.status_code == 200
    assert len(r.json()) == 8
    r = client.get("/api/categories")
    assert r.status_code == 200
    assert "Excavator" in r.json()


def _ask(question):
    r = client.post("/api/assistant", json={"question": question})
    assert r.status_code == 200
    return r.json()


def test_assistant_top_branch():
    body = _ask("Which branch generated the most revenue?")
    assert body["intent"] == "top_branch"
    assert "Charlotte Queen City" in body["answer"]
    assert "$2,941,464.08" in body["answer"]
    assert body["sql"] is not None
    assert body["rows"][0]["branch_name"] == "Charlotte Queen City"


def test_assistant_lowest_utilization():
    body = _ask("Which equipment is sitting idle?")
    assert body["intent"] == "lowest_utilization"
    assert len(body["rows"]) == 5
    utils = [row["utilization_pct"] for row in body["rows"]]
    assert utils == sorted(utils)
    assert "under 10%" in body["answer"]


def test_assistant_total_revenue():
    body = _ask("What is the total revenue?")
    assert body["intent"] == "total_revenue"
    assert "$18,712,625.17" in body["answer"]
    assert "7,327" in body["answer"]


def test_assistant_total_revenue_period():
    body = _ask("What was the total revenue in 2025?")
    assert body["intent"] == "total_revenue"
    assert "$6,310,835.60" in body["answer"]


def test_assistant_unknown_question():
    body = _ask("What is the weather going to be tomorrow?")
    assert body["intent"] == "unknown"
    assert body["sql"] is None
    assert body["rows"] == []
    assert "Total revenue" in body["answer"]  # lists what it CAN answer


def test_assistant_sql_injection_rejected():
    body = _ask("total revenue'; DROP TABLE fact_rentals; --")
    assert body["intent"] == "rejected"
    assert body["sql"] is None
    assert body["rows"] == []
    # The table must still exist and be intact.
    assert _direct("SELECT COUNT(*) FROM fact_rentals")[0][0] == 7327


def test_assistant_forecast_not_trained(monkeypatch, tmp_path):
    import app.assistant as assistant_module

    monkeypatch.setattr(
        assistant_module, "FORECAST_PATH", tmp_path / "no_such_forecast.json"
    )
    body = _ask("What is the predicted demand next month?")
    assert body["intent"] == "forecast"
    assert body["sql"] is None
    assert "has not been trained yet" in body["answer"]
    assert "latest_forecast.json" in body["answer"]


def test_assistant_forecast_present():
    # The ML agent has trained the model: ml/artifacts/latest_forecast.json
    # holds 2026-11 predictions (one per category x branch). Row count is
    # not asserted — the ML agent regenerates the file.
    body = _ask("What is the predicted demand next month?")
    assert body["intent"] == "forecast"
    assert "November 2026" in body["answer"]
    assert "predicted rentals" in body["answer"]
    assert body["rows"], "expected forecast rows from the trained model"
    assert all(row["year_month"] == "2026-11" for row in body["rows"])


def test_forecast_missing_file(monkeypatch, tmp_path):
    missing = tmp_path / "no_such_forecast.json"
    monkeypatch.setattr(main_module, "FORECAST_PATH", missing)
    r = client.get("/api/forecast")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is False
    assert "Train the demand-forecast model" in body["message"]


def test_forecast_present_file(monkeypatch, tmp_path):
    fake = tmp_path / "latest_forecast.json"
    fake.write_text(
        '[{"year_month": "2026-11", "category": "Excavator", '
        '"branch_id": "BR-01", "predicted_rentals": 42, '
        '"predicted_revenue": 123456.78}]'
    )
    monkeypatch.setattr(main_module, "FORECAST_PATH", fake)
    r = client.get("/api/forecast")
    assert r.status_code == 200
    body = r.json()
    assert body["available"] is True
    assert len(body["forecast"]) == 1
    assert body["forecast"][0]["predicted_rentals"] == 42

    r = client.get("/api/forecast", params={"category": "Crane"})
    assert r.json()["forecast"] == []


def test_assistant_empty_question_rejected():
    r = client.post("/api/assistant", json={"question": "   "})
    assert r.status_code == 400
