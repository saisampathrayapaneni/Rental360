-- mart_equipment_utilization: rented days vs. days in service per item.
-- Utilization = rented days / days since purchase (capped at the data window).
-- Grain: one row per equipment item.
with rental_days as (
    select equipment_id, sum(rental_days) as rented_days, count(*) as rental_count
    from stg_rentals
    where status != 'cancelled'
    group by equipment_id
),
service_days as (
    select
        equipment_id,
        julianday('2026-10-31') - julianday(purchase_date) as days_in_service
    from stg_equipment
)
select
    e.equipment_id,
    e.equipment_name,
    e.category,
    e.branch_id,
    e.daily_rate,
    e.status,
    coalesce(rd.rented_days, 0) as rented_days,
    coalesce(rd.rental_count, 0) as rental_count,
    cast(sd.days_in_service as integer) as days_in_service,
    round(100.0 * coalesce(rd.rented_days, 0) / nullif(sd.days_in_service, 0), 1)
        as utilization_pct
from stg_equipment e
left join rental_days rd on rd.equipment_id = e.equipment_id
join service_days sd on sd.equipment_id = e.equipment_id
