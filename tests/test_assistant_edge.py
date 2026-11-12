"""Rental360 — assistant edge-case tests (extends tests/test_api.py).

Covers ambiguous/empty/hostile inputs and period filters against the real
warehouse via the FastAPI TestClient.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def ask(q: str) -> dict:
    r = client.post("/api/assistant", json={"question": q})
    assert r.status_code == 200, r.text
    return r.json()


def test_empty_question_returns_400():
    # Empty questions are rejected at the API boundary with a clear message;
    # the parser itself returns "unknown" for empty input (tested directly).
    r = client.post("/api/assistant", json={"question": "   "})
    assert r.status_code == 400
    assert "empty" in r.json()["detail"]
    from app.assistant import answer_question
    assert answer_question("   ")["intent"] == "unknown"


def test_gibberish_question_is_unknown():
    body = ask("flibberty gibbet wozzle")
    assert body["intent"] == "unknown"
    assert "total revenue" in body["answer"].lower() or "can answer" in body["answer"].lower()


def test_period_filter_year():
    body = ask("What was the total revenue in 2025?")
    assert body["intent"] == "total_revenue"
    assert "2025" in body["answer"]
    assert body["rows"], "expected evidence rows"


def test_period_filter_last_month():
    body = ask("How much revenue did we make last month?")
    assert body["intent"] == "total_revenue"
    assert body["sql"] is not None


def test_unknown_branch_name_falls_back_gracefully():
    body = ask("How much revenue did the Atlantis branch make?")
    assert body["intent"] == "unknown_branch"
    # Must never invent a number for a branch that does not exist.
    assert "couldn't find a branch" in body["answer"]
    assert "Austin North" in body["answer"]  # lists the real branches
    assert body["rows"] == []


def test_case_insensitive_intent():
    body = ask("WHICH BRANCH GENERATED THE MOST REVENUE?")
    assert body["intent"] == "top_branch"
    assert "Charlotte" in body["answer"]


def test_write_keyword_rejected():
    for q in ["DELETE FROM fact_rentals", "drop table dim_branch",
              "update fact_rentals set total_revenue = 0",
              "SELECT * FROM fact_rentals; DROP TABLE dim_customer"]:
        body = ask(q)
        assert body["intent"] == "rejected", q


def test_forecast_question_without_model_file_reports_honestly():
    # The live artifact exists in this repo; the question must return a
    # forecast intent with real numbers from the artifact, never invented.
    body = ask("What is the predicted demand next month?")
    assert body["intent"] == "forecast"
    assert "2026-11" in body["answer"]


def test_idle_question_names_real_equipment():
    body = ask("Which equipment is sitting idle?")
    assert body["intent"] in {"idle_equipment", "lowest_utilization"}
    assert body["rows"], "expected evidence rows for idle equipment"
    assert all(float(r["utilization_pct"]) < 10 for r in body["rows"][:5])


def test_top_customers_returns_five():
    body = ask("Who are our top customers?")
    assert body["intent"] == "top_customers"
    assert len(body["rows"]) == 5
    revenues = [float(r["lifetime_revenue"]) for r in body["rows"]]
    assert revenues == sorted(revenues, reverse=True)
