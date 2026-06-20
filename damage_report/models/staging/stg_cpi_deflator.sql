/*
    stg_cpi_deflator
    ----------------
    One-to-one with raw_noaa.cpi_deflator. No joins, no aggregations.
    CPI table is already clean from ingestion — this model exists to:
      - Enforce the staging contract (all sources pass through staging)
      - Add column descriptions visible in dbt docs
      - Provide a stable ref() target for int_events_enriched
*/

with source as (
    select * from {{ source('raw_noaa', 'cpi_deflator') }}
),

renamed as (
    select
        year                as cpi_year,
        cpi_annual_avg      as cpi_annual_avg,
        cpi_base_year       as cpi_base_year,

        -- deflator = CPI_2024 / CPI_year
        -- multiply any historical damage amount by this to get 2024 dollars
        -- e.g. 1996 deflator ≈ 2.0 means $1 in 1996 = $2 in 2024
        deflator            as cpi_deflator
    from source
)

select * from renamed
