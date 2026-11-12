"""
Rental360 — Synthetic data generator.

Generates a realistic, fully SYNTHETIC equipment-rental dataset:
  - 8 branches, ~420 equipment items, ~320 customers
  - ~6,500 rental transactions over 2024-01-01 .. 2026-10-31
  - ~900 maintenance records

Everything is generated from a fixed seed (default 42) so the dataset is
reproducible. No real company, customer, or transaction is represented.
All output is labelled SYNTHETIC in the README and in the dashboard.

Usage:
    python data/generate_data.py [--seed 42] [--out data/raw]
"""
from __future__ import annotations

import argparse
import csv
import random
from datetime import date, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# Catalogues (fictional names only)
# ---------------------------------------------------------------------------

BRANCHES = [
    ("BR-01", "Austin North", "Austin", "TX"),
    ("BR-02", "Dallas Metro", "Dallas", "TX"),
    ("BR-03", "Houston Gulf", "Houston", "TX"),
    ("BR-04", "San Antonio River", "San Antonio", "TX"),
    ("BR-05", "Phoenix Desert", "Phoenix", "AZ"),
    ("BR-06", "Denver Front Range", "Denver", "CO"),
    ("BR-07", "Atlanta Peachtree", "Atlanta", "GA"),
    ("BR-08", "Charlotte Queen City", "Charlotte", "NC"),
]

# category -> (makes/models, daily rate range, typical rental days)
EQUIPMENT_CATALOG = {
    "Excavator": (
        [("Caterpillar", "320"), ("Komatsu", "PC210"), ("John Deere", "130G"),
         ("Hitachi", "ZX135"), ("Volvo", "EC140")],
        (180, 420), (3, 21),
    ),
    "Bulldozer": (
        [("Caterpillar", "D6"), ("Komatsu", "D61"), ("John Deere", "650K"),
         ("Case", "1150M")],
        (220, 480), (4, 28),
    ),
    "Crane": (
        [("Liebherr", "LTM 1060"), ("Grove", "RT600E"), ("Terex", "RT555"),
         ("Manitowoc", "Grove GMK")],
        (450, 950), (2, 14),
    ),
    "Forklift": (
        [("Toyota", "8FGU25"), ("Hyster", "H50XT"), ("Crown", "C-5"),
         ("Yale", "GLP050")],
        (85, 190), (2, 14),
    ),
    "Generator": (
        [("Cummins", "C100D6"), ("Generac", "MDG25"), ("Kohler", "60REOZ"),
         ("Atlas Copco", "QAS 60")],
        (70, 240), (2, 21),
    ),
    "Aerial Lift": (
        [("JLG", "450AJ"), ("Genie", "S-65"), ("Skyjack", "SJ85AJ"),
         ("Haulotte", "HA20 RTJ")],
        (120, 320), (2, 14),
    ),
    "Truck & Trailer": (
        [("Ford", "F-750 Flatbed"), ("Kenworth", "T880 Dump"), ("Peterbilt", "579"),
         ("Freightliner", "M2 106")],
        (150, 380), (2, 14),
    ),
    "Compactor": (
        [("Bomag", "BW213"), ("Dynapac", "CA3500"), ("Caterpillar", "CS56"),
         ("Hamm", "H 7i")],
        (140, 310), (3, 18),
    ),
}

CUSTOMER_TYPES = ["Construction", "Landscaping", "Events", "Industrial", "Municipal"]
CUSTOMER_FIRST = [
    "Summit", "Lone Star", "Bluebonnet", "Ironwood", "Pinnacle", "Red River",
    "Highland", "Copperline", "Frontier", "Magnolia", "Cactus", "Beacon",
    "Sterling", "Evergreen", "Liberty", "Atlas", "Horizon", "Keystone",
    "Prairie", "Sierra", "Timberline", "Vantage", "Zephyr", "Canyon",
]
CUSTOMER_SECOND = [
    "Builders", "Contracting", "Construction Co.", "Landscaping", "Excavating",
    "Events Group", "Industrial Services", "Public Works", "Development",
    "Site Services", "Grading Co.", "Rentals & Hauling", "Civil Works",
    "Outdoor Living", "Paving Co.", "Demolition", "Logistics", "Energy Services",
]

