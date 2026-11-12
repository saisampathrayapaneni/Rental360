"""Utilization anomaly detection for Rental360.

ALL DATA IS SYNTHETIC. Flags are illustrative only and must never be
presented as real business findings.

Method: for each equipment category, compute the mean and standard
deviation of ``utilization_pct`` across non-retired equipment in
``mart_equipment_utilization``. An item is an anomaly when its z-score
``(utilization - category_mean) / category_std`` is below -2 (unusually
idle) or above +2 (unusually hot). Retired equipment is never flagged.

CLI::

    python ml/anomaly.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "warehouse" / "rental360.db"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


def load_utilization(db_path: Path = DB_PATH) -> pd.DataFrame:
    """Load ``mart_equipment_utilization`` from the SQLite warehouse."""
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Warehouse not found: {db_path}")
    with sqlite3.connect(db_path) as con:
        df = pd.read_sql_query("SELECT * FROM mart_equipment_utilization", con)
    return df


def detect_anomalies(df: pd.DataFrame | None = None) -> list[dict]:
    """Flag utilization outliers per category. Returns a list of dicts::

        {equipment_id, equipment_name, category, branch_id,
         utilization_pct, z_score, flag: "idle_outlier" | "hot_outlier"}

    Retired equipment is never flagged. Categories with fewer than 2
    non-retired items or a zero standard deviation produce no flags.
    """
    if df is None:
        df = load_utilization()
    active = df[df["status"] != "retired"].copy()
    stats = (
        active.groupby("category")["utilization_pct"]
        .agg(["mean", "std", "count"])
        .reset_index()
    )
    merged = active.merge(stats, on="category", suffixes=("", "_cat"))
    anomalies = []
    for _, row in merged.iterrows():
        std = row["std"]
        if pd.isna(std) or std == 0 or row["count"] < 2:
            continue
        z = (row["utilization_pct"] - row["mean"]) / std
        if z < -2:
            flag = "idle_outlier"
        elif z > 2:
            flag = "hot_outlier"
        else:
            continue
        anomalies.append(
            {
                "equipment_id": str(row["equipment_id"]),
                "equipment_name": str(row["equipment_name"]),
                "category": str(row["category"]),
                "branch_id": str(row["branch_id"]),
                "utilization_pct": round(float(row["utilization_pct"]), 2),
                "z_score": round(float(z), 2),
                "flag": flag,
            }
        )
    return sorted(anomalies, key=lambda a: (a["category"], a["z_score"]))


def main() -> int:
    try:
        anomalies = detect_anomalies()
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        (ARTIFACTS_DIR / "anomalies.json").write_text(
            json.dumps(anomalies, indent=2)
        )
        idle = sum(1 for a in anomalies if a["flag"] == "idle_outlier")
        hot = sum(1 for a in anomalies if a["flag"] == "hot_outlier")
        print(f"Utilization anomalies (synthetic data): {len(anomalies)} total "
              f"({idle} idle outliers, {hot} hot outliers)")
        for a in anomalies:
            print(
                f"  {a['equipment_id']} {a['equipment_name']} "
                f"[{a['category']} {a['branch_id']}] "
                f"util={a['utilization_pct']}% z={a['z_score']} {a['flag']}"
            )
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI must exit non-zero on failure
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
