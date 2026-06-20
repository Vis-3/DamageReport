# The Damage Report — Interview Prep Guide
## Phase 0 (Design) + Phase 1 (Ingestion)

---

## How to Use This Guide

For every question: say the answer out loud, not in your head. If you stumble, that's the gap. The goal is to answer any of these in under 60 seconds, confidently, without notes.

---

## Section 1: BigQuery Partitioning & Clustering

### Q: What is partitioning in BigQuery and why did you use it?

**Answer:**
Partitioning divides a table into physical segments based on a column's value. BigQuery uses partition pruning — it won't open a partition that doesn't match your WHERE clause filter. This reduces bytes scanned, which directly reduces cost.

I partitioned by `YEAR` because every dbt model in this project either:
- Filters by year in a WHERE clause (incremental models: `WHERE event_year > MAX(event_year)`)
- Groups by year in aggregations

Partitioning by year means a query for 2020 data only scans the 2020 partition, not 1.8M rows.

---

### Q: What is clustering and how is it different from partitioning?

**Answer:**
Clustering sorts data within each partition by one or more columns. Unlike partitioning, clustering doesn't reduce bytes *billed* — it reduces bytes *scanned* by skipping irrelevant blocks within a partition. So clustering saves time, partitioning saves money.

I clustered by `STATE` first, then `EVENT_TYPE`, because:
- Two of my three mart models filter by state within a year partition
- State comes first because it's the more common first filter
- EVENT_TYPE is secondary — used in aggregations after the state filter

---

### Q: Why partition by year and not by state or event type?

**Answer:**
Partitioning works best on range or equality filters that eliminate large chunks of data. State and event type appear in GROUP BY clauses in my mart models, not WHERE filters. You'd never say "give me only tornado records across all years" — you'd always filter by year first. Year is the column that eliminates the most data before scanning begins.

---

### Q: General rule for partition vs cluster column selection?

**Answer:**
- **Partition** = the column your WHERE clause uses to eliminate 80%+ of data — usually a time dimension or range filter
- **Cluster** = high-cardinality columns you filter or group by *after* the partition filter runs
- Clustering order should reflect the most common first filter

---

## Section 2: Dataset Architecture

### Q: Why three BigQuery datasets instead of one?

**Answer:**
`raw_noaa`, `dbt_staging`, `dbt_marts` are separated for three reasons:

1. **Access control** — in a real org, raw data is locked (only ingestion pipeline writes to it), staging is internal to dbt, marts are what analysts and BI tools query
2. **Clarity** — the boundary between raw, transformed, and served data is explicit
3. **Cost visibility** — you can see storage and query costs per layer separately

Even solo, structuring it this way demonstrates you understand production patterns.

---

### Q: Why are staging and intermediate models views, but marts are tables?

**Answer:**
- **Views** have no storage cost and always read fresh from the layer below them. Staging and intermediate models are cheap transforms — materializing them would waste storage for no query performance benefit since marts are the query target.
- **Tables** are materialized — BigQuery pre-computes and stores the result. Mart models do heavy aggregations over 1.8M rows. Analysts and BI tools query marts repeatedly, so pre-computing them saves query time and cost on every downstream query.

Rule of thumb: materialize where queries land, leave everything upstream as views.

---

## Section 3: Raw Layer Design

### Q: Why did you use an explicit schema instead of BigQuery autodetect?

**Answer:**
NOAA stores damage figures as strings: `"10.00K"`, `"2.5M"`, `"0"`. BigQuery autodetect would try to cast these to FLOAT64, fail, and either error out or silently coerce them to NULL — destroying the data before it reaches dbt.

By defining an explicit schema with `DAMAGE_PROPERTY` and `DAMAGE_CROPS` as STRING, I preserve exactly what NOAA sent. The parsing logic lives in dbt staging where it's versioned, testable, and rerunnable.

General rule: **use autodetect only for exploratory work. Production pipelines always define explicit schemas at system boundaries.**

---

### Q: Why don't you rename or transform columns in the ingestion script?

**Answer:**
The raw layer contract is: ingest exactly what the source gives you. Renaming in Python means the raw table no longer reflects the source system. If NOAA changes a column name, or you need to debug a data issue, you'd be comparing a renamed raw table against the original CSV with no visible mapping.

By keeping raw as-is and renaming in dbt staging, the transformation is:
- **Explicit** — visible in the staging SQL
- **Versioned** — tracked in git
- **Visible in lineage** — dbt docs show that `event_id` came from `EVENT_ID` in raw

This is called **idempotent ingestion** — the raw layer is always reproducible from the source without any logic baked in.

---

### Q: Why is storm events append-only but CPI is full refresh?

**Answer:**
Different update patterns require different write strategies:

- **Storm events (WRITE_APPEND)**: 1.8M rows, new data arrives monthly as new year files. Historical records are immutable — NOAA doesn't revise past events. Appending only new years is cheaper and safer than re-loading everything.
- **CPI (WRITE_TRUNCATE)**: < 100 rows, BLS occasionally revises historical CPI values. Full refresh is trivially cheap on a 30-row table and guarantees we always have the latest official figures.

The principle: **match your write strategy to your data's update pattern, not to a single rule applied everywhere.**

---

### Q: Why does the current year always re-download even if it already exists in BigQuery?

**Answer:**
NOAA updates the current year's file throughout the year as new storm events are reported. A file downloaded in January won't have December's events. So on every monthly GitHub Actions run, the current year's partition is deleted and re-downloaded to pick up new events.

Prior years are truly immutable — once a year is complete, NOAA doesn't revise it — so we skip them if they already exist.

---

## Section 4: Data Design Decisions

### Q: Why did you start from 1996 instead of 1950?

**Answer:**
1996 is the first year NOAA standardized its 48 event type categories. Pre-1996 data uses a different schema and was collected with less infrastructure — fewer storm spotters, no Doppler radar coverage, no digital reporting systems.

Including pre-1996 data in trend analysis would create a **false escalation signal**: event counts appear to grow not because storms are getting more frequent, but because we're getting better at recording them.

I kept pre-1996 data available with an `is_pre_standardization` flag so downstream models can choose to include or exclude it, but I don't mix it into trend lines.

---

### Q: What is the reporting completeness problem and how did you handle it?

**Answer:**
Event counts have grown over time partly because of better reporting infrastructure, not because storms are actually more frequent. This is a **structural confound** — you can't clean your way out of it because the missing records genuinely don't exist.

Key insight: reporting completeness affects **frequency more than severity**. Unreported events are disproportionately small, low-damage events. So:
- Pre-1996 event *counts* are artificially low
- Pre-1996 average *damage per event* is artificially high (only the big storms were captured)

My handling: exclude pre-1996 from trend analysis, and frame frequency findings as "reported event frequency" rather than claiming to measure actual storm frequency.

---

### Q: Why do you adjust for inflation and what base year did you choose?

**Answer:**
A dollar in 1996 is not the same as a dollar in 2024. Without inflation adjustment, comparing damage figures across decades is misleading — a $1M storm in 1996 caused far more real economic damage than a $1M storm today.

I use CPI data from BLS to convert all damage figures to **2024 dollars**. The formula:
```
damage_2024 = damage_year * (CPI_2024 / CPI_year)
```

I chose 2024 as the base year because **interpretability**: readers understand 2024 dollars intuitively. The math is equally valid for any base year — the choice is purely about what feels concrete to your audience.

---

### Q: Why did you use annual average CPI instead of a specific month?

**Answer:**
Storm events happen throughout the year — some in January, some in August. No single month is more representative than any other. The annual average (CPI period M13, which BLS pre-computes and publishes officially) best represents the price level across the whole year a storm occurred.

December would be right if you cared about year-end valuation (financial contexts). Mid-year values are used in some academic research. Annual average is the most defensible for events distributed across a calendar year.

---

### Q: What is the exposure normalization problem and why didn't you fix it?

**Answer:**
Raw dollar damage conflates two things:
1. Storm intensity (what we actually care about)
2. Exposure growth — more buildings, more expensive buildings, more people in storm paths

A Category 3 hurricane hitting Miami in 2024 causes more dollar damage than the same storm in 1960 not because it's worse, but because there's more to destroy.

CPI adjustment handles inflation. It does **not** handle exposure growth.

The correct fix would be normalizing by state-level annual population and housing stock — damage per capita, deaths per million residents. This would require a third data source and third ingestion pipeline.

I made a deliberate scope decision: this is a DE portfolio project, not an econometrics paper. The showcase is the dbt pipeline. I document the limitation explicitly in model descriptions: *"damage figures are CPI-adjusted but not exposure-normalized — growth in absolute damage reflects both storm intensity changes and population/property growth."*

Knowing the limitation and naming it precisely is more impressive than papering over it with a half-implemented fix.

---

## Section 5: Event Type Grouping

### Q: Why did you map 48 NOAA event types to 10 groups? Why a seed file, not a macro?

**Answer:**
48 event types are too granular for trend analysis — you'd have noisy, thin time series for rare event types. Grouping to 10 categories gives enough granularity to tell distinct analytical stories while having enough events per group for statistical meaning.

I chose a **seed file** (CSV checked into the repo) rather than a CASE statement in a macro because:
- The mapping is **data**, not **logic** — it represents human decisions about categorization
- A CSV is human-readable and diffable in git — a stakeholder could open it in Excel and verify it
- When NOAA adds a new event type, the change is one row in a CSV with a clear git diff, not a SQL edit buried in a macro