CITIES = [
    ("Austin", "TX"), ("Dallas", "TX"), ("Houston", "TX"), ("San Antonio", "TX"),
    ("Fort Worth", "TX"), ("Phoenix", "AZ"), ("Denver", "CO"), ("Atlanta", "GA"),
    ("Charlotte", "NC"), ("Tucson", "AZ"), ("Colorado Springs", "CO"),
    ("Savannah", "GA"), ("Raleigh", "NC"), ("El Paso", "TX"), ("Mesa", "AZ"),
]

MAINT_TYPES = [
    ("Preventive", 150, 650, 1, 2),
    ("Repair", 300, 2800, 1, 5),
    ("Inspection", 80, 250, 1, 1),
    ("Tire/Track replacement", 600, 2400, 1, 3),
    ("Engine service", 400, 1800, 1, 3),
]

START = date(2024, 1, 1)
END = date(2026, 10, 31)


def seasonal_factor(d: date) -> float:
    """Construction demand peaks in spring/summer, dips in winter."""
    month = d.month
    return {
        1: 0.62, 2: 0.70, 3: 0.95, 4: 1.10, 5: 1.20, 6: 1.25,
        7: 1.18, 8: 1.12, 9: 1.05, 10: 0.98, 11: 0.80, 12: 0.65,
    }[month]


def gen_branches() -> list[dict]:
    rng = random.Random(42)
    rows = []
    for bid, name, city, state in BRANCHES:
        rows.append({
            "branch_id": bid,
            "branch_name": name,
            "city": city,
            "state": state,
            "opened_date": (date(2015, 1, 1) + timedelta(days=rng.randrange(0, 3000))).isoformat(),
        })
    return rows


def gen_equipment(rng: random.Random, branches: list[dict]) -> list[dict]:
    rows = []
    eq_id = 1000
    for category, (models, rate_range, _days) in EQUIPMENT_CATALOG.items():
        # Weight fleet size: excavators/forklifts/generators are the workhorses.
        count = {"Excavator": 70, "Bulldozer": 45, "Crane": 28, "Forklift": 75,
                 "Generator": 65, "Aerial Lift": 55, "Truck & Trailer": 45,
                 "Compactor": 37}[category]
        for _ in range(count):
            eq_id += 1
            make, model = rng.choice(models)
            purchase = date(2019, 1, 1) + timedelta(days=rng.randrange(0, 2500))
            branch = rng.choice(branches)
            rows.append({
                "equipment_id": f"EQ-{eq_id}",
                "equipment_name": f"{make} {model}",
                "category": category,
                "make": make,
                "model": model,
                "year": purchase.year,
                "daily_rate": round(rng.uniform(*rate_range), 2),
                "purchase_date": purchase.isoformat(),
                "branch_id": branch["branch_id"],
                "status": rng.choices(
                    ["available", "rented", "maintenance", "retired"],
                    weights=[0.72, 0.18, 0.07, 0.03])[0],
            })
    return rows


def gen_customers(rng: random.Random, n: int = 320) -> list[dict]:
    rows, seen = [], set()
    for i in range(1, n + 1):
        name = f"{rng.choice(CUSTOMER_FIRST)} {rng.choice(CUSTOMER_SECOND)}"
        if name in seen:  # keep names unique
            name = f"{name} {i}"
        seen.add(name)
        city, state = rng.choice(CITIES)
        rows.append({
            "customer_id": f"CU-{1000 + i}",
            "customer_name": name,
            "customer_type": rng.choices(
                CUSTOMER_TYPES, weights=[0.42, 0.16, 0.12, 0.20, 0.10])[0],
            "city": city,
            "state": state,
            "signup_date": (date(2020, 1, 1) + timedelta(days=rng.randrange(0, 2400))).isoformat(),
        })
    return rows


