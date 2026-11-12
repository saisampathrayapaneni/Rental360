-- mart_top_customers: lifetime value per customer.
-- Grain: one row per customer.
select
    c.customer_id,
    c.customer_name,
    c.customer_type,
    c.city,
    c.state,
    count(r.rental_id) as rental_count,
    sum(r.billable_revenue) as lifetime_revenue,
    round(avg(r.billable_revenue), 2) as avg_rental_value,
    max(r.start_date) as last_rental_date
from stg_rentals r
join dim_customer c on c.customer_id = r.customer_id
group by c.customer_id, c.customer_name, c.customer_type, c.city, c.state
