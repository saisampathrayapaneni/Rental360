"""Deterministic AI assistant for Rental360.

This assistant does NOT call any LLM (see ``app/llm_client.py`` — that
module is an unimplemented stub seam). It parses the user's question with
case-insensitive regex/keyword rules, maps it to one of a fixed set of
intents, and runs a fixed, parameterised, read-only SQL query against the
warehouse. Every number in every answer comes from that SQL — nothing is
invented.

ALL DATA IN THIS WAREHOUSE IS SYNTHETIC. Answers report the synthetic
figures as warehouse figures, never as real business results.

Safety:
- Any question containing a SQL write/DDL keyword (INSERT, UPDATE,
  DELETE, DROP, ALTER, PRAGMA, ...) is rejected before anything runs.
- All SQL is fixed text with bound parameters; user input is never
  interpolated into a query string.

Return shape (every intent)::

    {"answer": str, "intent": str, "sql": str | None, "rows": list[dict]}

``sql`` is the exact SQL that was run (``None`` when no query ran, e.g.
unknown questions or the forecast-missing case). ``rows`` is the evidence
behind the answer.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .db import REPO_ROOT, fetch_all, fetch_one

# Last date_key in the warehouse; the data window is 2024-01-01..2026-10-31.
DATA_START = "2024-01-01"
DATA_END = "2026-10-31"
# "Last month" / "this month" resolve to the most recent full month in data.
LAST_MONTH = "2026-10"
LAST_MONTH_LABEL = "October 2026 (the most recent month in the data)"

FORECAST_PATH = REPO_ROOT / "ml" / "artifacts" / "latest_forecast.json"

_FORBIDDEN = re.compile(
    r"\b(insert|update|delete|drop|alter|pragma|truncate|create|replace|"
    r"attach|detach|vacuum|reindex|grant|revoke|merge|call|exec|execute)\b",
    re.IGNORECASE,
)

_CAPABILITIES = (
    "I can answer these kinds of questions about the Rental360 warehouse "
    "(all figures are synthetic demo data):\n"
    "- Total revenue — e.g. 'What is the total revenue?' (add 'in 2025', "
    "'this year', or 'last month' for a period)\n"
    "- Top branch by revenue — e.g. 'Which branch generated the most revenue?'\n"
    "- Branch revenue — e.g. 'How much revenue did the Dallas branch make?'\n"
    "- Revenue by category — e.g. 'Which category earns the most?'\n"
    "- Lowest-utilization / idle equipment — e.g. 'Which equipment is sitting idle?'\n"
    "- Top customers — e.g. 'Who are our top customers by revenue?'\n"
    "- Average rental duration / value — e.g. 'What is the average rental duration?'\n"
    "- Maintenance costs — e.g. 'What is the total maintenance cost?' or "
    "'Which equipment costs the most to maintain?'\n"
    "- Demand forecast — e.g. 'What is the predicted demand next month?'"
)


def _money(value) -> str:
    """Format a number as $X,XXX.XX (or 'n/a' for None)."""
    if value is None:
        return "n/a"
    return f"${float(value):,.2f}"


def _unknown(question: str) -> dict:
    return {
        "answer": (
            "I don't have an answer for that one — I only answer questions "
            "about the Rental360 warehouse, using fixed queries so every "
            "number is traceable. " + _CAPABILITIES
        ),
        "intent": "unknown",
        "sql": None,
        "rows": [],
    }


def _rejected(question: str) -> dict:
    return {
        "answer": (
            "I can't run that — the question contains a SQL write or "
            "administration keyword, and I only run read-only queries "
            "against the warehouse. Please rephrase as a question about "
            "revenue, branches, equipment, customers, maintenance, or "
            "demand. " + _CAPABILITIES
        ),
        "intent": "rejected",
        "sql": None,
        "rows": [],
    }


def _period_filter(question: str) -> tuple[str | None, str]:
    """Detect an optional period filter.

    Returns (prefix, label) where prefix is a year (``'2025'``) or a
    year-month (``'2026-10'``) matched against the start of ``date_key``,
    or (None, 'all time ...') when no period is mentioned.
    """
    q = question.lower()
    if "last month" in q or "previous month" in q or "this month" in q:
        return LAST_MONTH, LAST_MONTH_LABEL
    year = re.search(r"\b(2024|2025|2026)\b", q)
    if year:
        return year.group(1), year.group(1)
    if "this year" in q or "current year" in q:
        return "2026", "2026 (the current year in the data)"
    if "last year" in q:
        return "2025", "2025"
    return None, f"all time ({DATA_START} to {DATA_END})"


def _no_data(intent: str) -> dict:
    return {
        "answer": (
            "The warehouse tables are empty — there is no data to answer "
            "from. Run the ETL to load the warehouse first."
        ),
        "intent": intent,
        "sql": None,
        "rows": [],
    }


def _intent_total_revenue(q: str) -> dict:
    prefix, label = _period_filter(q)
    if prefix:
        where = "WHERE substr(date_key, 1, ?) = ?"
        params = (len(prefix), prefix)
        where_billable = "WHERE substr(date_key, 1, ?) = ?"
        params_billable = (len(prefix), prefix)
    else:
        where, params = "", ()
        where_billable, params_billable = "", ()
    sql = (
        "SELECT COUNT(*) AS rental_count, "
        "COALESCE(SUM(total_revenue), 0) AS total_revenue "
        f"FROM fact_rentals {where}"
    )
    row = fetch_one(sql, params)
    if row is None:
        return _no_data("total_revenue")
    billable = fetch_one(
        "SELECT COALESCE(SUM(billable_revenue), 0) AS billable_revenue "
        f"FROM stg_rentals {where_billable}",
        params_billable,
    )
    total = row["total_revenue"]
    count = row["rental_count"]
    billable_total = billable["billable_revenue"]
    answer = (
        f"Total revenue for {label} is {_money(total)} across "
        f"{count:,} rentals. Of that, {_money(billable_total)} is billable "
        f"revenue (excluding cancelled rentals, which are booked at $0)."
    )
    return {
        "answer": answer,
        "intent": "total_revenue",
        "sql": sql,
        "rows": [row],
    }


def _intent_top_branch(q: str) -> dict:
    sql = (
        "SELECT branch_id, branch_name, city, state, rental_count, "
        "total_revenue, avg_rental_value, avg_rental_days "
        "FROM mart_revenue_by_branch ORDER BY total_revenue DESC LIMIT 1"
    )
    row = fetch_one(sql)
    if row is None:
        return _no_data("top_branch")
    answer = (
        f"{row['branch_name']} ({row['city']}, {row['state']}) generated the "
        f"most revenue: {_money(row['total_revenue'])} across "
        f"{row['rental_count']:,} rentals "
        f"(average rental value {_money(row['avg_rental_value'])}). "
        "Revenue here is billable revenue, which excludes cancelled rentals."
    )
    return {"answer": answer, "intent": "top_branch", "sql": sql, "rows": [row]}


def _intent_branch_revenue(q: str, branch: dict) -> dict:
    sql = (
        "SELECT branch_id, branch_name, city, state, rental_count, "
        "total_revenue, avg_rental_value, avg_rental_days "
        "FROM mart_revenue_by_branch WHERE branch_id = ?"
    )
    row = fetch_one(sql, (branch["branch_id"],))
    if row is None:
        return _no_data("branch_revenue")
    answer = (
        f"{row['branch_name']} ({row['city']}, {row['state']}) generated "
        f"{_money(row['total_revenue'])} across {row['rental_count']:,} "
        f"rentals (average rental value {_money(row['avg_rental_value'])}, "
        f"average rental duration {row['avg_rental_days']} days). Revenue "
        "here is billable revenue, which excludes cancelled rentals."
    )
    return {"answer": answer, "intent": "branch_revenue", "sql": sql, "rows": [row]}


def _intent_unknown_branch(q: str) -> dict:
    """The question names a branch that does not exist in the warehouse."""
    branches = fetch_all(
        "SELECT branch_name, city FROM dim_branch ORDER BY branch_name"
    )
    names = ", ".join(f"{b['branch_name']} ({b['city']})" for b in branches)
    return {
        "answer": (
            "I couldn't find a branch matching that name in the warehouse. "
            f"The branches are: {names}. Please ask about one of those."
        ),
        "intent": "unknown_branch",
        "sql": None,
        "rows": [],
    }


def _intent_category_revenue(q: str, category: str | None) -> dict:
    if category:
        sql = (
            "SELECT e.category, COUNT(*) AS rental_count, "
            "COALESCE(SUM(r.billable_revenue), 0) AS total_revenue "
            "FROM stg_rentals r JOIN dim_equipment e "
            "ON e.equipment_id = r.equipment_id "
            "WHERE e.category = ? GROUP BY e.category"
        )
        row = fetch_one(sql, (category,))
        if row is None:
            return _no_data("category_revenue")
        answer = (
            f"The {category} category generated "
            f"{_money(row['total_revenue'])} across "
            f"{row['rental_count']:,} rentals."
        )
        return {
            "answer": answer,
            "intent": "category_revenue",
            "sql": sql,
            "rows": [row],
        }
    sql = (
        "SELECT e.category, COUNT(*) AS rental_count, "
        "COALESCE(SUM(r.billable_revenue), 0) AS total_revenue "
        "FROM stg_rentals r JOIN dim_equipment e "
        "ON e.equipment_id = r.equipment_id "
        "GROUP BY e.category ORDER BY total_revenue DESC"
    )
    rows = fetch_all(sql)
    if not rows:
        return _no_data("category_revenue")
    top = rows[0]
    lines = ", ".join(
        f"{r['category']} ({_money(r['total_revenue'])})" for r in rows
    )
    answer = (
        f"The best-earning category is {top['category']} at "
        f"{_money(top['total_revenue'])} across {top['rental_count']:,} "
        f"rentals. Revenue by category: {lines}."
    )
    return {
        "answer": answer,
        "intent": "category_revenue",
        "sql": sql,
        "rows": rows,
    }


def _intent_lowest_utilization(q: str) -> dict:
    # Idle is defined as utilization under 10% (the same threshold the
    # /api/kpis idle_equipment_count uses). A second, stricter check counts
    # equipment currently 'available' with zero non-cancelled rentals in
    # the last 90 days of the data window (2026-08-02..2026-10-31).
    sql = (
        "SELECT equipment_id, equipment_name, category, branch_id, "
        "daily_rate, status, rented_days, rental_count, days_in_service, "
        "utilization_pct FROM mart_equipment_utilization "
        "ORDER BY utilization_pct ASC, rental_count ASC LIMIT 5"
    )
    rows = fetch_all(sql)
    idle_count = fetch_one(
        "SELECT COUNT(*) AS idle_count FROM mart_equipment_utilization "
        "WHERE utilization_pct < 10"
    )["idle_count"]
    idle_90d = fetch_one(
        "SELECT COUNT(*) AS n FROM dim_equipment e WHERE e.status = 'available' "
        "AND NOT EXISTS (SELECT 1 FROM stg_rentals r "
        "WHERE r.equipment_id = e.equipment_id AND r.status != 'cancelled' "
        "AND r.start_date >= date(?, '-90 days'))",
        (DATA_END,),
    )["n"]
    names = ", ".join(
        f"{r['equipment_name']} ({r['utilization_pct']}% utilized, "
        f"{r['rental_count']} rentals)"
        for r in rows
    )
    answer = (
        "I define idle equipment as utilization under 10% — "
        f"{idle_count} of the fleet meets that definition. The 5 "
        f"lowest-utilization items are: {names}. Separately, {idle_90d} "
        "items are currently 'available' with zero rentals in the last 90 "
        f"days of the data window (2026-08-02 to 2026-10-31)."
    )
    return {
        "answer": answer,
        "intent": "lowest_utilization",
        "sql": sql,
        "rows": rows,
    }


def _intent_top_customers(q: str) -> dict:
    sql = (
        "SELECT customer_id, customer_name, customer_type, city, state, "
        "rental_count, lifetime_revenue, avg_rental_value, last_rental_date "
        "FROM mart_top_customers ORDER BY lifetime_revenue DESC LIMIT 5"
    )
    rows = fetch_all(sql)
    names = ", ".join(
        f"{r['customer_name']} ({_money(r['lifetime_revenue'])}, "
        f"{r['rental_count']} rentals)"
        for r in rows
    )
    answer = f"The top 5 customers by lifetime revenue are: {names}."
    return {
        "answer": answer,
        "intent": "top_customers",
        "sql": sql,
        "rows": rows,
    }


def _intent_average(q: str) -> dict:
    sql = (
        "SELECT COUNT(*) AS rental_count, "
        "ROUND(AVG(rental_days), 1) AS avg_rental_days, "
        "ROUND(AVG(billable_revenue), 2) AS avg_rental_value "
        "FROM stg_rentals WHERE status != 'cancelled'"
    )
    row = fetch_one(sql)
    if row is None:
        return _no_data("average_rental")
    wants_duration = any(
        w in q for w in ("duration", "long", "days", "length")
    )
    wants_value = any(w in q for w in ("value", "worth", "average rental"))
    if wants_duration and not wants_value:
        answer = (
            f"The average rental duration is {row['avg_rental_days']} days "
            f"(across {row['rental_count']:,} completed/active rentals)."
        )
    elif wants_value and not wants_duration:
        answer = (
            f"The average rental value is {_money(row['avg_rental_value'])} "
            f"(across {row['rental_count']:,} completed/active rentals, "
            "excluding cancelled rentals)."
        )
    else:
        answer = (
            f"The average rental lasts {row['avg_rental_days']} days and is "
            f"worth {_money(row['avg_rental_value'])} "
            f"(across {row['rental_count']:,} completed/active rentals, "
            "excluding cancelled rentals)."
        )
    return {"answer": answer, "intent": "average_rental", "sql": sql, "rows": [row]}


def _intent_maintenance(q: str) -> dict:
    total = fetch_one(
        "SELECT COUNT(*) AS event_count, COALESCE(SUM(cost), 0) AS total_cost "
        "FROM fact_maintenance"
    )
    sql = (
        "SELECT e.equipment_id, e.equipment_name, e.category, "
        "COUNT(*) AS maintenance_events, SUM(m.cost) AS total_cost "
        "FROM fact_maintenance m JOIN dim_equipment e "
        "ON e.equipment_id = m.equipment_id "
        "GROUP BY e.equipment_id, e.equipment_name, e.category "
        "ORDER BY total_cost DESC LIMIT 5"
    )
    rows = fetch_all(sql)
    if not rows:
        return _no_data("maintenance")
    top = rows[0]
    names = ", ".join(
        f"{r['equipment_name']} ({_money(r['total_cost'])})" for r in rows
    )
    answer = (
        f"Total maintenance cost is {_money(total['total_cost'])} across "
        f"{total['event_count']:,} maintenance events. The most expensive "
        f"equipment to maintain is {top['equipment_name']} "
        f"({top['category']}) at {_money(top['total_cost'])} in total. "
        f"Top 5 by maintenance cost: {names}."
    )
    return {
        "answer": answer,
        "intent": "maintenance",
        "sql": sql,
        "rows": rows,
    }


def _intent_forecast(q: str) -> dict:
    if not FORECAST_PATH.exists():
        return {
            "answer": (
                "The demand forecast has not been trained yet — there is no "
                "forecast file at ml/artifacts/latest_forecast.json. Train "
                "the demand-forecast model (the training step in ml/) so it "
                "writes that file, then ask again. I never invent a "
                "forecast number."
            ),
            "intent": "forecast",
            "sql": None,
            "rows": [],
        }
    try:
        data = json.loads(Path(FORECAST_PATH).read_text())
    except (OSError, ValueError):
        return {
            "answer": (
                "A forecast file exists at ml/artifacts/latest_forecast.json "
                "but it could not be read as JSON. Re-train the "
                "demand-forecast model to regenerate it."
            ),
            "intent": "forecast",
            "sql": None,
            "rows": [],
        }
    rows = data if isinstance(data, list) else data.get("predictions", [])
    if isinstance(data, dict) and not rows:
        rows = [data]
    next_month = "2026-11"
    month_rows = [
        r for r in rows
        if isinstance(r, dict) and str(r.get("year_month", "")) == next_month
    ]
    use_rows = month_rows or rows
    if not use_rows:
        return {
            "answer": (
                "The forecast file exists but contains no predictions I can "
                "summarise. Re-train the demand-forecast model to "
                "regenerate ml/artifacts/latest_forecast.json."
            ),
            "intent": "forecast",
            "sql": None,
            "rows": [],
        }
    parts = []
    for r in use_rows[:10]:
        label = r.get("year_month") or r.get("period") or next_month
        pred = (
            r.get("predicted_rentals")
            or r.get("predicted_rental_count")
            or r.get("predicted_demand")
        )
        rev = r.get("predicted_revenue")
        bits = [str(label)]
        if r.get("category"):
            bits.append(str(r["category"]))
        if r.get("branch_id"):
            bits.append(str(r["branch_id"]))
        if pred is not None:
            bits.append(f"predicted rentals: {pred}")
        if rev is not None:
            bits.append(f"predicted revenue: {_money(rev)}")
        parts.append(" — ".join(bits))
    answer = (
        "Predicted demand for next month (November 2026), from "
        "ml/artifacts/latest_forecast.json: " + "; ".join(parts) + "."
    )
    return {
        "answer": answer,
        "intent": "forecast",
        "sql": None,
        "rows": [dict(r) for r in use_rows if isinstance(r, dict)],
    }


def _find_branch(q: str) -> dict | None:
    """Match a branch by name or city substring in the question."""
    branches = fetch_all(
        "SELECT branch_id, branch_name, city, state FROM dim_branch"
    )
    for b in branches:
        if b["branch_name"].lower() in q or b["city"].lower() in q:
            return b
    return None


def _find_category(q: str) -> str | None:
    """Match a known equipment category substring in the question."""
    cats = fetch_all("SELECT DISTINCT category FROM dim_equipment")
    for row in cats:
        if row["category"].lower() in q:
            return row["category"]
    return None


def answer_question(question: str) -> dict:
    """Answer *question* deterministically from the warehouse.

    Returns {"answer", "intent", "sql", "rows"}. Never raises for a
    well-formed question string; database errors propagate as exceptions
    for the API layer to turn into a 500.
    """
    q = (question or "").strip()
    if not q:
        return _unknown(q)
    lower = q.lower()

    # Safety gate first: no write/DDL keywords, ever.
    if _FORBIDDEN.search(q):
        return _rejected(q)

    # Forecast (reads the model artifact, not the DB).
    if re.search(r"\b(forecast|predict\w*|demand)\b", lower):
        return _intent_forecast(lower)

    # Maintenance.
    if "maintenance" in lower:
        return _intent_maintenance(lower)

    # Average rental duration / value.
    if re.search(r"\baverage\b", lower) and re.search(
        r"\b(rental|duration|long|days|value|worth)\b", lower
    ):
        return _intent_average(lower)

    # Top customers.
    if re.search(r"\b(top|best|biggest|highest)\b", lower) and "customer" in lower:
        return _intent_top_customers(lower)
    if "top customers" in lower or "best customers" in lower:
        return _intent_top_customers(lower)

    # Lowest utilization / idle equipment.
    if re.search(
        r"\b(idle|underutili[sz]ed|lowest utilization|least used|"
        r"sitting idle|unused)\b",
        lower,
    ) or ("lowest" in lower and "utilization" in lower):
        return _intent_lowest_utilization(lower)

    # Top branch by revenue.
    if "branch" in lower and re.search(
        r"\b(most|top|best|highest|biggest)\b", lower
    ) and "revenue" in lower:
        return _intent_top_branch(lower)

    # Named branch revenue ("how much did the Dallas branch make?").
    if "branch" in lower or "revenue" in lower:
        branch = _find_branch(lower)
        if branch is not None:
            return _intent_branch_revenue(lower, branch)
        if "branch" in lower:
            # Only treat this as a named-branch question when the wording
            # actually names one ("the Dallas branch", "branch in Austin");
            # generic branch questions fall through to the other intents.
            if re.search(r"the\s+\w+\s+branch|branch\s+(in|at|near)\s+\w+", lower):
                return _intent_unknown_branch(lower)

    # Category revenue.
    if "categor" in lower:
        category = _find_category(lower)
        return _intent_category_revenue(lower, category)
    if re.search(r"\b(best|top|highest)\b", lower) and "categor" in lower:
        return _intent_category_revenue(lower, None)
    category = _find_category(lower)
    if category is not None and "revenue" in lower:
        return _intent_category_revenue(lower, category)

    # Total revenue (optionally period-filtered).
    if "revenue" in lower:
        return _intent_total_revenue(lower)

    return _unknown(q)
