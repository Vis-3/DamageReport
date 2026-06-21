/*
    stg_county_population
    ---------------------
    Light staging over raw_noaa.county_population (Census 2020 Gazetteer + CC-EST2020).
    No joins, no aggregations — just type casting and column selection.
    Notebook 04 joins this onto storm events via state_abbrev + county_name.
*/

with source as (
    select * from {{ source('raw_noaa', 'county_population') }}
)

select
    UPPER(TRIM(state_abbrev))                           as state_abbrev,
    LOWER(TRIM(county_name))                            as county_name,
    INITCAP(TRIM(state_name))                           as state_name,
    CAST(population_2020   AS INT64)                    as population_2020,
    CAST(land_area_sqmi    AS FLOAT64)                  as land_area_sqmi,
    ROUND(CAST(population_density AS FLOAT64), 4)       as population_density

from source
