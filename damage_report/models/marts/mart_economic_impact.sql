/*
    mart_economic_impact
    --------------------
    Answers: What does extreme weather actually cost and which event types are
    most destructive per occurrence?

    Grain: one row per event_type_group.
    Materialized as table — full refresh. 10 rows (one per event group).

    Key metric: avg_damage_per_event_2024_usd — "efficiency of destruction."
    A hurricane causes more total damage than a tornado because there are more of them,
    but damage per occurrence tells you which event type is most destructive
    when it actually happens.
*/

{{ config(materialized='table') }}

with base as (
    select * from {{ ref('int_events_enriched') }}
    where
        is_pre_standardization = false
        and event_year < 2026
        and event_type_group is not null
),

aggregated as (
    select
        event_type_group,

        -- Volume
        COUNT(*)                                        as total_events,

        -- Total economic impact in 2024 dollars
        ROUND(SUM(total_damage_2024_usd), 2)           as total_damage_2024_usd,
        ROUND(SUM(COALESCE(property_damage_2024_usd, 0)), 2) as total_property_damage_2024_usd,
        ROUND(SUM(COALESCE(crop_damage_2024_usd, 0)), 2)     as total_crop_damage_2024_usd,

        -- Nominal totals for reference
        ROUND(SUM(total_damage_usd), 2)                as total_damage_usd_nominal,

        -- Damage per event: the "efficiency of destruction" metric
        -- Use this to compare event types fairly regardless of frequency
        ROUND(
            SAFE_DIVIDE(SUM(total_damage_2024_usd), NULLIF(COUNT(*), 0))
        , 2)                                            as avg_damage_per_event_2024_usd,

        -- Human cost
        SUM(deaths_direct)                             as total_deaths_direct,
        SUM(injuries_direct)                           as total_injuries_direct,
        ROUND(
            SAFE_DIVIDE(SUM(deaths_direct), NULLIF(COUNT(*), 0))
        , 4)                                            as avg_deaths_per_event,

        -- Percentage of total damage across all event types
        -- Computed as window function below
        ROUND(SUM(total_damage_2024_usd), 2)           as _damage_for_pct  -- temp, used below

    from base
    group by event_type_group
),

with_percentages as (
    select
        event_type_group,
        total_events,
        total_damage_2024_usd,
        total_property_damage_2024_usd,
        total_crop_damage_2024_usd,
        total_damage_usd_nominal,
        avg_damage_per_event_2024_usd,
        total_deaths_direct,
        total_injuries_direct,
        avg_deaths_per_event,

        -- Share of total damage across all event types
        ROUND(
            SAFE_DIVIDE(total_damage_2024_usd,
                SUM(total_damage_2024_usd) OVER ()
            ) * 100
        , 2)                                            as pct_of_total_damage,

        -- Rank by damage per event (most destructive per occurrence first)
        RANK() OVER (ORDER BY avg_damage_per_event_2024_usd DESC) as rank_by_damage_per_event,

        -- Rank by total damage
        RANK() OVER (ORDER BY total_damage_2024_usd DESC)         as rank_by_total_damage

    from aggregated
)

select * from with_percentages
order by rank_by_damage_per_event