Rule: **if it's a lookup table, it belongs in a seed. If it's a transformation, it belongs in a macro.**

---

### Q: Walk me through your 10 event type groups and one grouping decision you'd defend.

**Answer:**
Groups: TORNADO, FLOOD, HURRICANE, WINTER_STORM, HEAT, DROUGHT, FIRE, HAIL, WIND, MARINE, OTHER

Decision I'd defend: **I split DROUGHT out of HEAT into its own group.**

Initially I grouped them together because both are heat-related. But drought has a fundamentally different damage mechanism — it causes crop loss and economic attrition over months, not acute infrastructure damage in hours. Separating them lets the economic impact mart tell a cleaner story: drought damage is crop-dominated and geographically concentrated in agricultural states, heat damage is human-cost dominated. Mixing them would obscure both signals.

---

## Section 6: Python Ingestion Architecture

### Q: How does your ingestion script handle first run vs incremental monthly runs?

**Answer:**
The script queries BigQuery for existing years at startup. Then:
- **First run**: no years exist → downloads all years from 1996 to current year
- **Monthly re-run**: existing years found → skips them, downloads only missing years + current year (current year always refreshes because NOAA updates it mid-year)

This makes the script **idempotent** — you can run it multiple times without duplicating data.

---

### Q: Why did you use latin-1 encoding for NOAA CSV files?

**Answer:**
NOAA CSVs contain special characters in location names and narratives that aren't valid UTF-8. Reading with UTF-8 encoding raises a decode error mid-file. latin-1 (ISO-8859-1) is a superset of ASCII that maps all 256 byte values to characters, so it never raises a decode error — it's the safe fallback for files of unknown or mixed encoding.

---

## Section 7: Concepts to Know Cold

### Idempotent ingestion
Running the same ingestion script multiple times produces the same result — no duplicate data, no data loss. Achieved by checking existing years before downloading and using append/truncate write strategies appropriately.

### Raw layer contract
The raw layer stores data exactly as received from the source — no renaming, no parsing, no transformation. Any change to source data is visible by comparing raw to the original. Transformations belong in dbt where they're versioned and testable.

### Partition pruning
BigQuery eliminates entire partitions before scanning any data when your WHERE clause filters on the partition column. This is a billing reduction, not just a performance improvement.

### Survivorship bias (in this project's context)
Pre-1996 storm data only captured large, damaging events — smaller events went unrecorded. This makes early averages appear artificially high. Including pre-1996 data in trend analysis without a caveat would suggest storms were more severe historically than they actually were relative to today's complete reporting.

### Deflator formula
```
damage_in_2024_dollars = historical_damage * (CPI_2024 / CPI_historical_year)
```
A deflator > 1 means historical dollars were worth less than 2024 dollars (inflation happened). A 1996 deflator of ~2.0 means $1 in 1996 = $2 in 2024.

---

## Section 8: dbt Staging Models

### Q: What is the job of a staging model? What must it always do and never do?

**Answer:**
A staging model has one job: make source data usable for downstream models.

**Must always:** one staging model = one source table. `stg_storm_events` touches only `raw_noaa.storm_events`. One-to-one relationship, always.

**Must never:** join across tables, aggregate, or apply business logic. No GROUP BY, no joins, no filtering based on business rules. If you join CPI into staging, you've hidden a business decision inside what looks like a cleaning layer — the next person reading your code won't find it where they expect it.

What staging does: cast types, rename to snake_case, parse malformed strings, standardize nulls, generate surrogate keys.

---

### Q: Why did you generate a surrogate key in staging instead of using NOAA's EVENT_ID?

**Answer:**
NOAA's `EVENT_ID` is not unique across years — they reuse IDs. A surrogate key of `EVENT_ID || '-' || YEAR` gives a stable, globally unique identifier across the entire dataset. Downstream models can use `event_id` as a reliable join and deduplication key without knowing anything about NOAA's internal ID scheme.

General rule: **generate surrogate keys in staging where you control uniqueness guarantees. Don't trust source system IDs to be globally unique.**

---

### Q: Why did you use SAFE_CAST instead of CAST in the damage parsing macro?

**Answer:**
NOAA has rows where the damage field is a bare suffix with no numeric part — just `"K"` or `"M"` with nothing before it. `REPLACE("K", "K", "")` gives `""`, and `CAST("" AS FLOAT64)` throws a runtime error that fails the entire query.

`SAFE_CAST` returns NULL instead of erroring on unparseable values. For damage data, NULL is the correct sentinel — it means "unparseable/not recorded" and BigQuery's SUM/AVG ignore NULLs automatically, so aggregations remain correct.

---

### Q: What did your dbt tests find when you first ran them, and what did you do about it?

**Answer:**
Three tests failed on first run — none were code bugs, they were the tests revealing real data characteristics:

