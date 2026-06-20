"""
NOAA Storm Events ingestion script.

Downloads StormEvents_details CSVs from NOAA FTP and uploads to BigQuery raw layer.
Raw layer contract: no transformations — ingest exactly what NOAA provides.
Parsing (damage strings, type casting) happens in dbt staging.

Usage:
    python ingest_noaa.py --project damagereport-499916 --keyfile path/to/key.json
    python ingest_noaa.py --project damagereport-499916 --keyfile path/to/key.json --year 2023
"""

import argparse
import ftplib
import gzip
import io
import re
import sys
from pathlib import Path

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

# --- Constants -----------------------------------------------------------

NOAA_FTP_HOST = "ftp.ncdc.noaa.gov"
NOAA_FTP_DIR = "/pub/data/swdi/stormevents/csvfiles/"

# 1996 is the first year with standardized 48-category event types.
# Pre-1996 data uses a different schema and is structurally incomparable.
START_YEAR = 1996

RAW_DATASET = "raw_noaa"
RAW_TABLE = "storm_events"

# Explicit schema — never use autodetect on this table.
# PROPERTY_DAMAGE and CROP_DAMAGE are intentionally STRING:
# NOAA stores them as "10.00K", "2.5M" etc. Parsing happens in dbt staging.
# Autodetect would silently cast these to FLOAT and lose the original values.
BQ_SCHEMA = [
    bigquery.SchemaField("BEGIN_YEARMONTH",     "STRING"),
    bigquery.SchemaField("BEGIN_DAY",           "STRING"),
    bigquery.SchemaField("BEGIN_TIME",          "STRING"),
    bigquery.SchemaField("END_YEARMONTH",       "STRING"),
    bigquery.SchemaField("END_DAY",             "STRING"),
    bigquery.SchemaField("END_TIME",            "STRING"),
    bigquery.SchemaField("EPISODE_ID",          "STRING"),
    bigquery.SchemaField("EVENT_ID",            "STRING"),
    bigquery.SchemaField("STATE",               "STRING"),
    bigquery.SchemaField("STATE_FIPS",          "STRING"),
    bigquery.SchemaField("YEAR",                "INTEGER"),
    bigquery.SchemaField("MONTH_NAME",          "STRING"),
    bigquery.SchemaField("EVENT_TYPE",          "STRING"),
    bigquery.SchemaField("CZ_TYPE",             "STRING"),
    bigquery.SchemaField("CZ_FIPS",             "STRING"),
    bigquery.SchemaField("CZ_NAME",             "STRING"),
    bigquery.SchemaField("WFO",                 "STRING"),
    bigquery.SchemaField("BEGIN_DATE_TIME",     "STRING"),
    bigquery.SchemaField("CZ_TIMEZONE",         "STRING"),
    bigquery.SchemaField("END_DATE_TIME",       "STRING"),
    bigquery.SchemaField("INJURIES_DIRECT",     "INTEGER"),
    bigquery.SchemaField("INJURIES_INDIRECT",   "INTEGER"),
    bigquery.SchemaField("DEATHS_DIRECT",       "INTEGER"),
    bigquery.SchemaField("DEATHS_INDIRECT",     "INTEGER"),
    bigquery.SchemaField("DAMAGE_PROPERTY",     "STRING"),
    bigquery.SchemaField("DAMAGE_CROPS",        "STRING"),
    bigquery.SchemaField("SOURCE",              "STRING"),
    bigquery.SchemaField("MAGNITUDE",           "STRING"),
    bigquery.SchemaField("MAGNITUDE_TYPE",      "STRING"),
    bigquery.SchemaField("FLOOD_CAUSE",         "STRING"),
    bigquery.SchemaField("CATEGORY",            "STRING"),
    bigquery.SchemaField("TOR_F_SCALE",         "STRING"),
    bigquery.SchemaField("TOR_LENGTH",          "STRING"),
    bigquery.SchemaField("TOR_WIDTH",           "STRING"),
    bigquery.SchemaField("TOR_OTHER_WFO",       "STRING"),
    bigquery.SchemaField("TOR_OTHER_CZ_STATE",  "STRING"),
    bigquery.SchemaField("TOR_OTHER_CZ_FIPS",   "STRING"),
    bigquery.SchemaField("TOR_OTHER_CZ_NAME",   "STRING"),
    bigquery.SchemaField("BEGIN_RANGE",         "STRING"),
    bigquery.SchemaField("BEGIN_AZIMUTH",       "STRING"),
    bigquery.SchemaField("BEGIN_LOCATION",      "STRING"),
    bigquery.SchemaField("END_RANGE",           "STRING"),
    bigquery.SchemaField("END_AZIMUTH",         "STRING"),
    bigquery.SchemaField("END_LOCATION",        "STRING"),
    bigquery.SchemaField("BEGIN_LAT",           "STRING"),
    bigquery.SchemaField("BEGIN_LON",           "STRING"),
    bigquery.SchemaField("END_LAT",             "STRING"),
    bigquery.SchemaField("END_LON",             "STRING"),
    bigquery.SchemaField("EPISODE_NARRATIVE",   "STRING"),
    bigquery.SchemaField("EVENT_NARRATIVE",     "STRING"),
    bigquery.SchemaField("DATA_SOURCE",         "STRING"),
]


