/*
    mart_surprise_states
    --------------------
    Answers: Which states have seen the biggest risk increase decade-over-decade?

    Grain: one row per state + decade combination, with comparison to prior decade.
    Materialized as table — full refresh. ~400 rows (50 states × 8 decades).

    "Surprise states" = states whose damage rank jumped significantly between decades.
    A state that was #40 in the 1990s and is #10 in the 2010s is a surprise —
    risk concentrated there faster than expected.
*/

{{ config(materialized='table') }}

with base as (
    select * from {{ ref('mart_geographic_risk') }}
    -- mart_geographic_risk already excludes pre-1996, 2026, NULL states
),

-- Add prior decade damage for each state using LAG window function
with_lag as (
    select
        state,
        region,
        decade,
        event_count,
        total_damage_2024_usd,
        total_deaths_direct,
        damage_percentile_in_decade,

        -- Prior decade values for comparison
        LAG(total_damage_2024_usd) OVER (
            PARTITION BY state ORDER BY decade
        )                                               as prior_decade_damage_2024_usd,

        LAG(event_count) OVER (
            PARTITION BY state ORDER BY decade
        )                                               as prior_decade_event_count,

        LAG(damage_percentile_in_decade) OVER (
            PARTITION BY state ORDER BY decade
        )                                               as prior_decade_damage_percentile

    from base
),

with_deltas as (
    select
        state,
        region,
        decade,
        event_count,
        total_damage_2024_usd,
        total_deaths_direct,
        damage_percentile_in_decade,
        prior_decade_damage_2024_usd,
        prior_decade_event_count,
        prior_decade_damage_percentile,

        -- Absolute damage change vs prior decade
        ROUND(
            total_damage_2024_usd - COALESCE(prior_decade_damage_2024_usd, 0)
        , 2)                                            as damage_delta_2024_usd,

        -- Percentage change in damage vs prior decade
        ROUND(
            SAFE_DIVIDE(
                total_damage_2024_usd - prior_decade_damage_2024_usd,
                NULLIF(prior_decade_damage_2024_usd, 0)
            ) * 100
        , 2)                                            as damage_pct_change,

        -- Percentile rank change: how much did this state's relative risk position shift?
        -- Positive = moved up in risk rankings (got more dangerous relative to other states)
        -- Negative = moved down (got safer relative to other states)
        ROUND(
            damage_percentile_in_decade - COALESCE(prior_decade_damage_percentile, 0)
        , 1)                                            as percentile_rank_change,

        -- Flag: "surprise state" = percentile rank jumped more than 20 points
        -- i.e. moved from bottom half to top quintile or similar
        CASE
            WHEN (damage_percentile_in_decade - COALESCE(prior_decade_damage_percentile, 0)) > 20
            THEN TRUE ELSE FALSE
        END                                             as is_surprise_state

    from with_lag
    -- Exclude first decade per state (no prior decade to compare against)
    where prior_decade_damage_2024_usd is not null
)

select * from with_deltas
order by decade, percentile_rank_change desc