1. **`accepted_values` on `event_type_raw`** — 12 event types in the data weren't in our seed file. NOAA added new types (Marine Lightning, Heavy Rain, Sneakerwave, etc.) that we hadn't mapped. Fixed by adding them to the seed CSV with appropriate group assignments.

2. **`not_null` on `state`** — one 2003 Waterspout in Guam Coastal Waters had NULL state. Guam is a US territory, not a state. NOAA legitimately leaves STATE null for marine/territory zones. Removed the incorrect test and updated the column description.

3. **`relationships` on `event_year`** — 10,652 rows for 2026 had no matching CPI record. BLS hasn't published 2026 annual CPI yet — unavoidable. Fixed by adding `where: "event_year < 2026"` to scope the test to years with available CPI data.

This is exactly what tests are for: they don't just catch bugs, they surface assumptions about your data that need to be validated or revised.

---

### Q: Why does the CPI relationship test have a WHERE clause excluding 2026?

**Answer:**
BLS publishes annual CPI averages after the year ends. Our storm data includes 2026 events (NOAA updates continuously), but the 2026 CPI annual average doesn't exist yet. Without the WHERE clause, the test would permanently fail for the current partial year on every run.

The WHERE clause makes the test honest: *"every event year up to 2025 must have a CPI record."* 2026 is excluded because the CPI data structurally can't exist yet — it's not a data quality problem, it's a timing constraint.

---

## Section 9: Intermediate Models

### Q: What is the job of an intermediate model and how is it different from staging?

**Answer:**
Staging = source cleaning, one table in, clean rows out, no business logic.
Intermediate = business logic and enrichment, can join multiple models, no aggregation.

Intermediate models answer questions like "what group does this event belong to?" and "what is this damage worth in 2024 dollars?" — those are business decisions, not source cleaning. They live in intermediate because they're reusable enrichment that multiple mart models need, but they don't aggregate into the final analytical shape.

---

### Q: Why did you build int_event_type_groups as a separate model instead of joining the seed directly in int_events_enriched?

**Answer:**
Separation of concerns. `int_event_type_groups` has one job: attach a group label to every event. `int_events_enriched` has one job: join all enrichments together. If the grouping logic needs to change, you know exactly where to look.

Also, if we ever needed the grouped events without CPI enrichment — for a model that doesn't care about inflation — `int_event_type_groups` is available as a standalone ref.

---

### Q: Why use LEFT JOIN everywhere in intermediate models instead of INNER JOIN?

**Answer:**
Loud failures over silent data loss. INNER JOINs silently drop rows that don't match. In a pipeline with 1.8M events:
- An unmapped event type → row dropped from every mart, counts quietly understated
- A NULL state (marine zone) → row dropped from geographic analysis
- A missing CPI year (2026) → rows dropped from economic analysis

LEFT JOIN keeps all rows. Missing enrichments become NULL values, which downstream tests catch explicitly. You always know exactly how many rows have incomplete enrichment rather than having them vanish.

---

### Q: Why put the inflation adjustment in int_events_enriched instead of each mart?

**Answer:**
Single source of truth. If we change the CPI base year from 2024 to 2023, or if BLS revises historical CPI values and we re-ingest, the change propagates to all four marts automatically. Four mart models each joining CPI independently could drift out of sync — one mart might use a different deflator than another, producing inconsistent figures across the dashboard.

DRY principle applied to SQL: compute once in intermediate, consume many times in marts.

---

## Section 10: Mart Models

### Q: Why are mart models materialized as tables and not views?

**Answer:**
Mart models aggregate 1.8M rows into a few hundred summary rows. If they were views, every dashboard query would re-run the full aggregation over the raw data — expensive and slow. As materialized tables, BigQuery pre-computes the result once during `dbt run`. Downstream queries hit a pre-built table of 300 rows, not a view over 1.8M rows.

Rule: **materialize where queries land. Leave everything upstream as views.**

---

### Q: Why did you choose full refresh over incremental for the mart models?

**Answer:**
All four marts produce a few hundred rows at most — 30 years × 10 event groups, 50 states × 8 decades. A full rebuild costs pennies and runs in seconds.

Incremental materialization requires defining a `unique_key` for deduplication and a WHERE clause for filtering new rows. That complexity is only justified when the full refresh is genuinely expensive — billions of rows aggregating into millions of output rows.

The principle: **incremental is a performance optimization, not a best practice. Apply it only when full refresh is slow or expensive.**

---

### Q: What does `avg_damage_per_event_2024_usd` tell you that total damage doesn't?

**Answer:**
Total damage conflates frequency with severity. Hurricanes cause more total damage than tornadoes partly because there are more of them. `avg_damage_per_event` normalizes for frequency — it tells you how destructive each event type is *when it actually occurs*.

