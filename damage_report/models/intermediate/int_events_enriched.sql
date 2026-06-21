/*
    int_events_enriched
    -------------------
    Central enrichment model — joins event type groups, CPI deflator, and
    region mapping onto every storm event. All mart models build from this
    single model, never from staging directly.

    Enrichments applied here:
      1. event_type_group — from int_event_type_groups (48 types → 10 groups)
      2. inflation-adjusted damage — multiply raw damage by CPI deflator
      3. decade — FLOOR(year/10)*10 for decade-level aggregations in marts
      4. region — from state_regions seed (state → 6 US regions)

    Why one central enrichment model instead of joining in each mart?
    If the CPI deflator logic changes (e.g. new base year), we change it here
    once. Four mart models that each join CPI independently would each need
    updating — and could drift out of sync.
*/

with events as (
    -- Use int_event_type_groups which already has event_type_group attached
    select * from {{ ref('int_event_type_groups') }}
),

cpi as (
    select * from {{ ref('stg_cpi_deflator') }}
),

regions as (
    select * from {{ ref('state_regions') }}
),

population as (
    select * from {{ ref('stg_county_population') }}
),

enriched as (
    select
        -- All event fields from upstream
        events.event_id,
        events.noaa_event_id,
        events.noaa_episode_id,
        events.event_year,
        events.event_month,
        events.begin_datetime,
        events.end_datetime,
        events.state,
        events.state_fips,
        events.county_zone_name,
        events.county_zone_type,
        events.event_type_raw,
        events.event_type_group,
        events.weather_forecast_office,
        events.injuries_direct,
        events.injuries_indirect,
        events.deaths_direct,
        events.deaths_indirect,
        events.property_damage_usd,
        events.crop_damage_usd,
        events.tornado_f_scale,
        events.tornado_length_miles,
        events.tornado_width_yards,
        events.magnitude,
        events.magnitude_type,
        events.hurricane_category,
        events.episode_narrative,
        events.event_narrative,
        events.is_pre_standardization,

        -- Decade: FLOOR(year/10)*10 gives 1990 for 1990-1999, 2000 for 2000-2009
        -- Used for decade-level aggregations in mart_geographic_risk and mart_surprise_states
        CAST(FLOOR(events.event_year / 10) * 10 AS INT64) as decade,

        -- Region: NULL for marine zones and territories with no state match
        regions.region,

        -- Inflation-adjusted damage in 2024 dollars.
        -- NULL input → NULL output (NOAA didn't record damage, don't fabricate a value)
        -- 2026 events have no CPI yet → deflator is NULL → adjusted damage is NULL
        -- This is correct: we can't inflation-adjust what we don't have CPI for yet
        ROUND(events.property_damage_usd * cpi.cpi_deflator, 2) as property_damage_2024_usd,
        ROUND(events.crop_damage_usd     * cpi.cpi_deflator, 2) as crop_damage_2024_usd,

        -- Total damage (property + crop) in both nominal and real terms
        -- COALESCE to 0 for summing: if one is NULL treat as zero contribution
        ROUND(
            COALESCE(events.property_damage_usd, 0) +
            COALESCE(events.crop_damage_usd, 0)
        , 2) as total_damage_usd,

        ROUND(
            COALESCE(events.property_damage_usd * cpi.cpi_deflator, 0) +
            COALESCE(events.crop_damage_usd     * cpi.cpi_deflator, 0)
        , 2) as total_damage_2024_usd,

        -- Keep deflator for auditability — marts can show what factor was applied
        cpi.cpi_deflator,
        cpi.cpi_annual_avg,

        -- Population density: people per sq mile at the county level (Census 2020)
        -- NULL for marine zones, territories, or counties not matched in Census data
        population.population_density,
        population.population_2020

    from events
    -- LEFT JOIN CPI: 2026 events have no CPI row yet, keep them with NULL adjusted damage
    left join cpi
        on events.event_year = cpi.cpi_year
    -- LEFT JOIN regions: marine/territory events have NULL state, keep them with NULL region
    left join regions
        on events.state = regions.state
    -- LEFT JOIN population: county zone name normalized to match Census county_name
    -- CZ_TYPE='C' are county zones; 'Z' are forecast zones (no county match expected)
    left join population
        on regions.state_abbrev = population.state_abbrev
        and LOWER(REGEXP_REPLACE(
                TRIM(events.county_zone_name),
                r'\s+(County|Parish|Borough|Census Area|Municipality)$', ''
            )) = population.county_name
)

select * from enriched
