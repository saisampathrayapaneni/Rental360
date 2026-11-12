-- mart_revenue_by_branch: revenue, rental count and avg rental value per branch.
-- Grain: one row per branch.
select
    b.branch_id,
    b.branch_name,
    b.city,
    b.state,
    count(r.rental_id) as rental_count,
    sum(r.billable_revenue) as total_revenue,
    round(avg(r.billable_revenue), 2) as avg_rental_value,
    round(avg(r.rental_days), 1) as avg_rental_days
from stg_rentals r
join dim_branch b on b.branch_id = r.branch_id
group by b.branch_id, b.branch_name, b.city, b.state