From the data: Hurricane = $25.5M avg per event, WIND = $70K avg per event. WIND events are 60x more frequent but 364x cheaper per occurrence. That's the "efficiency of destruction" metric — it answers a different question than total damage.

---

### Q: How does mart_surprise_states identify which states are "surprises"?

**Answer:**
It uses a LAG window function to compare each state's damage percentile rank in the current decade against the prior decade:

```sql
LAG(damage_percentile_in_decade) OVER (PARTITION BY state ORDER BY decade)
```

A state that was at the 25th percentile in the 1990s and is at the 85th percentile in the 2000s jumped 60 points — flagged as `is_surprise_state = TRUE` (threshold: >20 point jump).

This separates states that were always high-risk (Florida, Texas) from states where risk concentrated unexpectedly (Vermont from Hurricane Irene flooding, Hawaii from Maui wildfires).

---

### Q: What are the key analytical findings from your marts?

**Answer — be ready to state these confidently:**

1. **2005 is a Katrina outlier** — $161.7B in damage, roughly 4-10x any other year. Single event dominates the economic impact story. Always caveat this explicitly.

2. **Hurricane is most destructive per occurrence** — $25.5M avg damage per event vs WIND at $70K. But WIND has 636K events vs Hurricane's 10K — frequency tells a completely different story than per-event severity.

3. **Surprise states tell the real geographic story** — Hawaii +70 percentile points in 2020 (Maui wildfires), Vermont +43 in 2010 (Irene flooding), New Jersey +43 in 2010 (Irene + Sandy). Risk is not static — it concentrates in unexpected places as climate patterns shift.

4. **Damage figures are CPI-adjusted but not exposure-normalized** — growth in absolute damage reflects both storm intensity changes and population/property growth. Always frame as "cost trends" not "intensity trends."

---

## Section 11: CI/CD, Docs, and Visualizations

### Q: What does your GitHub Actions workflow do and why is it structured as two jobs?

**Answer:**
Two jobs: `dbt-test` and `dbt-docs`.

`dbt-test` runs on every push and every PR — it runs `dbt seed`, `dbt run`, and `dbt test`. This is the quality gate: no code merges without a green pipeline.

`dbt-docs` only runs on pushes to main, and only if `dbt-test` passes. It regenerates the docs and commits them back to the repo. Publishing docs from a broken pipeline would mean the published lineage doesn't match the actual code.

The `[skip ci]` tag in the docs commit message prevents an infinite loop — without it, the docs commit would trigger another CI run which would commit docs again endlessly.

---

### Q: Why does dbt docs not work on GitHub Pages static hosting?

**Answer:**
dbt's `index.html` is a single-page app that fetches `manifest.json` and `catalog.json` via XHR requests at runtime. GitHub Pages serves static files, but the XHR requests fail because:
1. The files are there but dbt appends a cache-busting query string (`?cb=randomnumber`) that doesn't match the actual filenames
2. GitHub Pages doesn't support the dynamic path resolution dbt expects

The correct production approach is to serve dbt docs via a web server (`dbt docs serve`). For a portfolio, the lineage screenshot + instructions to run locally is the honest, standard approach.

---

### Q: What are your key analytical findings and their limitations?

**Answer — three findings, each with a caveat:**

**Finding 1:** Storm events are being reported more frequently — ~48K/year in 1996 to ~70K/year in 2024.
**Caveat:** Part of this increase is better reporting infrastructure, not more actual storms. The trend is real but overstated.

**Finding 2:** Hurricanes are the most destructive event type per occurrence at $25.5M average damage per event. WIND events occur 60× more frequently but cause only $70K per event.
**Caveat:** Damage figures are CPI-adjusted but not exposure-normalized. Higher dollar damage partly reflects more buildings in hurricane paths, not just more intense storms.

**Finding 3:** Geographic risk is shifting. California surged in the 2010s (wildfires), Louisiana in the 2020s (Ida). Hawaii jumped 70 percentile points in the 2020s (Maui wildfires).
**Caveat:** The 2020s decade is only 5 years complete — figures will change as the decade finishes.

---

### Q: Why did you annotate Katrina on the damage per event chart instead of removing it?

**Answer:**
Removing it would be dishonest — a trend line that hides a $161.7B event is misleading. Labeling it as an outlier is both more honest and more compelling as a data story: it shows the analyst understands the data well enough to distinguish a structural trend from a single catastrophic event.

The annotation makes the chart's message clearer: "severity per event is noisy with no clear upward trend, except for major hurricane years which are outliers, not a trend."

---

## Quick-Fire Questions