# --- BigQuery helpers -----------------------------------------------------

def get_bq_client(project: str, keyfile: str) -> bigquery.Client:
    credentials = service_account.Credentials.from_service_account_file(
        keyfile,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return bigquery.Client(project=project, credentials=credentials)


def ensure_dataset_and_table(client: bigquery.Client, project: str) -> str:
    """Create raw_noaa dataset and storm_events table if they don't exist."""
    dataset_id = f"{project}.{RAW_DATASET}"
    table_id = f"{dataset_id}.{RAW_TABLE}"

    # Create dataset if missing
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = "US"
    client.create_dataset(dataset, exists_ok=True)
    print(f"Dataset ready: {dataset_id}")

    # Create table with explicit schema + year partitioning if missing.
    # Partitioning by YEAR means BigQuery only scans the partitions
    # touched by a query's WHERE clause — critical for cost on 1.8M rows.
    table = bigquery.Table(table_id, schema=BQ_SCHEMA)
    table.range_partitioning = bigquery.RangePartitioning(
        field="YEAR",
        range_=bigquery.PartitionRange(start=1996, end=2030, interval=1),
    )
    # Cluster within each year partition by STATE first (most mart models
    # filter by state), then EVENT_TYPE (secondary filter axis).
    table.clustering_fields = ["STATE", "EVENT_TYPE"]
    client.create_table(table, exists_ok=True)
    print(f"Table ready: {table_id}")

    return table_id


def get_existing_years(client: bigquery.Client, project: str) -> set[int]:
    """Return the set of years already loaded in BigQuery."""
    table_id = f"{project}.{RAW_DATASET}.{RAW_TABLE}"
    query = f"SELECT DISTINCT YEAR FROM `{table_id}`"
    try:
        result = client.query(query).result()
        return {row.YEAR for row in result}
    except Exception:
        # Table is empty or doesn't exist yet
        return set()


# --- NOAA FTP helpers -----------------------------------------------------

def list_noaa_files(ftp: ftplib.FTP) -> list[str]:
    """List all StormEvents details files on NOAA FTP."""
    files = ftp.nlst()
    # Only details files — NOAA also has fatalities and locations files
    return [f for f in files if "StormEvents_details" in f]


def year_from_filename(filename: str) -> int | None:
    """Extract year from NOAA filename like StormEvents_details-ftp_v1.0_d2023_c20240116.csv.gz"""
    match = re.search(r"_d(\d{4})_", filename)
    return int(match.group(1)) if match else None


def download_and_parse(ftp: ftplib.FTP, filename: str) -> pd.DataFrame:
    """Download a gzipped CSV from NOAA FTP and return as DataFrame."""
    buffer = io.BytesIO()
    ftp.retrbinary(f"RETR {filename}", buffer.write)
    buffer.seek(0)

    with gzip.open(buffer, "rt", encoding="latin-1") as f:
        df = pd.read_csv(f, dtype=str, low_memory=False)

    # Coerce integer columns — keep everything else as string per raw contract
    for col in ["YEAR", "INJURIES_DIRECT", "INJURIES_INDIRECT",
                "DEATHS_DIRECT", "DEATHS_INDIRECT"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")

    # Keep only columns in our schema — NOAA occasionally adds extra columns
    schema_cols = [field.name for field in BQ_SCHEMA]
    df = df[[c for c in schema_cols if c in df.columns]]

    return df


# --- Upload ---------------------------------------------------------------

def upload_to_bigquery(
    client: bigquery.Client,
    df: pd.DataFrame,
    table_id: str,
    year: int,
) -> None:
    """Append a year's DataFrame to the BigQuery raw table."""
    job_config = bigquery.LoadJobConfig(
        schema=BQ_SCHEMA,
        # WRITE_APPEND: add rows to existing table.
        # We never overwrite — raw layer is append-only.
        write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
    )
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()  # wait for completion
    print(f"  Uploaded {len(df):,} rows for {year}")


# --- Main -----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ingest NOAA Storm Events to BigQuery")
    parser.add_argument("--project",  required=True, help="GCP project ID")
    parser.add_argument("--keyfile",  required=True, help="Path to service account JSON")
    parser.add_argument("--year",     type=int,      help="Ingest a single year only (for testing)")
    args = parser.parse_args()

    import datetime
    current_year = datetime.date.today().year

    client = get_bq_client(args.project, args.keyfile)
    table_id = ensure_dataset_and_table(client, args.project)

    existing_years = get_existing_years(client, args.project)
    print(f"Years already in BigQuery: {sorted(existing_years) or 'none'}")

    print(f"Connecting to NOAA FTP: {NOAA_FTP_HOST}")
    ftp = ftplib.FTP(NOAA_FTP_HOST)
    ftp.login()  # anonymous login — no credentials needed
    ftp.cwd(NOAA_FTP_DIR)

    all_files = list_noaa_files(ftp)

    # Determine which years to download:
    # - Single year mode (--year flag): just that year, for testing
    # - Normal mode: missing years + current year (current year updates mid-year)
    if args.year:
        years_to_fetch = [args.year]
    else:
        all_years = {year_from_filename(f) for f in all_files if year_from_filename(f)}
        eligible_years = {y for y in all_years if y >= START_YEAR}
        missing_years = eligible_years - existing_years
        years_to_fetch = sorted(missing_years | {current_year})

    print(f"Years to download: {years_to_fetch}")

    for year in years_to_fetch:
        # Find the file for this year — NOAA filenames include a creation date
        # suffix so we can't hardcode the full name, just match by year
        matches = [f for f in all_files if f"_d{year}_" in f and "details" in f]
        if not matches:
            print(f"  No file found for {year}, skipping")
            continue

        # If multiple versions exist for a year, take the most recent (last alphabetically)
        filename = sorted(matches)[-1]
        print(f"Downloading {filename}...")

        # Delete existing rows for this year before re-uploading.
        # Current year re-downloads because NOAA updates it mid-year.
        if year in existing_years:
            delete_query = f"""
                DELETE FROM `{table_id}`
                WHERE YEAR = {year}
            """
            client.query(delete_query).result()
            print(f"  Cleared existing rows for {year}")

        df = download_and_parse(ftp, filename)
        upload_to_bigquery(client, df, table_id, year)

    ftp.quit()
    print("\nIngestion complete.")


if __name__ == "__main__":
    main()
