"""
Stage 11c — Exact fiscal period-end date per 10-K filing, from DERA sub.txt.

Stage 11 kept only cik/fiscal_year/filed_date. The pre-event definition and the label
horizon need the balance-sheet date (`period`) and the fiscal-year-end month/day (`fye`),
so sub.txt is re-read (in memory, nothing left on disk) for the panel's accessions.

Output: data/interim/stage_11c_period_end.csv
        accession, period, fye, fy, fp, filed, accepted
"""

import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_PATH = REPO_ROOT / "data" / "processed" / "firm_year_panel.csv"
ANNUAL_PATH = REPO_ROOT / "data" / "interim" / "stage_11_annual_filers.csv"
RAW_QUARTERS_PATH = REPO_ROOT / "data" / "interim" / "stage_11_raw_quarters.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_11c_period_end.csv"
DONE_PATH = REPO_ROOT / "data" / "interim" / "stage_11c_quarters_done.txt"

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def main():
    # all FY2012+ annual filers (the panel is a subset), so later rule changes need no re-download
    annual = pd.read_csv(ANNUAL_PATH, dtype={"cik": str}, usecols=["accession"])
    raw = pd.read_csv(RAW_QUARTERS_PATH, dtype=str, usecols=["accession", "_year", "_quarter"]).dropna(subset=["accession"])
    annual = annual.join(raw.drop_duplicates("accession").set_index("accession"), on="accession")
    done = set(DONE_PATH.read_text().split()) if DONE_PATH.exists() else set()
    for year in range(2012, 2026):
        for quarter in range(1, 5):
            key = f"{year}q{quarter}"
            wanted = set(annual.loc[(annual["_year"] == str(year)) & (annual["_quarter"] == str(quarter)), "accession"])
            if key in done or not wanted:
                continue
            try:
                resp = get_with_hard_timeout(SESSION, f"https://www.sec.gov/files/dera/data/financial-statement-data-sets/{key}.zip",
                                             hard_timeout_seconds=300, timeout=(10, 120))
                resp.raise_for_status()
            except (requests.RequestException, HardTimeoutError) as e:
                print(f"  [warn] {key}: {e}", flush=True)
                continue
            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf, zf.open("sub.txt") as f:
                sub = pd.read_csv(f, sep="\t", dtype=str, usecols=["adsh", "period", "fye", "fy", "fp", "filed", "accepted"])
            sub = sub[sub["adsh"].isin(wanted)].rename(columns={"adsh": "accession"})
            sub.to_csv(OUT_PATH, mode="a", header=not OUT_PATH.exists(), index=False)
            done.add(key)
            DONE_PATH.write_text("\n".join(sorted(done)))
            print(f"  {key}: {len(sub)} filings", flush=True)
            time.sleep(0.3)


if __name__ == "__main__":
    main()
