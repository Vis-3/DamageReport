"""
BLS CPI ingestion script.

Downloads the BLS All Items CPI series and uploads annual averages to BigQuery.
We collapse 12 monthly values to one annual average per year — storms happen
throughout the year so no single month is more representative than any other.

Usage:
    python ingest_cpi.py --project damagereport-499916 --keyfile path/to/key.json
"""

import argparse
import io

import pandas as pd
import requests
from google.cloud import bigquery
from google.oauth2 import service_account

# --- Constants -----------------------------------------------------------

BLS_CPI_URL = "https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems"

RAW_DATASET = "raw_noaa"
CPI_TABLE = "cpi_deflator"

# Base year for inflation adjustment — all damage figures will be expressed
# in 2024 dollars. Choice is audience interpretability: readers understand
# 2024 dollars intuitively. The math is valid for any base year.
BASE_YEAR = 2024

BQ_SCHEMA = [
    bigquery.SchemaField("year",          "INTEGER"),
    bigquery.SchemaField("cpi_annual_avg","FLOAT64"),
    bigquery.SchemaField("cpi_base_year", "INTEGER"),  # always 2024
    bigquery.SchemaField("deflator",      "FLOAT64"),  # cpi_2024 / cpi_year
]


# --- BigQuery helpers -----------------------------------------------------

def get_bq_client(project: str, keyfile: str) -> bigquery.Client:
    credentials = service_account.Credentials.from_service_account_file(
        keyfile,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return bigquery.Client(project=project, credentials=credentials)


def ensure_cpi_table(client: bigquery.Client, project: str) -> str:
    """Create cpi_deflator table if it doesn't exist."""
    table_id = f"{project}.{RAW_DATASET}.{CPI_TABLE}"
    table = bigquery.Table(table_id, schema=BQ_SCHEMA)
    client.create_table(table, exists_ok=True)
    print(f"Table ready: {table_id}")
    return table_id


# --- BLS download --------------------------------------------------------

def download_cpi(local_file: str | None = None) -> pd.DataFrame:
    """
    Load BLS CPI flat file and return annual averages.

    BLS flat file format:
        series_id    year    period    value    footnote_codes
        CUUR0000SA0  1947    M01       21.48
        ...
        CUUR0000SA0  1947    M13       21.48   <- M13 is the annual average BLS publishes

    We use M13 (BLS pre-computed annual average) rather than computing our own
    mean — it matches what BLS publishes officially and avoids rounding differences.

    BLS blocks automated HTTP downloads, so we read from a local file.
    Download manually from: https://download.bls.gov/pub/time.series/cu/cu.data.1.AllItems
    """
    if local_file:
        df = pd.read_csv(local_file, sep="\t", dtype=str)
    else:
        # Fallback: attempt HTTP download (may be blocked by BLS)
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(BLS_CPI_URL, headers=headers)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text), sep="\t", dtype=str)

    # Clean column names — BLS file has trailing whitespace
    df.columns = df.columns.str.strip()
    df = df.apply(lambda col: col.str.strip() if col.dtype == "object" else col)

    # Filter to: All Urban Consumers series + annual average period (M13)
    # series_id has trailing whitespace in the BLS file — use str.strip() in comparison
    df = df[
        (df["series_id"].str.strip() == "CUUR0000SA0") &
        (df["period"].str.strip() == "M13")
    ].copy()

    df["year"] = df["year"].astype(int)
    df["cpi_annual_avg"] = df["value"].astype(float)

    # Only keep years from 1996 onward — matches our storm events range
    df = df[df["year"] >= 1996].copy()

    return df[["year", "cpi_annual_avg"]].reset_index(drop=True)


def compute_deflator(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add deflator column: cpi_base_year / cpi_year.

    To convert a 2000 dollar amount to 2024 dollars:
        damage_2024 = damage_2000 * deflator_2000
        deflator_2000 = cpi_2024 / cpi_2000

    A deflator > 1 means that year's dollars were worth less than 2024 dollars.
    A deflator < 1 would mean that year's dollars were worth more (only possible
    for years after 2024, which won't exist in our data).
    """
    cpi_base = df.loc[df["year"] == BASE_YEAR, "cpi_annual_avg"].values[0]
    df["deflator"] = cpi_base / df["cpi_annual_avg"]
    df["cpi_base_year"] = BASE_YEAR
    return df


# --- Upload --------------------------------------------------------------

def upload_cpi(client: bigquery.Client, df: pd.DataFrame, table_id: str) -> None:
    """Replace CPI table entirely — it's a small reference table, full refresh is fine."""
    job_config = bigquery.LoadJobConfig(
        schema=BQ_SCHEMA,
        # WRITE_TRUNCATE: replace all rows on every run.
        # CPI table is tiny (< 100 rows) and BLS occasionally revises historical values,
        # so full refresh is safer and cheaper than incremental for this table.
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_dataframe(df, table_id, job_config=job_config)
    job.result()
    print(f"Uploaded {len(df)} years of CPI data (base year: {BASE_YEAR})")


# --- Main ----------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Ingest BLS CPI data to BigQuery")
    parser.add_argument("--project",    required=True, help="GCP project ID")
    parser.add_argument("--keyfile",    required=True, help="Path to service account JSON")
    parser.add_argument("--local-file", required=True, help="Path to locally downloaded BLS CPI file")
    args = parser.parse_args()

    client = get_bq_client(args.project, args.keyfile)
    table_id = ensure_cpi_table(client, args.project)

    print("Loading BLS CPI data from local file...")
    df = download_cpi(local_file=args.local_file)
    df = compute_deflator(df)

    print(f"Years: {df['year'].min()} - {df['year'].max()}")
    print(f"CPI {BASE_YEAR}: {df.loc[df['year'] == BASE_YEAR, 'cpi_annual_avg'].values[0]:.1f}")
    print(f"Sample deflators:\n{df[df['year'].isin([1996, 2000, 2010, 2020, BASE_YEAR])][['year','cpi_annual_avg','deflator']].to_string(index=False)}")

    upload_cpi(client, df, table_id)


if __name__ == "__main__":
    main()
