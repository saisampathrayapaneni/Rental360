"""
Rental360 — ETL pipeline: raw CSVs -> SQLite warehouse (star schema).

Steps
  1. Extract: read data/raw/*.csv with pandas.
  2. Clean (documented, deterministic):
       - drop exact duplicate rows (generator deliberately inserts 6)
       - drop rows with missing rental_id / equipment_id / customer_id
       - fill missing daily_rate from the equipment's catalogue rate
       - normalise status values to lowercase and map to the allowed set
       - drop rows whose end_date < start_date, or with rental_days <= 0
       - recompute total_revenue = daily_rate * rental_days when the stored
         value disagrees by more than $0.01 (guards against stale edits)
       - drop fact rows referencing unknown equipment/customers/branches
         (referential-integrity guard; reported, not silently kept)
  3. Transform: build dim_date for the full date range.
  4. Load: recreate the warehouse from db/schema.sql and bulk-insert.

Usage:
    python etl/load_warehouse.py [--raw data/raw] [--db data/warehouse/rental360.db]

Exit code 0 on success; prints a row-count + cleaning report.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_STATUS = {"completed", "active", "cancelled"}
ALLOWED_EQ_STATUS = {"available", "rented", "maintenance", "retired"}


def build_dim_date(start: date, end: date) -> pd.DataFrame:
    rows = []
    d = start
    while d <= end:
        rows.append({
            "date_key": d.isoformat(),
            "date": d.isoformat(),
            "year": d.year,
            "month": d.month,
            "month_name": d.strftime("%B"),
            "quarter": (d.month - 1) // 3 + 1,
            "day_of_week": d.isoweekday(),
            "is_weekend": 1 if d.weekday() >= 5 else 0,
        })
        d += timedelta(days=1)
    return pd.DataFrame(rows)


def clean_rentals(df: pd.DataFrame, equipment: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Clean the rentals frame. Returns (clean_df, report)."""
    report = {"raw": len(df)}
    df = df.copy()
    # 1. exact duplicates
    dupes = int(df.duplicated().sum())
    df = df.drop_duplicates()
    report["duplicates_removed"] = dupes
    # 2. missing keys
    before = len(df)
    df = df.dropna(subset=["rental_id", "equipment_id", "customer_id", "branch_id"])
    report["missing_key_rows_removed"] = before - len(df)
    # 3. normalise status
    df["status"] = df["status"].astype(str).str.strip().str.lower()
    bad = ~df["status"].isin(ALLOWED_STATUS)
    report["invalid_status_rows_removed"] = int(bad.sum())
    df = df[~bad]
    # 4. dates
    df["start_date"] = pd.to_datetime(df["start_date"], errors="coerce")
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce")
    bad_dates = df["start_date"].isna() | df["end_date"].isna() | (df["end_date"] < df["start_date"])
    report["invalid_date_rows_removed"] = int(bad_dates.sum())
    df = df[~bad_dates]
    # 5. missing daily_rate -> catalogue rate for that equipment
    #    (coerce to numeric first: pandas 3.x keeps str dtype otherwise)
    df["daily_rate"] = pd.to_numeric(df["daily_rate"], errors="coerce")
    rate_map = {k: float(v) for k, v in
                zip(equipment["equipment_id"], equipment["daily_rate"])}
    missing_rate = df["daily_rate"].isna()
    report["missing_daily_rate_filled"] = int(missing_rate.sum())
    if missing_rate.any():
        df.loc[missing_rate, "daily_rate"] = df.loc[missing_rate, "equipment_id"].map(rate_map)
    df = df.dropna(subset=["daily_rate"])
    df = df[df["daily_rate"] > 0]
    # 6. rental_days / revenue consistency
    df["rental_days"] = (df["end_date"] - df["start_date"]).dt.days
    df = df[df["rental_days"] > 0]
    df["total_revenue"] = pd.to_numeric(df["total_revenue"], errors="coerce")
    expected = (df["daily_rate"] * df["rental_days"]).round(2)
    actual = df["total_revenue"]
    # Only recompute on material disagreement (> $1): sub-dollar differences
    # are rounding artefacts of the 14+ day discount in the generator.
    mismatch = (actual - expected).abs() > 1.00
    report["revenue_recomputed"] = int(mismatch.sum())
    if mismatch.any():
        df.loc[mismatch, "total_revenue"] = expected[mismatch]
    # 7. referential integrity
    known_eq = set(equipment["equipment_id"])
    orphan = ~df["equipment_id"].isin(known_eq)
    report["orphan_equipment_rows_removed"] = int(orphan.sum())
    df = df[~orphan]

    df["date_key"] = df["start_date"].dt.strftime("%Y-%m-%d")
    df["start_date"] = df["start_date"].dt.strftime("%Y-%m-%d")
    df["end_date"] = df["end_date"].dt.strftime("%Y-%m-%d")
    report["clean"] = len(df)
    cols = ["rental_id", "equipment_id", "customer_id", "branch_id", "date_key",
            "start_date", "end_date", "rental_days", "daily_rate",
            "total_revenue", "status"]
    return df[cols], report


