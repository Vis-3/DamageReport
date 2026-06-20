/*
    stg_storm_events
    ----------------
    One-to-one with raw_noaa.storm_events. No joins, no aggregations.
    Responsibilities:
      - Rename columns to snake_case
      - Cast types (dates, integers)
      - Parse NOAA damage strings ("10.00K", "2.5M") to FLOAT64 via macro
      - Standardize nulls (empty strings → NULL)
      - Generate a surrogate key (event_id) for downstream joins
      - Flag pre-standardization records (pre-1996 schema is incomparable)
*/

with source as (
    select * from {{ source('raw_noaa', 'storm_events') }}
),

renamed as (
    select
        -- Surrogate key: EVENT_ID is NOAA's identifier but is not guaranteed unique
        -- across years (NOAA reuses IDs). Combining with YEAR gives a stable unique key.
        CAST(EVENT_ID AS STRING) || '-' || CAST(YEAR AS STRING) as event_id,

        -- Event identifiers
        CAST(EVENT_ID   AS STRING) as noaa_event_id,
        CAST(EPISODE_ID AS STRING) as noaa_episode_id,

        -- Time dimensions
        CAST(YEAR AS INT64)                                          as event_year,
        MONTH_NAME                                                   as event_month,
        PARSE_TIMESTAMP('%d-%b-%y %H:%M:%S', BEGIN_DATE_TIME)       as begin_datetime,
        PARSE_TIMESTAMP('%d-%b-%y %H:%M:%S', END_DATE_TIME)         as end_datetime,

        -- Geography
        INITCAP(STATE)  as state,
        STATE_FIPS      as state_fips,
        CZ_NAME         as county_zone_name,
        CZ_TYPE         as county_zone_type,

        -- Event classification
        EVENT_TYPE      as event_type_raw,  -- kept raw for joining to seed
        WFO             as weather_forecast_office,

        -- Human impact — already integers in raw schema
        COALESCE(INJURIES_DIRECT,   0) as injuries_direct,
        COALESCE(INJURIES_INDIRECT, 0) as injuries_indirect,
        COALESCE(DEATHS_DIRECT,     0) as deaths_direct,
        COALESCE(DEATHS_INDIRECT,   0) as deaths_indirect,

        -- Damage parsing: macro expands to a CASE statement inline
        -- NULL means not recorded, 0 means confirmed zero damage
        {{ parse_damage_string('DAMAGE_PROPERTY') }} as property_damage_usd,
        {{ parse_damage_string('DAMAGE_CROPS') }}    as crop_damage_usd,

        -- Tornado-specific fields (NULL for non-tornado events)
        TOR_F_SCALE     as tornado_f_scale,
        CAST(TOR_LENGTH AS FLOAT64) as tornado_length_miles,
        CAST(TOR_WIDTH  AS FLOAT64) as tornado_width_yards,

        -- Narratives — useful for dbt docs and exploratory analysis
        EPISODE_NARRATIVE  as episode_narrative,
        EVENT_NARRATIVE    as event_narrative,

        -- Reporting completeness flag: pre-1996 data uses a different schema
        -- and incomplete reporting infrastructure — exclude from trend analysis
        CASE WHEN CAST(YEAR AS INT64) < 1996 THEN TRUE ELSE FALSE END
            as is_pre_standardization

    from source
)

select * from renamed
