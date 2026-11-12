-- mart_monthly_demand: rentals + revenue per month, category and branch.
-- This is the training grain for the demand-forecast model.
-- Grain: one row per (year-month, category, branch).
select
    substr(r.date_key, 1, 7) as year_month,
    e.category,
    r.branch_id,
    count(r.rental_id) as rental_count,
    sum(r.rental_days) as total_rental_days,
    sum(r.billable_revenue) as total_revenue
from stg_rentals r
join dim_equipment e on e.equipment_id = r.equipment_id
where r.status != 'cancelled'
group by year_month, e.category, r.branch_id