| Question | One-line answer |
|---|---|
| Why BigQuery sandbox? | Free tier: 10GB storage + 1TB queries/month — no credit card |
| Why dbt Core not dbt Cloud? | Free, open source, shows you can operate without a managed platform |
| Why partition by year not month? | Storm events analysis is always annual — month-level partitions would be over-granular |
| Why cluster STATE before EVENT_TYPE? | Two of three mart models filter by state first |
| Why 1996 start date? | First year of standardized 48-category NOAA schema |
| Why 2024 as CPI base year? | Audience interpretability — readers understand 2024 dollars |
| Why seed file for event types? | It's data (a lookup table), not logic — belongs in CSV not SQL |
| Why not normalize for population? | Scope decision — DE showcase, not econometrics. Limitation documented explicitly. |
| What's the raw layer contract? | Store exactly what the source sends. No transformations. |
| Why WRITE_APPEND for storm events? | Historical records are immutable, new years append cleanly |
| Why WRITE_TRUNCATE for CPI? | 30-row table, BLS revises historical values — full refresh is safer and trivially cheap |
| What does a staging model never do? | Join tables, aggregate, or apply business logic — one source table in, clean rows out |
| Why surrogate key instead of NOAA EVENT_ID? | NOAA reuses EVENT_IDs across years — EVENT_ID + YEAR gives a globally unique key |
| Why SAFE_CAST in damage macro? | Bare suffixes like "K" with no number cast to "" which CAST errors on — SAFE_CAST returns NULL instead |
| Why WHERE clause on CPI relationship test? | BLS hasn't published 2026 annual CPI yet — excluding current year makes the test permanently valid |
| What did failing tests reveal? | 12 unmapped event types, legitimate NULL states for marine zones, 2026 events with no CPI yet |
| Why LEFT JOIN in int_event_type_groups? | INNER JOIN silently drops unmapped event types — LEFT JOIN + downstream not_null test fails loudly instead |
| Why one central enrichment model? | CPI deflator logic in one place — if base year changes, fix it once not in four mart models |
| How is decade calculated? | FLOOR(event_year / 10) * 10 — gives 1990 for 1990-1999, sortable and groupable directly |
| Why seed for state regions? | It's a lookup table (data), not logic — human decision about regionalization belongs in CSV not SQL |
| What does a 1996 deflator of ~2.0 mean? | $1 in 1996 = $2 in 2024 — multiply historical damage by deflator to get 2024 dollars |
| Why LEFT JOIN CPI in int_events_enriched? | 2026 events have no CPI yet — LEFT JOIN keeps them with NULL adjusted damage rather than dropping them |
| Why full refresh for all marts? | Output is hundreds of rows — full rebuild costs pennies. Incremental adds complexity with no performance benefit at this scale. |
| Why SAFE_DIVIDE everywhere in marts? | Rare event types can have zero event counts — SAFE_DIVIDE returns NULL instead of a division by zero error |
| What does avg_damage_per_event tell you? | Destructiveness per occurrence — separates "frequent but cheap" (WIND) from "rare but catastrophic" (HURRICANE) |
| What is mart_surprise_states measuring? | Decade-over-decade percentile rank change — states that jumped >20 points in damage ranking are flagged as surprise states |
| Why does mart_surprise_states ref mart_geographic_risk? | Reuses already-aggregated and ranked data rather than re-aggregating 1.8M rows — single source of truth for state/decade risk metrics |
| What does the 2005 damage spike represent? | Hurricane Katrina — $161.7B in 2024 dollars, a statistical outlier that should be called out explicitly in analysis |
| Which event type is most destructive per occurrence? | Hurricane at $25.5M avg damage per event — 10,590 events vs WIND's 636,791 events at $70K avg |
| What belongs in dbt docs vs README? | dbt docs = model/column level detail for pipeline contributors; README = project narrative and findings for external audience |
| Why does dbt docs need a local server? | index.html fetches manifest.json and catalog.json via XHR — static hosting breaks because the JSON files 404 on GitHub Pages |
| What does the CI workflow do on a PR vs a push to main? | PR: dbt run + test only. Push to main: run + test, then publish docs if tests pass. Never publish from a broken pipeline. |
| Why [skip ci] in the docs commit message? | Prevents infinite loop — the docs commit would trigger another CI run which would commit docs again |
| What does the monthly GitHub Actions schedule do? | Runs dbt seed + run + test to pick up new NOAA data published each month |
| What story does the choropleth tell across decades? | 2000s: Gulf Coast dominates (Katrina). 2010s: California surges (wildfires), NJ lights up (Sandy). 2020s: Louisiana spikes (Ida). Risk is shifting, not just growing. |
| Why annotate Katrina on the damage per event chart? | Hiding the outlier would be dishonest. Labeling it shows the spike is one event, not a trend — and is more compelling as a data story. |

---

## Section 12: Data Science Extension

### Q: What statistical test did you use to assess trend significance and why?

**Answer:**
Mann-Kendall non-parametric trend test. It tests whether there is a monotonic trend in a time series — consistently going in one direction, not necessarily a straight line.

