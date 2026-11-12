-- Rental360 warehouse schema (star schema)
-- SQLite is the default runtime database; this file is also valid PostgreSQL
-- (types are standard SQL; replace INTEGER PRIMARY KEY AUTOINCREMENT with
--  SERIAL in Postgres — see db/schema_postgres.sql).
--
-- Grain: one row per rental in fact_rentals; one row per maintenance event
-- in fact_maintenance. All dates are ISO-8601 (YYYY-MM-DD).

CREATE TABLE IF NOT EXISTS dim_branch (
    branch_id   TEXT PRIMARY KEY,
    branch_name TEXT NOT NULL,
    city        TEXT NOT NULL,
    state       TEXT NOT NULL,
    opened_date DATE
);

CREATE TABLE IF NOT EXISTS dim_equipment (
    equipment_id   TEXT PRIMARY KEY,
    equipment_name TEXT NOT NULL,
    category       TEXT NOT NULL,
    make           TEXT NOT NULL,
    model          TEXT NOT NULL,
    year           INTEGER,
    daily_rate     REAL NOT NULL CHECK (daily_rate > 0),
    purchase_date  DATE,
    branch_id      TEXT NOT NULL REFERENCES dim_branch (branch_id),
    status         TEXT NOT NULL CHECK (status IN ('available', 'rented', 'maintenance', 'retired'))
);

CREATE TABLE IF NOT EXISTS dim_customer (
    customer_id   TEXT PRIMARY KEY,
    customer_name TEXT NOT NULL,
    customer_type TEXT NOT NULL CHECK (customer_type IN ('Construction', 'Landscaping', 'Events', 'Industrial', 'Municipal')),
    city          TEXT NOT NULL,
    state         TEXT NOT NULL,
    signup_date   DATE
);

CREATE TABLE IF NOT EXISTS dim_date (
    date_key    TEXT PRIMARY KEY,  -- YYYY-MM-DD
    date        DATE NOT NULL UNIQUE,
    year        INTEGER NOT NULL,
    month       INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    month_name  TEXT NOT NULL,
    quarter     INTEGER NOT NULL CHECK (quarter BETWEEN 1 AND 4),
    day_of_week INTEGER NOT NULL CHECK (day_of_week BETWEEN 1 AND 7), -- Mon=1
    is_weekend  INTEGER NOT NULL CHECK (is_weekend IN (0, 1))
);

CREATE TABLE IF NOT EXISTS fact_rentals (
    rental_id     TEXT PRIMARY KEY,
    equipment_id  TEXT NOT NULL REFERENCES dim_equipment (equipment_id),
    customer_id   TEXT NOT NULL REFERENCES dim_customer (customer_id),
    branch_id     TEXT NOT NULL REFERENCES dim_branch (branch_id),
    date_key      TEXT NOT NULL REFERENCES dim_date (date_key),
    start_date    DATE NOT NULL,
    end_date      DATE NOT NULL,
    rental_days   INTEGER NOT NULL CHECK (rental_days > 0),
    daily_rate    REAL NOT NULL CHECK (daily_rate > 0),
    total_revenue REAL NOT NULL CHECK (total_revenue >= 0),
    status        TEXT NOT NULL CHECK (status IN ('completed', 'active', 'cancelled')),
    CHECK (end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS fact_maintenance (
    maintenance_id   TEXT PRIMARY KEY,
    equipment_id     TEXT NOT NULL REFERENCES dim_equipment (equipment_id),
    date_key         TEXT NOT NULL REFERENCES dim_date (date_key),
    maintenance_date DATE NOT NULL,
    maintenance_type TEXT NOT NULL,
    cost             REAL NOT NULL CHECK (cost >= 0),
    downtime_days    INTEGER NOT NULL CHECK (downtime_days >= 0)
);

CREATE INDEX IF NOT EXISTS idx_fact_rentals_date ON fact_rentals (date_key);
CREATE INDEX IF NOT EXISTS idx_fact_rentals_branch ON fact_rentals (branch_id);
CREATE INDEX IF NOT EXISTS idx_fact_rentals_equipment ON fact_rentals (equipment_id);
CREATE INDEX IF NOT EXISTS idx_fact_rentals_customer ON fact_rentals (customer_id);
CREATE INDEX IF NOT EXISTS idx_fact_maint_equipment ON fact_maintenance (equipment_id);
