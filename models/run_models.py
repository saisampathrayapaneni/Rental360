"""
Rental360 — dbt-style model runner (no dbt installation required).

Materialises models/staging/*.sql and models/marts/*.sql as SQLite views in
the warehouse DB, in dependency order (staging first, then marts).
The SQL is written in dbt-compatible style ({{ ref('...') }} is emulated by
plain table names, so the same files run under real dbt against Postgres
with only the ref() calls added — see models/schema.yml).

Usage:
    python models/run_models.py [--db data/warehouse/rental360.db]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Explicit order: staging models first, then marts.
MODEL_FILES = [
    "staging/stg_rentals.sql",
    "staging/stg_equipment.sql",
    "marts/mart_revenue_by_branch.sql",
    "marts/mart_equipment_utilization.sql",
    "marts/mart_monthly_demand.sql",
    "marts/mart_top_customers.sql",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "warehouse" / "rental360.db"))
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"Warehouse not found: {db_path}\nRun: python etl/load_warehouse.py")
        return 1

    conn = sqlite3.connect(db_path)
    try:
        for rel in MODEL_FILES:
            name = Path(rel).stem
            sql = (ROOT / "models" / rel).read_text()
            conn.execute(f"DROP VIEW IF EXISTS {name}")
            conn.execute(f"CREATE VIEW {name} AS {sql}")
            n = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            print(f"  {name}: {n:,} rows")
        conn.commit()
    finally:
        conn.close()
    print("Models materialised as views.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
