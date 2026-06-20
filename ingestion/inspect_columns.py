"""Quick script to print column names from a NOAA CSV without downloading the whole file."""
import ftplib, gzip, io, pandas as pd

ftp = ftplib.FTP("ftp.ncdc.noaa.gov")
ftp.login()
ftp.cwd("/pub/data/swdi/stormevents/csvfiles/")

filename = "StormEvents_details-ftp_v1.0_d2023_c20260323.csv.gz"
buffer = io.BytesIO()
ftp.retrbinary(f"RETR {filename}", buffer.write)
buffer.seek(0)

with gzip.open(buffer, "rt", encoding="latin-1") as f:
    df = pd.read_csv(f, dtype=str, nrows=2)

print("Columns in NOAA 2023 file:")
for col in df.columns:
    print(f"  {repr(col)}")

ftp.quit()
