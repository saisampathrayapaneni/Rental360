"""Rental360 FastAPI backend.

Serves KPI / reporting endpoints over the synthetic SQLite warehouse and
a deterministic assistant (regex + fixed read-only SQL — no LLM; see
``app/assistant.py`` and ``app/llm_client.py``).

ALL DATA IS SYNTHETIC demo data. The dashboard static files are mounted
at ``/`` when ``app/static`` exists; another agent owns those files.

Every endpoint handles a missing/empty database gracefully: a 500 with a
clear message, never a traceback leak.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .assistant import answer_question
from .db import (
    REPO_ROOT,
    DatabaseUnavailableError,
    get_connection,
)

FORECAST_PATH = REPO_ROOT / "ml" / "artifacts" / "latest_forecast.json"
STATIC_DIR = REPO_ROOT / "app" / "static"

DATA_START = "2024-01-01"
DATA_END = "2026-10-31"

app = FastAPI(
    title="Rental360 API",
    description=(
        "Reporting API over the Rental360 synthetic warehouse. "
        "All data is synthetic demo data."
    ),
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class AssistantRequest(BaseModel):
    question: str = Field(..., min_length=1, description="Natural-language question")


def _run(sql: str, params: tuple = ()):
    """Run a read-only query, translating DB problems into clean 500s."""
    try:
        conn = get_connection()
    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500, detail=f"Warehouse query failed: {exc}"
        )
    finally:
        conn.close()


def _run_one(sql: str, params: tuple = ()):
    rows = _run(sql, params)
    return rows[0] if rows else None


@app.get("/api/health")
def health():
    return {"status": "ok", "synthetic_data": True}


@app.get("/api/kpis")
def kpis():
    totals = _run_one(
        "SELECT COUNT(*) AS total_rentals, "
        "COALESCE(SUM(total_revenue), 0) AS total_revenue FROM fact_rentals"
    )
    util = _run_one(
        "SELECT ROUND(AVG(utilization_pct), 2) AS avg_utilization_pct, "
        "SUM(CASE WHEN utilization_pct < 10 THEN 1 ELSE 0 END) "
        "AS idle_equipment_count FROM mart_equipment_utilization"
    )
    counts = _run_one(
        "SELECT (SELECT COUNT(*) FROM dim_equipment) AS equipment_count, "
        "(SELECT COUNT(*) FROM dim_customer) AS customer_count, "
        "(SELECT COUNT(*) FROM dim_branch) AS branch_count"
    )
    top = _run_one(
        "SELECT branch_name, total_revenue FROM mart_revenue_by_branch "
        "ORDER BY total_revenue DESC LIMIT 1"
    )
    period = _run_one(
        "SELECT MIN(date_key) AS start, MAX(date_key) AS end FROM fact_rentals"
    )
    if totals is None or util is None or counts is None or top is None:
        raise HTTPException(
            status_code=500,
            detail="Warehouse tables are empty — no data to report. "
            "Run the ETL to load the warehouse first.",
        )
    return {
        "total_revenue": totals["total_revenue"],
        "total_rentals": totals["total_rentals"],
        "avg_utilization_pct": util["avg_utilization_pct"],
        "idle_equipment_count": util["idle_equipment_count"],
        "equipment_count": counts["equipment_count"],
        "customer_count": counts["customer_count"],
        "branch_count": counts["branch_count"],
        "top_branch": {
            "name": top["branch_name"],
            "revenue": top["total_revenue"],
        },
        "period": {
            "start": period["start"] or DATA_START,
            "end": period["end"] or DATA_END,
        },
    }


@app.get("/api/revenue-timeseries")
def revenue_timeseries(
    granularity: str = Query("month", pattern="^(month|quarter)$"),
):
    if granularity == "month":
        period_expr = "substr(date_key, 1, 7)"
    else:
        period_expr = (
            "substr(date_key, 1, 4) || '-Q' || "
            "CAST((CAST(substr(date_key, 6, 2) AS INTEGER) - 1) / 3 + 1 AS TEXT)"
        )
    sql = (
        f"SELECT {period_expr} AS period, "
        "COALESCE(SUM(billable_revenue), 0) AS revenue, "
        "COUNT(*) AS rentals FROM stg_rentals "
        "GROUP BY period ORDER BY period"
    )
    return _run(sql)


@app.get("/api/branch-performance")
def branch_performance():
    return _run(
        "SELECT branch_id, branch_name, city, state, rental_count, "
        "total_revenue, avg_rental_value, avg_rental_days "
        "FROM mart_revenue_by_branch ORDER BY total_revenue DESC"
    )


@app.get("/api/top-customers")
def top_customers(limit: int = Query(10, ge=1, le=100)):
    return _run(
        "SELECT customer_id, customer_name, customer_type, city, state, "
        "rental_count, lifetime_revenue, avg_rental_value, last_rental_date "
        "FROM mart_top_customers ORDER BY lifetime_revenue DESC LIMIT ?",
        (limit,),
    )


@app.get("/api/equipment")
def equipment(
    category: str | None = None,
    branch_id: str | None = None,
    min_utilization: float | None = None,
    limit: int = Query(100, ge=1, le=500),
):
    conditions: list[str] = []
    params: list = []
    if category:
        conditions.append("category = ?")
        params.append(category)
    if branch_id:
        conditions.append("branch_id = ?")
        params.append(branch_id)
    if min_utilization is not None:
        conditions.append("utilization_pct >= ?")
        params.append(min_utilization)
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    sql = (
        "SELECT equipment_id, equipment_name, category, branch_id, "
        "daily_rate, status, rented_days, rental_count, days_in_service, "
        "utilization_pct FROM mart_equipment_utilization "
        f"{where} ORDER BY utilization_pct ASC LIMIT ?"
    )
    params.append(limit)
    return _run(sql, tuple(params))


@app.get("/api/forecast")
def forecast(category: str | None = None, branch_id: str | None = None):
    if not FORECAST_PATH.exists():
        return {
            "available": False,
            "message": (
                "No forecast has been generated yet. Train the "
                "demand-forecast model so it writes "
                "ml/artifacts/latest_forecast.json, then call this endpoint "
                "again."
            ),
        }
    try:
        data = json.loads(Path(FORECAST_PATH).read_text())
    except (OSError, ValueError):
        return {
            "available": False,
            "message": (
                "A forecast file exists at ml/artifacts/latest_forecast.json "
                "but it could not be read as JSON. Re-train the "
                "demand-forecast model to regenerate it."
            ),
        }
    rows = data if isinstance(data, list) else data.get("predictions", [])
    if isinstance(data, dict) and not rows:
        rows = [data]
    rows = [r for r in rows if isinstance(r, dict)]
    if category:
        rows = [r for r in rows if str(r.get("category", "")) == category]
    if branch_id:
        rows = [r for r in rows if str(r.get("branch_id", "")) == branch_id]
    return {"available": True, "forecast": rows}


@app.post("/api/assistant")
def assistant(req: AssistantRequest):
    question = (req.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question must not be empty")
    try:
        return answer_question(question)
    except DatabaseUnavailableError as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    except sqlite3.Error as exc:
        raise HTTPException(
            status_code=500, detail=f"Warehouse query failed: {exc}"
        )


@app.get("/api/branches")
def branches():
    return _run(
        "SELECT branch_id, branch_name, city, state, opened_date "
        "FROM dim_branch ORDER BY branch_name"
    )


@app.get("/api/categories")
def categories():
    rows = _run("SELECT DISTINCT category FROM dim_equipment ORDER BY category")
    return [r["category"] for r in rows]


# Dashboard: mount the static files another agent owns at the root.
# Registered last so /api/* routes take precedence.
if STATIC_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
else:

    @app.get("/")
    def _root():
        return {
            "message": "Rental360 API — dashboard static files not yet added.",
            "docs": "/docs",
            "health": "/api/health",
        }
