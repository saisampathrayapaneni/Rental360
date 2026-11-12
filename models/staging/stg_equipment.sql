-- stg_equipment: equipment dimension with a derived age band.
select
    equipment_id,
    equipment_name,
    category,
    make,
    model,
    year,
    daily_rate,
    purchase_date,
    branch_id,
    status,
    cast(strftime('%Y', 'now') as integer) - year as age_years
from dim_equipment
