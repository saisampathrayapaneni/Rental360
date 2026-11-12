"""
Rental360 — data-quality checks (dbt-test equivalents for the SQLite runtime).

Checks:
  1. No NULL primary keys in any table.
  2. No negative revenue / rates / costs.
  3. Referential integrity: every fact row references known dimensions.
  4. No duplicate rental_ids.
  5. end_date >= start_date on every rental.
  6. Date dimension covers the full fact range.
  7. Utilization mart values are within [0, 100].

Usage: python etl/data_quality.py [--db data/warehouse/rental360.db]
Exit code 0 when all checks pass, 1 otherwise.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CHECKS: list[tuple[str, str, str]] = [
    # (name, sql returning offending-row count, failure message)
    ("pk_nulls_branch", "SELECT COUNT(*) FROM dim_branch WHERE branch_id IS NULL",
     "NULL branch_id"),
    ("pk_nulls_equipment", "SELECT COUNT(*) FROM dim_equipment WHERE equipment_id IS NULL",
     "NULL equipment_id"),
    ("pk_nulls_customer", "SELECT COUNT(*) FROM dim_customer WHERE customer_id IS NULL",
     "NULL customer_id"),
    ("pk_nulls_rentals", "SELECT COUNT(*) FROM fact_rentals WHERE rental_id IS NULL",
     "NULL rental_id"),
    ("negative_revenue", "SELECT COUNT(*) FROM fact_rentals WHERE total_revenue < 0",
     "negative total_revenue"),
    ("negative_rate", "SELECT COUNT(*) FROM dim_equipment WHERE daily_rate <= 0",
     "non-positive daily_rate"),
    ("negative_maint_cost", "SELECT COUNT(*) FROM fact_maintenance WHERE cost < 0",
     "negative maintenance cost"),
    ("duplicate_rentals", "SELECT COUNT(*) - COUNT(DISTINCT rental_id) FROM fact_rentals",
     "duplicate rental_id"),
    ("bad_date_range", "SELECT COUNT(*) FROM fact_rentals WHERE end_date < start_date",
     "end_date before start_date"),
    ("orphan_rental_equipment",
     "SELECT COUNT(*) FROM fact_rentals f LEFT JOIN dim_equipment e USING(equipment_id) WHERE e.equipment_id IS NULL",
     "rental references unknown equipment"),
    ("orphan_rental_customer",
     "SELECT COUNT(*) FROM fact_rentals f LEFT JOIN dim_customer c USING(customer_id) WHERE c.customer_id IS NULL",
     "rental references unknown customer"),
    ("orphan_rental_branch",
     "SELECT COUNT(*) FROM fact_rentals f LEFT JOIN dim_branch b USING(branch_id) WHERE b.branch_id IS NULL",
     "rental references unknown branch"),
    ("orphan_maint_equipment",
     "SELECT COUNT(*) FROM fact_maintenance m LEFT JOIN dim_equipment e USING(equipment_id) WHERE e.equipment_id IS NULL",
     "maintenance references unknown equipment"),
    ("date_coverage",
     "SELECT COUNT(*) FROM fact_rentals f LEFT JOIN dim_date d ON d.date_key = f.date_key WHERE d.date_key IS NULL",
     "fact date missing from dim_date"),
    ("utilization_bounds",
     "SELECT COUNT(*) FROM mart_equipment_utilization WHERE utilization_pct < 0 OR utilization_pct > 100",
     "utilization outside [0, 100]"),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "warehouse" / "rental360.db"))
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    failed = 0
    try:
        for name, sql, msg in CHECKS:
            try:
                n = conn.execute(sql).fetchone()[0]
            except sqlite3.Error as e:
                print(f"  FAIL {name}: could not run ({e})")
                failed += 1
                continue
            if n:
                print(f"  FAIL {name}: {n:,} offending rows — {msg}")
                failed += 1
            else:
                print(f"  PASS {name}")
    finally:
        conn.close()
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