I chose it over linear regression for three reasons:
1. It makes no normality assumption — storm damage is heavily right-skewed
2. It's robust to outliers — a single Katrina year doesn't invalidate the test
3. It measures monotonic trend, which is the right question: "is it consistently going up?" not "does a straight line fit well?"

**Results:** Frequency shows a statistically significant increasing trend (p<0.0001, Tau=+0.655, Sen's slope +813 events/year). Severity per event shows no significant trend (p=0.134, Tau=-0.195). This confirms we're reporting more storms but individual storms are not becoming more destructive on average.

---

### Q: What is Sen's slope and why is it better than OLS slope for this data?

**Answer:**
Sen's slope is the median of all pairwise slopes between observations. It's robust to outliers — one extreme value (Katrina 2005) shifts OLS slope significantly but barely moves the median slope.

For storm damage data with a known extreme outlier, Sen's slope gives a more representative estimate of the underlying rate of change.

---

### Q: How does Isolation Forest work and why did you use it for anomaly detection?

**Answer:**
Isolation Forest randomly partitions the feature space by selecting a random feature and a random split point. Anomalous points are isolated with fewer splits — they're rare and far from the bulk of the data, so they appear close to the root of the tree. The anomaly score is the average depth across all trees — lower depth = more anomalous.

I chose it over z-score because:
- Anomalies in multi-dimensional space aren't captured by single-column z-scores
- A year can be anomalous on the combination of frequency + severity + deaths without being an outlier on any single dimension
- It's unsupervised — no labels needed

**Results:** Three years flagged — 2005 (Katrina, extreme severity), 2011 (peak frequency + high deaths from Joplin tornado season), 2025 (anomalously low damage per event due to partial year data).

---

### Q: Why did anomaly detection flag 2025 as anomalous even though nothing extreme happened?

**Answer:**
2025 has the second highest event count but the lowest average damage per event in the dataset ($64K). That combination — many events, minimal damage — is statistically unusual relative to the 1996-2024 distribution. It's anomalous in the opposite direction from 2005.

This illustrates a key property of anomaly detection: it flags deviation from the norm in any direction, not just extremes. 2025 is a data completeness artifact — NOAA is still filing damage reports for recent events. The model correctly identified structural unusualness; interpreting *why* requires domain knowledge.

---

### Q: What is class imbalance and how did you handle it in the fatality prediction model?

**Answer:**
0.60% of 1.72M events resulted in fatalities — 164 non-fatal events for every 1 fatal event. A naive model predicting "non-fatal" for every event would be 99.4% accurate but completely useless.

I handled it with `class_weight='balanced'`, which scales the loss penalty inversely proportional to class frequency. The model is penalised much more heavily for misclassifying a fatal event than a non-fatal one.

I chose this over SMOTE because SMOTE would generate ~280K synthetic fatal events on a 1.7M row dataset — memory-heavy, slow, and synthetic data that may not reflect real storm physics. `class_weight='balanced'` is mathematically equivalent to oversampling for logistic regression with no synthetic data.

---

### Q: Why did you use a temporal train/test split instead of random split?

**Answer:**
A random split would let the model train on 2020 data and test on 2015 data. In production, a model only ever sees historical data — it cannot use future information to predict the past. A random split creates data leakage and produces optimistically biased evaluation metrics.

Temporal split (train 1996-2010, test 2011-2025) reflects the real deployment scenario: train on everything you know, predict on what comes next.

---

### Q: Why is accuracy the wrong metric for this problem?

**Answer:**
With 0.60% positive rate, a model predicting "non-fatal" for every event achieves 99.4% accuracy. It has learned nothing about fatality. Accuracy is misleading for imbalanced classification because the majority class dominates the metric.

Better metrics:
- **PR-AUC** — area under the precision-recall curve. Focuses only on the positive class. Harder to game with a majority-class predictor. Primary metric.
- **ROC-AUC** — ability to rank a fatal event above a non-fatal one. Good general measure, slightly optimistic with severe imbalance.
- **Recall at threshold** — operational metric: what fraction of fatal events does the model catch at a chosen operating threshold?

---

### Q: Why did logistic regression outperform gradient boosting on PR-AUC after adding lag features?

**Answer:**
Lag features (prior-year state deaths, event count, damage) have a relatively linear relationship with fatality risk — regions under sustained storm stress show predictably elevated risk the following year. Logistic regression exploits linear relationships directly, so PR-AUC jumped from 0.069 to 0.135.

Gradient boosting already approximated this signal non-linearly through interactions between event type, region, and decade. Adding explicit lag features changed the feature space without providing genuinely new information for GB, slightly reducing PR-AUC.

This is the key lesson: **feature engineering mattered more than model complexity.** The simpler model won after proper feature engineering.

---

### Q: What is the difference between GB built-in feature importance and SHAP?

**Answer:**
- **Built-in importance** measures how often a feature is used to split trees and how much it reduces impurity in aggregate. It's biased towards high-frequency, high-cardinality features — HAIL has 636K events, so it gets split on constantly and appears most important.
- **SHAP (Shapley values)** measures the marginal contribution of each feature to each individual prediction. It's theoretically grounded in game theory. SHAP importance = mean absolute SHAP value across all predictions.

In this project, built-in importance ranked `lag_event_count` and `lag_damage` as top features. SHAP confirmed only `lag_deaths` carries genuine marginal predictive value. Built-in overcounted the other two because they're continuous with many possible split points.

Rule: **use SHAP for trustworthy feature importance. Use built-in importance only as a fast approximation.**

---

### Q: What is threshold optimisation and why did you do it?

**Answer:**
Classification models output a probability. The threshold is the cutoff above which you predict positive (fatal). Default is 0.50, but this is arbitrary.

For safety-critical use cases, the cost of a false negative (missing a deadly event) far exceeds the cost of a false positive (unnecessary warning). You lower the threshold so the model flags more events as potentially fatal, accepting more false positives to catch more true positives.

I optimised for maximum recall subject to a precision floor of 5% — below 5% precision, every warning is noise regardless of recall.

**Results:**
- LR: threshold=0.82, recall=0.46
- GB: threshold=0.02, recall=0.51

GB needed threshold=0.02 because its raw probabilities are calibrated towards the majority class — common with imbalanced data even after class weighting. Explicit threshold tuning corrected for this.

---

### Q: What did SHAP reveal about fatality drivers that built-in importance didn't?

**Answer:**
SHAP dot plot shows direction and magnitude per prediction:
- **HEAT** — high feature value (it IS a heat event) pushes predictions strongly positive (+2 to +4 SHAP). Heat kills disproportionately relative to economic damage.
- **HAIL** — high feature value (it IS hail) pushes predictions negative. Being hail decreases fatality risk. More importantly, NOT being hail is a positive signal — it's a proxy for "something more dangerous than hail."
- **lag_deaths** — prior year deaths in a state increases current fatality predictions. Regions under sustained storm stress show elevated risk.
- **region_WEST** — elevated positive SHAP values. Wildfire signal — western events are more often fatal relative to damage.

The HAIL finding was counterintuitive — SHAP revealed the model uses it as a negative indicator, not a positive one.

---

## Quick-Fire DS Questions

| Question | One-line answer |
|---|---|
| Why Mann-Kendall not linear regression? | No normality assumption, robust to outliers like Katrina, tests monotonic trend not linear fit |
| What does Tau=+0.655 mean? | Strong positive monotonic relationship between time and event count — 0.655 out of 1.0 |
| What does p=0.134 on severity mean? | Above 0.05 threshold — cannot reject null hypothesis of no trend. Severity is flat. |
| What is Sen's slope? | Median of all pairwise slopes — robust to outliers unlike OLS slope |
| Why did you log-transform before Isolation Forest? | Right-skewed damage data compresses normal years and stretches the tail — log pulls the tail in |
| Why StandardScaler before Isolation Forest? | Without scaling, damage ($billions) dominates the anomaly score over event count (thousands) |
| Why fit scaler on train only? | Fitting on full dataset leaks test set distribution into training — scaler would learn future data |
| Why is 2025 flagged as anomalous? | Highest event count but lowest damage per event — opposite direction anomaly from 2005, caused by partial year data |
| What does contamination=0.10 mean? | Tell the model to expect ~10% of data to be anomalous — controls how many points get flagged |
| Why class_weight='balanced' not SMOTE? | SMOTE generates 280K synthetic rows on a 1.7M row dataset — slow and synthetic. Balanced weights are mathematically equivalent for LR. |
| Why temporal split not random? | Random split leaks future data into training — model sees 2020 data when predicting 2015 |
| Why PR-AUC over ROC-AUC for imbalanced data? | ROC-AUC includes true negative rate which is trivially high — PR-AUC focuses only on positive class performance |
| What is threshold optimisation? | Moving the classification cutoff below 0.5 to increase recall at the cost of precision for safety-critical use cases |
| Why did LR PR-AUC improve with lag features? | Lag features have a linear relationship with risk — LR exploits linear signal directly |
| Why did GB PR-AUC drop with lag features? | GB already captured this signal non-linearly — lag features changed the feature space without new information |
| What is the HAIL SHAP finding? | HAIL is a negative predictor — its absence is a stronger positive signal than its presence. The model uses "not hail" as a proxy for something more dangerous. |
| Why is built-in GB importance unreliable? | Biased towards high-cardinality continuous features — HAIL has 636K events so trees split on it constantly regardless of real predictive value |
| What does a ROC-AUC of 0.84 mean in plain English? | The model ranks a fatal event above a non-fatal one 84% of the time — significantly better than random (50%) |