def gen_rentals(rng: random.Random, equipment: list[dict],
                customers: list[dict]) -> list[dict]:
    rows = []
    rental_id = 5000
    # Expected rentals per day, scaled by seasonality and a slow growth trend.
    day = START
    while day <= END:
        growth = 1.0 + 0.25 * ((day - START).days / (END - START).days)
        lam = 6.5 * seasonal_factor(day) * growth
        # Poisson-ish count via Knuth for small lambda.
        n_today = 0
        p, limit = 1.0, 2.718281828 ** (-lam)
        while True:
            p *= rng.random()
            if p <= limit:
                break
            n_today += 1
        for _ in range(n_today):
            eq = rng.choice(equipment)
            if eq["status"] == "retired":
                continue
            cust = rng.choice(customers)
            _, _, day_range = EQUIPMENT_CATALOG[eq["category"]]
            days = rng.randint(*day_range)
            # Weekend starts are rarer; long rentals slightly cheaper per day.
            rate = eq["daily_rate"] * (0.92 if days >= 14 else 1.0)
            revenue = round(rate * days, 2)
            rental_id += 1
            rows.append({
                "rental_id": f"RT-{rental_id}",
                "equipment_id": eq["equipment_id"],
                "customer_id": cust["customer_id"],
                "branch_id": eq["branch_id"],
                "start_date": day.isoformat(),
                "end_date": (day + timedelta(days=days)).isoformat(),
                "rental_days": days,
                "daily_rate": round(rate, 2),
                "total_revenue": revenue,
                "status": rng.choices(
                    ["completed", "completed", "completed", "completed",
                     "active", "cancelled"],
                    weights=[0.88, 0.88, 0.88, 0.88, 0.05, 0.07])[0],
            })
        day += timedelta(days=1)
    # A few deliberate data-quality issues for the ETL to clean (documented):
    #  - 6 exact duplicate rows
    #  - 4 rows with a missing daily_rate
    #  - 3 rows with a lowercase status value
    for _ in range(6):
        rows.append(dict(rng.choice(rows)))
    for _ in range(4):
        r = dict(rng.choice(rows))
        r["rental_id"] = f"RT-{rental_id + 1}"
        rental_id += 1
        r["daily_rate"] = ""
        rows.append(r)
    for _ in range(3):
        r = dict(rng.choice(rows))
        r["rental_id"] = f"RT-{rental_id + 1}"
        rental_id += 1
        r["status"] = r["status"].upper()
        rows.append(r)
    rng.shuffle(rows)
    return rows


def gen_maintenance(rng: random.Random, equipment: list[dict]) -> list[dict]:
    rows = []
    for i in range(1, 901):
        eq = rng.choice(equipment)
        mtype, lo, hi, dlo, dhi = rng.choice(MAINT_TYPES)
        mdate = date(2024, 1, 1) + timedelta(days=rng.randrange(0, 1034))
        rows.append({
            "maintenance_id": f"MT-{2000 + i}",
            "equipment_id": eq["equipment_id"],
            "maintenance_date": mdate.isoformat(),
            "maintenance_type": mtype,
            "cost": round(rng.uniform(lo, hi), 2),
            "downtime_days": rng.randint(dlo, dhi),
        })
    return rows


def write_csv(path: Path, rows: list[dict]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default="data/raw")
    args = ap.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    branches = gen_branches()
    equipment = gen_equipment(rng, branches)
    customers = gen_customers(rng)
    rentals = gen_rentals(rng, equipment, customers)
    maintenance = gen_maintenance(rng, equipment)

    for name, rows in [
        ("branches", branches), ("equipment", equipment),
        ("customers", customers), ("rentals", rentals),
        ("maintenance", maintenance),
    ]:
        write_csv(out / f"{name}.csv", rows)
        print(f"{name}.csv: {len(rows):,} rows")

    print("All data is SYNTHETIC — generated with seed", args.seed)


if __name__ == "__main__":
    main()
