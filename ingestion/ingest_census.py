"""
Ingest Census 2020 county population density to BigQuery.

Sources:
  ingestion/2020_Gaz_counties_national.zip  — land area per county (Census Gazetteer)
  ingestion/CC-EST2020-ALLDATA.csv          — population by county/age/year (Census estimates)

Output: raw_noaa.county_population (WRITE_TRUNCATE)
"""

import argparse
import zipfile
import pathlib

import pandas as pd
from google.cloud import bigquery
from google.oauth2 import service_account

ROOT = pathlib.Path(__file__).parent.parent
GAZ_ZIP  = ROOT / "ingestion" / "2020_Gaz_counties_national.zip"
POP_CSV  = ROOT / "ingestion" / "CC-EST2020-ALLDATA.csv"
TABLE_ID = "raw_noaa.county_population"

SCHEMA = [
    bigquery.SchemaField("state_abbrev",        "STRING",  mode="REQUIRED"),
    bigquery.SchemaField("county_name",          "STRING",  mode="REQUIRED"),
    bigquery.SchemaField("state_name",           "STRING",  mode="NULLABLE"),
    bigquery.SchemaField("population_2020",      "INTEGER", mode="NULLABLE"),
    bigquery.SchemaField("land_area_sqmi",       "FLOAT",   mode="NULLABLE"),
    bigquery.SchemaField("population_density",   "FLOAT",   mode="NULLABLE"),
]


def load_gazetteer(path: pathlib.Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        with z.open(z.namelist()[0]) as f:
            gaz = pd.read_csv(f, sep="\t", dtype=str)

    gaz.columns = gaz.columns.str.strip()
    gaz = gaz[["USPS", "NAME", "ALAND_SQMI"]].copy()
    gaz.columns = ["state_abbrev", "county_raw", "land_area_sqmi"]
    gaz["state_abbrev"] = gaz["state_abbrev"].str.strip()
    gaz["land_area_sqmi"] = pd.to_numeric(gaz["land_area_sqmi"], errors="coerce")

    # Normalize: strip " County", " Parish", " Borough", " Census Area", " Municipality"
    # Keep true counties only — drop independent cities that share a county name
    # (e.g. "Baltimore city" vs "Baltimore County" in MD)
    gaz = gaz[~gaz["county_raw"].str.endswith(" city")].copy()

    gaz["county_name"] = (
        gaz["county_raw"]
        .str.replace(
            r"\s+(County|Parish|Borough|Census Area|Municipality)$",
            "", regex=True
        )
        .str.strip()
        .str.lower()
    )
    return gaz[["state_abbrev", "county_name", "land_area_sqmi"]]


def load_population(path: pathlib.Path) -> pd.DataFrame:
    pop = pd.read_csv(path, encoding="latin-1", low_memory=False)
    # SUMLEV=50 county rows, AGEGRP=0 all-ages total, YEAR=13 = 2020 estimate
    pop = pop[(pop["SUMLEV"] == 50) & (pop["AGEGRP"] == 0) & (pop["YEAR"] == 13)].copy()
    pop = pop[["STNAME", "CTYNAME", "TOT_POP"]].copy()
    pop.columns = ["state_name", "county_raw", "population_2020"]

    pop["county_name"] = (
        pop["county_raw"]
        .str.replace(
            r"\s+(County|Parish|Borough|Census Area|Municipality|city)$",
            "", regex=True
        )
        .str.strip()
        .str.lower()
    )

    # State name → abbreviation lookup
    state_abbrev = {
        "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR",
        "California": "CA", "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE",
        "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID",
        "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
        "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD",
        "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS",
        "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
        "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
        "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
        "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
        "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT",
        "Vermont": "VT", "Virginia": "VA", "Washington": "WA", "West Virginia": "WV",
        "Wisconsin": "WI", "Wyoming": "WY",
    }
    pop["state_abbrev"] = pop["state_name"].map(state_abbrev)
    pop["population_2020"] = pd.to_numeric(pop["population_2020"], errors="coerce").astype("Int64")
    return pop[["state_abbrev", "state_name", "county_name", "population_2020"]]


def build_density(gaz: pd.DataFrame, pop: pd.DataFrame) -> pd.DataFrame:
    merged = pop.merge(gaz, on=["state_abbrev", "county_name"], how="inner")
    merged["population_density"] = merged["population_2020"] / merged["land_area_sqmi"]
    merged["population_density"] = merged["population_density"].round(4)

    n_pop = len(pop)
    n_merged = len(merged)
    print(f"Population rows: {n_pop}")
    print(f"Matched after join: {n_merged} ({n_merged/n_pop:.1%} match rate)")
    if n_merged < n_pop * 0.9:
        print("WARNING: match rate below 90% — check county name normalization")

    return merged[["state_abbrev", "county_name", "state_name",
                   "population_2020", "land_area_sqmi", "population_density"]]


def upload(df: pd.DataFrame, project: str, client: bigquery.Client) -> None:
    full_table = f"{project}.{TABLE_ID}"
    job_config = bigquery.LoadJobConfig(
        schema=SCHEMA,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    job = client.load_table_from_dataframe(df, full_table, job_config=job_config)
    job.result()
    tbl = client.get_table(full_table)
    print(f"Loaded {tbl.num_rows} rows → {full_table}")


def main():
    parser = argparse.ArgumentParser(description="Ingest Census population density to BigQuery")
    parser.add_argument("--project",  required=True, help="GCP project ID")
    parser.add_argument("--keyfile",  required=True, help="Path to service account JSON")
    args = parser.parse_args()

    creds = service_account.Credentials.from_service_account_file(
        args.keyfile,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    client = bigquery.Client(project=args.project, credentials=creds)

    print("Loading Gazetteer (land area)...")
    gaz = load_gazetteer(GAZ_ZIP)
    print(f"  {len(gaz)} county rows")

    print("Loading Census population estimates...")
    pop = load_population(POP_CSV)
    print(f"  {len(pop)} county rows")

    print("Joining and computing density...")
    df = build_density(gaz, pop)

    print(df[["state_abbrev", "county_name", "population_2020",
              "land_area_sqmi", "population_density"]].head(5).to_string())

    print(f"\nUploading to BigQuery: {args.project}.{TABLE_ID}")
    upload(df, args.project, client)


if __name__ == "__main__":
    main()
