/*
    mart_severity_trends
    --------------------
    Answers: Are storms getting more frequent AND more severe, or just more frequent?

    Grain: one row per event_year + event_type_group combination.
    Materialized as table — full refresh. Output is ~300 rows (30 years × 10 groups),
    too small to justify incremental complexity.

    Excludes pre-1996 and 2026 (partial year) from trend analysis:
    - Pre-1996: incomparable schema and reporting infrastructure
    - 2026: partial year would make event counts look artificially low
    Excludes NULL event_type_group: unmapped event types (should be caught by tests)
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
        event_year,
        event_type_group,

        -- Frequency metrics
        COUNT(*)                                        as event_count,

        -- Human cost metrics
        SUM(deaths_direct)                             as total_deaths_direct,
        SUM(injuries_direct)                           as total_injuries_direct,
        ROUND(AVG(deaths_direct), 4)                   as avg_deaths_per_event,

        -- Nominal damage (raw dollars of the year)
        ROUND(SUM(COALESCE(property_damage_usd, 0) +
                  COALESCE(crop_damage_usd, 0)), 2)    as total_damage_usd,

        -- Inflation-adjusted damage (2024 dollars) — use for cross-year comparisons
        ROUND(SUM(total_damage_2024_usd), 2)           as total_damage_2024_usd,

        -- Severity: damage per event (inflation-adjusted)
        -- Key metric for severity escalation pillar:
        -- rising avg damage = storms getting more destructive per occurrence
        -- rising event count with flat avg damage = more storms, same severity
        ROUND(
            SAFE_DIVIDE(
                SUM(total_damage_2024_usd),
                NULLIF(COUNT(*), 0)
            ), 2
        )                                               as avg_damage_per_event_2024_usd,

        -- Fatality rate per event — separates deadlier storms from more frequent ones
        ROUND(
            SAFE_DIVIDE(SUM(deaths_direct), NULLIF(COUNT(*), 0)),
        4)                                              as fatality_rate_per_event

    from base
    group by event_year, event_type_group
)

select * from aggregated
order by event_year, event_type_group
