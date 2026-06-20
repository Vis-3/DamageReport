/*
    mart_geographic_risk
    --------------------
    Answers: Which states are most at risk and is the risk distribution shifting?

    Grain: one row per state + decade combination.
    Materialized as table — full refresh. ~400 rows (50 states × 8 decades).

    Excludes pre-1996, 2026 partial year, and NULL states (marine/territory zones)
    since geographic analysis requires a US state assignment.
*/

{{ config(materialized='table') }}

with base as (
    select * from {{ ref('int_events_enriched') }}
    where
        is_pre_standardization = false
        and event_year < 2026
        and state is not null
),

aggregated as (
    select
        state,
        region,
        decade,

        -- Volume
        COUNT(*)                                            as event_count,

        -- Human cost
        SUM(deaths_direct)                                  as total_deaths_direct,
        SUM(injuries_direct)                                as total_injuries_direct,

        -- Economic impact in 2024 dollars
        ROUND(SUM(total_damage_2024_usd), 2)               as total_damage_2024_usd,
        ROUND(SUM(COALESCE(property_damage_2024_usd, 0)), 2) as property_damage_2024_usd,
        ROUND(SUM(COALESCE(crop_damage_2024_usd, 0)), 2)   as crop_damage_2024_usd,

        -- Damage per event: measures destructiveness per occurrence, not just total
        ROUND(
            SAFE_DIVIDE(SUM(total_damage_2024_usd), NULLIF(COUNT(*), 0))
        , 2)                                                as avg_damage_per_event_2024_usd,

        -- Most common event type in this state/decade
        -- Useful for "tornado alley", "hurricane coast" narrative
        -- Picks the group with the highest event count using CASE + COUNTIF
        CASE
            WHEN COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'FLOOD')
             AND COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'HURRICANE')
             AND COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'WIND')
             AND COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'WINTER_STORM')
             AND COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'HEAT')
             AND COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'DROUGHT')
             AND COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'FIRE')
             AND COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'HAIL')
             AND COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'MARINE')
             AND COUNTIF(event_type_group = 'TORNADO')      >= COUNTIF(event_type_group = 'OTHER')
            THEN 'TORNADO'
            WHEN COUNTIF(event_type_group = 'FLOOD')        >= COUNTIF(event_type_group = 'HURRICANE')
             AND COUNTIF(event_type_group = 'FLOOD')        >= COUNTIF(event_type_group = 'WIND')
             AND COUNTIF(event_type_group = 'FLOOD')        >= COUNTIF(event_type_group = 'WINTER_STORM')
             AND COUNTIF(event_type_group = 'FLOOD')        >= COUNTIF(event_type_group = 'HEAT')
             AND COUNTIF(event_type_group = 'FLOOD')        >= COUNTIF(event_type_group = 'DROUGHT')
             AND COUNTIF(event_type_group = 'FLOOD')        >= COUNTIF(event_type_group = 'FIRE')
             AND COUNTIF(event_type_group = 'FLOOD')        >= COUNTIF(event_type_group = 'HAIL')
             AND COUNTIF(event_type_group = 'FLOOD')        >= COUNTIF(event_type_group = 'MARINE')
             AND COUNTIF(event_type_group = 'FLOOD')        >= COUNTIF(event_type_group = 'OTHER')
            THEN 'FLOOD'
            WHEN COUNTIF(event_type_group = 'WIND')         >= COUNTIF(event_type_group = 'HURRICANE')
             AND COUNTIF(event_type_group = 'WIND')         >= COUNTIF(event_type_group = 'WINTER_STORM')
             AND COUNTIF(event_type_group = 'WIND')         >= COUNTIF(event_type_group = 'HEAT')
             AND COUNTIF(event_type_group = 'WIND')         >= COUNTIF(event_type_group = 'DROUGHT')
             AND COUNTIF(event_type_group = 'WIND')         >= COUNTIF(event_type_group = 'FIRE')
             AND COUNTIF(event_type_group = 'WIND')         >= COUNTIF(event_type_group = 'HAIL')
             AND COUNTIF(event_type_group = 'WIND')         >= COUNTIF(event_type_group = 'MARINE')
             AND COUNTIF(event_type_group = 'WIND')         >= COUNTIF(event_type_group = 'OTHER')
            THEN 'WIND'
            WHEN COUNTIF(event_type_group = 'HURRICANE')    >= COUNTIF(event_type_group = 'WINTER_STORM')
             AND COUNTIF(event_type_group = 'HURRICANE')    >= COUNTIF(event_type_group = 'HEAT')
             AND COUNTIF(event_type_group = 'HURRICANE')    >= COUNTIF(event_type_group = 'DROUGHT')
             AND COUNTIF(event_type_group = 'HURRICANE')    >= COUNTIF(event_type_group = 'FIRE')
             AND COUNTIF(event_type_group = 'HURRICANE')    >= COUNTIF(event_type_group = 'HAIL')
             AND COUNTIF(event_type_group = 'HURRICANE')    >= COUNTIF(event_type_group = 'MARINE')
             AND COUNTIF(event_type_group = 'HURRICANE')    >= COUNTIF(event_type_group = 'OTHER')
            THEN 'HURRICANE'
            ELSE 'WINTER_STORM'
        END                                                 as dominant_event_type

    from base
    group by state, region, decade
),

-- Add risk percentile ranking within each decade
-- Lets us identify which states are in the top 10% of damage for a given decade
ranked as (
    select
        *,
        ROUND(
            PERCENT_RANK() OVER (
                PARTITION BY decade
                ORDER BY total_damage_2024_usd
            ) * 100
        , 1) as damage_percentile_in_decade
    from aggregated
)

select * from ranked
order by decade, total_damage_2024_usd desc
