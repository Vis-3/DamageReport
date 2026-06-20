# The Damage Report

[![dbt CI](https://github.com/Vis-3/DamageReport/actions/workflows/dbt_ci.yml/badge.svg)](https://github.com/Vis-3/DamageReport/actions/workflows/dbt_ci.yml)

A production-grade data engineering pipeline over 1.8M NOAA storm events (1996–2026), answering three questions: **is extreme weather getting worse, where is it hitting hardest, and what does it cost?**

---

## The Story

### Act 1 — More Storms, But Is That Real?

Reported storm events have grown from ~48,000 per year in 1996 to ~70,000 per year in 2024. At first glance, that looks alarming. But this trend has a confound: NOAA's reporting infrastructure — storm spotters, Doppler radar coverage, digital reporting systems — expanded significantly over this period. Some of the increase is real. Some of it is us getting better at counting.

This is why pre-1996 data is excluded from trend analysis entirely. The 1996 standardization of 48 event categories was the first point where the data is structurally comparable year-over-year.

![Event frequency over time](docs/screenshots/chart1_event_frequency.png)
*[Interactive version](docs/charts/chart1_event_frequency.html)*

---

### Act 2 — Are Storms Getting Worse Per Event?

Frequency going up doesn't mean severity is going up. To answer that, we look at average damage per event — normalized to 2024 dollars so we're comparing apples to apples across decades.

The answer is: noisy. No clean upward trend in severity per event — except for one unmistakable spike. **2005: $3M average damage per event, 4–10× a typical year.** That's Hurricane Katrina, which accounted for a disproportionate share of that year's total damage. The chart labels it explicitly rather than hiding it — it's an outlier, not a trend.

![Damage per event over time](docs/screenshots/chart2_damage_per_event.png)
*[Interactive version](docs/charts/chart2_damage_per_event.html)*

---

### Act 3 — Which Event Types Cost the Most?

Not all storms are equal. When we rank event types by average damage per occurrence — not total damage, which favors frequent events — the picture shifts dramatically.

**Hurricanes: $25.5M average damage per event.** Fire: $5.7M. Flood: $1.5M. Wind events — the most common event type at 636,000 occurrences — cause just $70K per event. Total damage and per-event damage tell completely different stories about which event types to prioritize.

![Economic impact by event type](docs/screenshots/chart3_economic_impact.png)
*[Interactive version](docs/charts/chart3_economic_impact.html)*

---

### Act 4 — Where Is It Hitting?

Storm damage isn't evenly distributed. The animated choropleth below shows total damage by state across three decades — and the map changes.

**2000s:** Texas and Louisiana dominate in dark red. That's Katrina (2005) and a historically active Gulf Coast hurricane season. California is notable but not dominant.

**2010s:** California surges to the top — wildfires. Texas stays dark. New Jersey lights up (Sandy, 2012). The Northeast is now on the map in a way it wasn't before.

**2020s (partial):** Louisiana spikes to darkest red — Hurricane Ida (2021). Texas fades relative to prior decades. California lightens, though the decade is only half complete.

Risk is not static. It's shifting.

![Geographic risk by decade](docs/screenshots/chart4_geographic_risk.png)
*[Interactive version — use the slider to move across decades](docs/charts/chart4_geographic_risk.html)*

---

### Act 5 — The Surprise States

The final question: which states saw the biggest unexpected risk jumps? Not the states that were always high-risk, but the ones that moved.

**Hawaii, 2020s: +70 percentile points.** The Maui wildfires pushed Hawaii from a low-risk state to one of the highest-damage states in the country in a single decade.

**Vermont and New Jersey, 2010s: +43 points each.** Hurricane Irene's inland flooding devastated Vermont — a state not typically associated with hurricane damage. Sandy did the same to New Jersey.

**Oregon, 2020s: +55 points.** Wildfires again. The western US wildfire risk story is real and it shows up clearly in the data.

These aren't flukes. They're signals that the geographic distribution of climate risk is shifting faster than traditional risk models account for.

![Surprise states](docs/screenshots/chart5_surprise_states.png)
*[Interactive version](docs/charts/chart5_surprise_states.html)*

---

## Analytical Limitations

- **Damage figures are CPI-adjusted but not exposure-normalized.** Growth in absolute damage reflects both storm intensity changes and population/property growth in storm-prone areas. Findings should be framed as cost trends, not intensity trends.
- **Reporting completeness is a gradient.** Event counts have grown partly because of better reporting infrastructure, not only because storms are more frequent.
- **2020s decade is only half complete.** Per-decade figures for 2020–2029 will change as the decade finishes.
- **2026 data is partial.** NOAA updates the current year continuously. Damage figures for 2026 are excluded from analysis.

---

## Pipeline Architecture

```
NOAA FTP Server ──────────┐
                           ├──► Python ingestion ──► BigQuery raw layer
BLS CPI Data ─────────────┘    (explicit schema,      (partitioned by year,
                                 append-only)           clustered by state)
                                      │
                              dbt staging (views)
                              type casting · damage string parsing · surrogate keys
                                      │
                              dbt intermediate (views)
                              event type grouping · CPI inflation adjustment · region mapping
                                      │
                              dbt mart models (tables)
                         ┌────────────┼────────────┐
                         │            │            │
                   Severity      Geographic    Economic
                   Trends          Risk         Impact
                                      │
                              mart_surprise_states
                              (decade-over-decade risk shifts)
```

![dbt Lineage Graph](docs/lineage.png)

---

## Tech Stack

| Component | Technology |
|---|---|
| Language | Python 3.12 |
| Package manager | uv |
| Cloud warehouse | BigQuery (sandbox — free tier) |
| Transformation | dbt Core 1.11 |
| Ingestion | Python + google-cloud-bigquery |
| Data quality | dbt tests (24 tests across staging + marts) |
| CI/CD | GitHub Actions (test on push, monthly refresh) |
| Version control | Git |

---

## dbt Model Structure

```
sources.yml
└── raw_noaa.storm_events      1.8M rows, 1996-2026
└── raw_noaa.cpi_deflator      BLS annual CPI averages

staging/ (views)
├── stg_storm_events           damage string parsing, type casting, surrogate key
└── stg_cpi_deflator           CPI index standardization

intermediate/ (views)
├── int_event_type_groups      48 NOAA types → 10 groups via seed file
└── int_events_enriched        CPI inflation adjustment, decade, region

marts/ (tables)
├── mart_severity_trends       frequency + severity by year + event group
├── mart_geographic_risk       damage + deaths by state + decade, percentile ranked
├── mart_economic_impact       damage per event type, ranked by destructiveness
└── mart_surprise_states       decade-over-decade risk position changes (LAG window)
```

---

## Key Design Decisions

**Partitioning + clustering:** Raw table partitioned by `YEAR` (all mart queries filter by year), clustered by `STATE` then `EVENT_TYPE` (two of three mart models filter by state first).

**Explicit schema over autodetect:** NOAA stores damage as strings (`"10.00K"`, `"2.5M"`). Autodetect would silently coerce these to NULL. Schema is defined explicitly; parsing happens in dbt staging via a Jinja macro.

**Seed files over CASE macros:** Event type grouping (48→10) and state region mapping live in CSV seed files, not SQL macros. The mapping is data, not logic — human decisions belong in version-controlled CSVs that a stakeholder can read without touching SQL.

**Full refresh marts:** All mart models are full refresh tables. Output is hundreds of rows — incremental adds complexity with no performance benefit at this scale.

**Left joins everywhere:** Intermediate models use LEFT JOIN to preserve all events even when enrichment is missing (unmapped event types, marine zones with no state, 2026 events with no CPI). Silent row drops are worse than NULL values caught by downstream tests.

---

## How to Run

### Prerequisites
- Python 3.12+, uv
- GCP project with BigQuery API enabled
- Service account with BigQuery Data Editor + Job User roles

### Setup
```bash
git clone https://github.com/Vis-3/DamageReport.git
cd DamageReport
uv sync

# Set your keyfile path
export KEYFILE="path/to/your/keyfile.json"
export PROJECT="your-gcp-project-id"
```

### Ingest raw data
```bash
# NOAA storm events (1996-2026, ~1.8M rows, takes 10-20 min)
python ingestion/ingest_noaa.py --project $PROJECT --keyfile $KEYFILE

# BLS CPI deflator (download cu.data.1.AllItems from BLS first)
python ingestion/ingest_cpi.py --project $PROJECT --keyfile $KEYFILE --local-file path/to/cu.data.1.AllItems
```

### Run dbt pipeline
```bash
cd damage_report
dbt seed        # load event type groups + state regions
dbt run         # build all 8 models
dbt test        # run 24 data quality tests
```

### View dbt lineage locally
```bash
dbt docs generate
dbt docs serve  # opens at http://localhost:8080
```

---

## Resume Bullet

> Built The Damage Report, a climate risk analytics pipeline over 1.8M NOAA storm events — dbt Core on BigQuery with Jinja macros for damage string parsing, seed-driven event type classification, CPI inflation adjustment joining BLS data, 24 dbt tests (built-in + relationship tests), and GitHub Actions CI/CD for monthly data refresh.