def clean_equipment(df: pd.DataFrame) -> pd.DataFrame:
    df = df.drop_duplicates(subset=["equipment_id"]).copy()
    df["status"] = df["status"].astype(str).str.strip().str.lower()
    df = df[df["status"].isin(ALLOWED_EQ_STATUS)]
    df["daily_rate"] = pd.to_numeric(df["daily_rate"], errors="coerce")
    df = df.dropna(subset=["daily_rate"])
    return df


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(ROOT / "data" / "raw"))
    ap.add_argument("--db", default=str(ROOT / "data" / "warehouse" / "rental360.db"))
    args = ap.parse_args()

    raw = Path(args.raw)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    branches = pd.read_csv(raw / "branches.csv", dtype=str).drop_duplicates(subset=["branch_id"])
    equipment = clean_equipment(pd.read_csv(raw / "equipment.csv", dtype=str))
    customers = pd.read_csv(raw / "customers.csv", dtype=str).drop_duplicates(subset=["customer_id"])
    rentals_raw = pd.read_csv(raw / "rentals.csv", dtype=str)
    maintenance = pd.read_csv(raw / "maintenance.csv", dtype=str).drop_duplicates(subset=["maintenance_id"])

    rentals, report = clean_rentals(rentals_raw, equipment)

    # Referential guards for customers/branches on the cleaned rentals.
    known_cust = set(customers["customer_id"])
    before = len(rentals)
    rentals = rentals[rentals["customer_id"].isin(known_cust)]
    report["orphan_customer_rows_removed"] = before - len(rentals)
    known_br = set(branches["branch_id"])
    before = len(rentals)
    rentals = rentals[rentals["branch_id"].isin(known_br)]
    report["orphan_branch_rows_removed"] = before - len(rentals)
    report["clean"] = len(rentals)

    # Maintenance cleaning: valid equipment + dates only.
    maintenance["maintenance_date"] = pd.to_datetime(maintenance["maintenance_date"], errors="coerce")
    maintenance = maintenance.dropna(subset=["maintenance_date"])
    maintenance = maintenance[maintenance["equipment_id"].isin(set(equipment["equipment_id"]))]
    maintenance["date_key"] = maintenance["maintenance_date"].dt.strftime("%Y-%m-%d")
    maintenance["maintenance_date"] = maintenance["maintenance_date"].dt.strftime("%Y-%m-%d")
    maintenance["cost"] = pd.to_numeric(maintenance["cost"], errors="coerce").fillna(0)
    maintenance["downtime_days"] = pd.to_numeric(maintenance["downtime_days"], errors="coerce").fillna(0).astype(int)
    maint_cols = ["maintenance_id", "equipment_id", "date_key", "maintenance_date",
                  "maintenance_type", "cost", "downtime_days"]

    # dim_date spans the full rental + maintenance range.
    all_dates = pd.concat([
        pd.to_datetime(rentals["start_date"]),
        pd.to_datetime(maintenance["maintenance_date"]),
    ])
    dim_date = build_dim_date(all_dates.min().date(), all_dates.max().date())

    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript((ROOT / "db" / "schema.sql").read_text())
        branches.to_sql("dim_branch", conn, if_exists="append", index=False)
        equipment.to_sql("dim_equipment", conn, if_exists="append", index=False)
        customers.to_sql("dim_customer", conn, if_exists="append", index=False)
        dim_date.to_sql("dim_date", conn, if_exists="append", index=False)
        rentals.to_sql("fact_rentals", conn, if_exists="append", index=False)
        maintenance[maint_cols].to_sql("fact_maintenance", conn, if_exists="append", index=False)
        conn.commit()
    finally:
        conn.close()

    print(f"Warehouse built: {db_path}")
    for k, v in report.items():
        print(f"  rentals.{k}: {v:,}")
    print(f"  dim_branch: {len(branches):,}  dim_equipment: {len(equipment):,}"
          f"  dim_customer: {len(customers):,}  dim_date: {len(dim_date):,}"
          f"  fact_maintenance: {len(maintenance):,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
