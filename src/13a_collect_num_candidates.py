"""
Stage 13a — Exploration extract from DERA num.txt for the feature-construction design.

num.txt is not kept on disk by Stage 11 (only sub.txt was parsed, in memory), so each
quarterly ZIP is re-downloaded here, streamed in memory, and reduced to the rows we
might need. Nothing raw is written to disk.

Kept rows: adsh in the current panel's accession set, standard taxonomy tags only
(version != adsh, i.e. not filer-custom), no co-registrant rows (coreg empty), NO
dimensional rows (segments empty - ~42% of num.txt rows carry a dimension such as
guarantor/segment/consolidating-entity members, and without this filter 35% of
filings have several conflicting values for the same tag, e.g. six different `Assets`
values for Ruby Tuesday FY2016), USD,
value dated at the filing's own period end (sub.period) with qtrs 0 (instant, balance
sheet) or 4 (annual duration, income/cash-flow), and a tag name matching one of the
core-concept patterns. This is a candidate pool for tag-frequency/coverage analysis
(13b), not the final feature extract.

Output: data/interim/stage_13a_num_candidates.csv
        adsh, tag, version, ddate, qtrs, value
Checkpointed per quarter (a quarter is done once its marker row is written).
"""

import io
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_PATH = REPO_ROOT / "data" / "processed" / "firm_year_panel.csv"
RAW_QUARTERS_PATH = REPO_ROOT / "data" / "interim" / "stage_11_raw_quarters.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_13a_num_candidates.csv"
DONE_PATH = REPO_ROOT / "data" / "interim" / "stage_13a_quarters_done.txt"

FIRST_YEAR, LAST_YEAR = 2012, 2025
USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.3

TAG_PATTERN = re.compile(
    r"(?:Assets|Cash|Liabilit|Debt|Borrowing|Notes|Revenue|Sales|NetIncomeLoss|ProfitLoss|"
    r"OperatingActivities|PropertyPlant|ResearchAndDevelopment|StockholdersEquity|CapitalLease|FinanceLease|"
    r"LineOfCredit|LinesOfCredit|Loan|Mortgage|Debenture|Bond|Financing(?:Obligation|Lease))",
    re.IGNORECASE,
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def process_quarter(year: int, quarter: int, wanted: set[str]) -> pd.DataFrame | None:
    url = f"https://www.sec.gov/files/dera/data/financial-statement-data-sets/{year}q{quarter}.zip"
    try:
        resp = get_with_hard_timeout(SESSION, url, hard_timeout_seconds=300, timeout=(10, 120))
        resp.raise_for_status()
    except (requests.RequestException, HardTimeoutError) as e:
        print(f"  [warn] {year}q{quarter}: {e}", flush=True)
        return None
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        with zf.open("sub.txt") as f:
            sub = pd.read_csv(f, sep="\t", dtype=str, usecols=["adsh", "period"])
        period = sub[sub["adsh"].isin(wanted)].set_index("adsh")["period"]
        kept = []
        with zf.open("num.txt") as f:
            for chunk in pd.read_csv(f, sep="\t", dtype=str, chunksize=1_000_000,
                                     usecols=["adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value"]):
                chunk = chunk[chunk["adsh"].isin(period.index) & chunk["coreg"].isna() & chunk["segments"].isna() & (chunk["uom"] == "USD")
                              & chunk["qtrs"].isin(["0", "4"]) & (chunk["version"] != chunk["adsh"])]
                if chunk.empty:
                    continue
                chunk = chunk[chunk["ddate"] == chunk["adsh"].map(period)]
                chunk = chunk[chunk["tag"].str.contains(TAG_PATTERN, regex=True)]
                kept.append(chunk[["adsh", "tag", "version", "ddate", "qtrs", "value"]])
    return pd.concat(kept) if kept else pd.DataFrame(columns=["adsh", "tag", "version", "ddate", "qtrs", "value"])


def main():
    panel = pd.read_csv(PANEL_PATH, dtype={"cik": str}, usecols=["cik", "accession"])
    raw = pd.read_csv(RAW_QUARTERS_PATH, dtype=str, usecols=["accession", "_year", "_quarter"]).dropna(subset=["accession"])
    quarter_of = raw.drop_duplicates("accession").set_index("accession")[["_year", "_quarter"]]
    panel = panel.join(quarter_of, on="accession")
    done = set(DONE_PATH.read_text().split()) if DONE_PATH.exists() else set()

    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        for quarter in range(1, 5):
            key = f"{year}q{quarter}"
            if key in done:
                continue
            wanted = set(panel.loc[(panel["_year"] == str(year)) & (panel["_quarter"] == str(quarter)), "accession"])
            if not wanted:
                DONE_PATH.write_text("\n".join(sorted(done | {key})))
                done.add(key)
                continue
            df = process_quarter(year, quarter, wanted)
            time.sleep(REQUEST_DELAY_SECONDS)
            if df is None:
                continue
            df.to_csv(OUT_PATH, mode="a", header=not OUT_PATH.exists(), index=False)
            done.add(key)
            DONE_PATH.write_text("\n".join(sorted(done)))
            print(f"  {key}: {len(wanted)} panel filings, {len(df)} candidate rows", flush=True)


if __name__ == "__main__":
    main()
