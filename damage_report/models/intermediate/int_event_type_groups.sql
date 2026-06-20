/*
    int_event_type_groups
    ---------------------
    Joins storm events to the event_type_groups seed to attach a group label
    to every event. This is the only place the 48→10 mapping is applied —
    all downstream models use event_type_group, never event_type_raw.

    Why intermediate and not staging?
    The join to a seed table is business logic (we decided how to group event types),
    not source cleaning. Staging models touch one source table each. This model
    exists to enrich staging output with a decision we made.

    Why not do this join in each mart?
    DRY principle — if we change a group assignment, we change it here once,
    not in four mart models. The seed + this model = single source of truth
    for event type classification.
*/

with events as (
    select * from {{ ref('stg_storm_events') }}
),

event_groups as (
    select * from {{ ref('event_type_groups') }}
),

joined as (
    select
        events.*,
        -- LEFT JOIN: keeps events even if event_type_raw has no match in seed.
        -- Unmatched types get NULL group — caught by not_null test downstream
        -- rather than silently dropping rows.
        event_groups.event_type_group
    from events
    left join event_groups
        on events.event_type_raw = event_groups.event_type_raw
)

select * from joined
