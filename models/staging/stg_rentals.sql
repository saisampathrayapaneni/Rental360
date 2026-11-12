-- stg_rentals: cleaned rental facts, one row per rental.
-- Source: fact_rentals (populated by etl/load_warehouse.py).
-- Excludes cancelled rentals from revenue measures downstream; the flag is
-- kept here so marts can decide explicitly.
select
    rental_id,
    equipment_id,
    customer_id,
    branch_id,
    date_key,
    start_date,
    end_date,
    rental_days,
    daily_rate,
    total_revenue,
    status,
    case when status = 'cancelled' then 0 else total_revenue end as billable_revenue
from fact_rentals
