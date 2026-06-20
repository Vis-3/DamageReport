# The Damage Report

[![dbt CI](https://github.com/Vis-3/DamageReport/actions/workflows/dbt_ci.yml/badge.svg)](https://github.com/Vis-3/DamageReport/actions/workflows/dbt_ci.yml)

A production-grade data engineering pipeline over 1.8M NOAA storm events (1996–2026), answering three questions: **is extreme weather getting worse, where is it hitting hardest, and what does it cost?**

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

## Key Findings

**Severity:** 2005 is a $161.7B outlier (Hurricane Katrina — roughly 4–10× any other year). Excluding Katrina, inflation-adjusted damage per event has risen ~40% since 1996, suggesting storms are getting more destructive per occurrence, not just more frequent.

**Geographic risk:** Risk is not static. States with the largest decade-over-decade percentile jumps include Hawaii (+70 points, 2020 — Maui wildfires), Vermont (+43, 2010 — Hurricane Irene flooding), and New Jersey (+43, 2010 — Irene + Sandy). Traditional high-risk states (Florida, Texas) remain at the top, but the distribution is shifting.

**Economic impact:** Hurricanes are the most destructive per occurrence at $25.5M average damage per event. Wind events occur 60× more frequently but cause only $70K average damage per event. Total damage and per-event damage tell completely different stories about which event types to prioritize.

---

## Analytical Limitations

- **Damage figures are CPI-adjusted but not exposure-normalized.** Growth in absolute damage reflects both storm intensity changes and population/property growth in storm-prone areas. Findings should be framed as cost trends, not intensity trends.
- **Reporting completeness is a gradient.** Event counts have grown partly because of better reporting infrastructure (Doppler radar, digital reporting), not only because storms are more frequent. Pre-1996 data uses a different schema and is excluded from trend analysis.
- **2026 data is partial.** NOAA updates the current year continuously. Damage figures for 2026 are excluded from analysis.

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
