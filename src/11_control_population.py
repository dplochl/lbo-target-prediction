"""
Stage 11 — Control-population (risk-set) construction from SEC DERA's
Financial Statement Data Sets, per design_memo.md Section 2.

Why DERA instead of re-parsing our own full-index files: DERA's sub.txt
already carries filer category (afs - Large Accelerated / Accelerated /
Non-accelerated / Smaller Reporting Company, a public-float proxy for
"is this a real exchange-listed company, not an OTC/Pink-Sheet shell")
and SIC code per filing, in one place - avoiding a separate per-CIK
lookup the way Stage 2c's Form 25 check needed. It does NOT replace the
full-index scanning used for event detection (Stages 1/2a): DERA only
covers XBRL-tagged financial-statement filings, with no visibility into
SC 13E-3/DEFM14A/PREM14A/etc., which are narrative/legal filings.

Disk safety: only sub.txt is extracted from each quarterly ZIP (not the
~320MB num.txt, which isn't needed for population construction - that's
for a later feature-extraction stage). The ZIP and extracted sub.txt are
both deleted immediately after that quarter is parsed into memory, so at
most one quarter's raw files (~80MB) ever sit on disk at once - the same
lesson learned the hard way earlier in this project (a Stage 2b run once
filled the disk by keeping every downloaded document around).

Population definition: a CIK counts as being in the risk-set population
for fiscal year FY if it filed a 10-K (or 10-K/A) covering that fiscal
year. Using 10-K specifically (not 10-Q) keeps the panel firm-YEAR, and
using "files a 10-K, never 20-F" as the domestic-issuer signal is
already the convention documented in docs/data_dictionary.md.

Exit-reason tracking (design_memo.md Section 2's "at risk until..."):
for each CIK whose last observed fiscal year in this population is
before 2025 (i.e., it appears to have stopped filing), cross-reference
combined_events.csv first - if it's a genuine going-private event
(completion_status=='completed'), label the exit accordingly; otherwise
log "stopped filing, reason unknown" rather than assuming continued
public listing or silently dropping the firm.

Panel start year (scope decision, design_memo.md Section 6 / v1.5): the
annual-filers output starts at FY2012, not FY2010. DERA sub.txt only contains
XBRL filers, and XBRL phase-in reached smaller filers only for periods after
mid-2011 - full-index 10-K filers are covered ~1-19% in 2010-2011 quarters vs.
~80-90% from 2012 on. The core financial features (XBRL Company Facts / DERA
num.txt) are equally unavailable pre-XBRL, so rebuilding the 2010-2011 risk set
from the full index would recover firm identities but no usable feature rows.
stage_11_raw_quarters.csv still holds the unfiltered 2010-2025 extraction.

Right-censoring: the raw extraction covers 10-Ks *filed* through 2025 Q4, so a
firm whose last observed fiscal year is 2024 is indistinguishable from one still
filing (its FY2025 10-K is filed in 2026, outside the data). Exit reasons are
therefore only assigned for last_fiscal_year_observed <= LAST_COMPLETE_FY - 1.

Output: data/interim/stage_11_annual_filers.csv
    cik, company_name, sic, afs, fiscal_year, form, filed_date, accession
Output: data/interim/stage_11_exit_reasons.csv
    cik, last_fiscal_year_observed, exit_reason, event_completion_status,
    event_year, event_year_minus_last_fy
"""

import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
COMBINED_EVENTS_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"
RAW_DERA_DIR = REPO_ROOT / "data" / "raw" / "dera_quarterly"
ANNUAL_OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_11_annual_filers.csv"
EXIT_OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_11_exit_reasons.csv"

START_YEAR = 2010  # raw DERA download range (kept for the unfiltered checkpoint)
END_YEAR = 2025
PANEL_START_FY = 2012  # analysis panel start - see module docstring
LAST_COMPLETE_FY = 2024  # last fiscal year whose 10-Ks are (almost) all filed inside the data
FORM_TYPES = {"10-K", "10-K/A"}

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.3

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

FIELDNAMES = ["cik", "company_name", "sic", "afs", "fiscal_year", "form", "filed_date", "accession"]


def download_and_extract_sub_txt(year: int, quarter: int) -> pd.DataFrame | None:
    """Download one quarter's ZIP, pull out only sub.txt, delete everything
    on disk immediately - never accumulate raw files across quarters."""
    RAW_DERA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = RAW_DERA_DIR / f"{year}q{quarter}.zip"
    url = f"https://www.sec.gov/files/dera/data/financial-statement-data-sets/{year}q{quarter}.zip"

    try:
        resp = get_with_hard_timeout(SESSION, url, hard_timeout_seconds=120, timeout=(10, 60))
        resp.raise_for_status()
    except (requests.RequestException, HardTimeoutError) as e:
        print(f"  [warn] failed to fetch {year}q{quarter}: {e}", flush=True)
        return None
    time.sleep(REQUEST_DELAY_SECONDS)

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            with zf.open("sub.txt") as f:
                df = pd.read_csv(f, sep="\t", dtype=str, low_memory=False)
    except (zipfile.BadZipFile, KeyError) as e:
        print(f"  [warn] bad zip or missing sub.txt for {year}q{quarter}: {e}", flush=True)
        return None
    # Nothing written to disk at all (parsed straight from the in-memory
    # response), so there's nothing to clean up here - the zip_path
    # variable above is unused deliberately, kept only for clarity of intent.
    return df


RAW_QUARTERS_PATH = REPO_ROOT / "data" / "interim" / "stage_11_raw_quarters.csv"


def load_already_processed_quarters() -> set[tuple[int, int]]:
    if not RAW_QUARTERS_PATH.exists():
        return set()
    done = pd.read_csv(RAW_QUARTERS_PATH, dtype=str, usecols=["_year", "_quarter"]).drop_duplicates()
    return {(int(y), int(q)) for y, q in zip(done["_year"], done["_quarter"])}


def build_annual_filers() -> pd.DataFrame:
    import csv

    already_done = load_already_processed_quarters()
    if already_done:
        print(f"Resuming: {len(already_done)} quarters already checkpointed.", flush=True)

    write_header = not RAW_QUARTERS_PATH.exists()
    raw_fieldnames = FIELDNAMES + ["_year", "_quarter"]

    with open(RAW_QUARTERS_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=raw_fieldnames)
        if write_header:
            writer.writeheader()
            f.flush()

        for year in range(START_YEAR, END_YEAR + 1):
            for quarter in range(1, 5):
                if (year, quarter) in already_done:
                    continue

                df = download_and_extract_sub_txt(year, quarter)
                if df is not None:
                    filtered = df[df["form"].isin(FORM_TYPES)].copy()
                    if not filtered.empty:
                        filtered["fiscal_year"] = pd.to_numeric(filtered["fy"], errors="coerce")
                        filtered["cik"] = filtered["cik"].astype(str).str.zfill(10)
                        subset = filtered[["cik", "name", "sic", "afs", "fiscal_year", "form", "filed", "adsh"]]
                        subset = subset.rename(columns={"name": "company_name", "filed": "filed_date", "adsh": "accession"})
                        subset["_year"], subset["_quarter"] = year, quarter
                        for row in subset.to_dict("records"):
                            writer.writerow(row)
                    print(f"  {year}Q{quarter}: {0 if df is None else len(filtered)} 10-K/10-K-A rows", flush=True)
                # Always mark the quarter as processed, even with 0 matching
                # rows or a fetch failure, so a resume doesn't retry forever
                # on a quarter that's simply empty or persistently failing.
                writer.writerow({"_year": year, "_quarter": quarter, **{k: None for k in FIELDNAMES}})
                f.flush()

    raw = pd.read_csv(RAW_QUARTERS_PATH, dtype={"cik": str})
    raw = raw.dropna(subset=["fiscal_year"])
    raw["fiscal_year"] = raw["fiscal_year"].astype(int)

    # Amendments (10-K/A) can restate an original 10-K for the same
    # CIK+fiscal_year - keep the most recently filed row per (cik, fiscal_year)
    # so amendments correctly supersede originals rather than duplicating rows.
    raw = raw.sort_values("filed_date")
    raw = raw.drop_duplicates(subset=["cik", "fiscal_year"], keep="last")

    raw = raw[raw["fiscal_year"] >= PANEL_START_FY]

    return raw[FIELDNAMES].sort_values(["cik", "fiscal_year"])


def compute_exit_reasons(annual_filers: pd.DataFrame) -> pd.DataFrame:
    events = pd.read_csv(COMBINED_EVENTS_PATH, dtype={"cik": str})
    events_by_cik = events.set_index("cik")

    last_year = annual_filers.groupby("cik")["fiscal_year"].max()
    # Last FY observed == LAST_COMPLETE_FY is right-censored (see docstring),
    # not an exit.
    exited = last_year[last_year < LAST_COMPLETE_FY]
    event_year_by_cik = pd.to_datetime(events.set_index("cik")["first_event_date"], errors="coerce").dt.year

    rows = []
    for cik, exit_year in exited.items():
        if cik in events_by_cik.index:
            ev = events_by_cik.loc[cik]
            if isinstance(ev, pd.DataFrame):  # defensive: duplicate CIK rows shouldn't exist, but don't crash if so
                ev = ev.iloc[0]
            completion = ev.get("completion_status")
            if completion == "completed":
                reason = "going_private_event"
            elif pd.notna(completion):
                reason = f"going_private_event_candidate_{completion}"
            else:
                reason = "stopped filing, reason unknown"
        else:
            reason = "stopped filing, reason unknown"

        rows.append({
            "cik": cik,
            "last_fiscal_year_observed": exit_year,
            "exit_reason": reason,
            "event_completion_status": events_by_cik.loc[cik, "completion_status"] if cik in events_by_cik.index else None,
            "event_year": event_year_by_cik.get(cik),
        })

    out = pd.DataFrame(rows)
    out["event_year_minus_last_fy"] = out["event_year"] - out["last_fiscal_year_observed"]
    return out


def main():
    print(f"Building annual 10-K filer population for {START_YEAR}-{END_YEAR}...")
    annual_filers = build_annual_filers()
    ANNUAL_OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    annual_filers.to_csv(ANNUAL_OUT_PATH, index=False)
    print(f"Wrote {ANNUAL_OUT_PATH}: {len(annual_filers)} rows, {annual_filers['cik'].nunique()} unique CIKs")

    print("Computing exit reasons for CIKs whose last observed FY is before the right-censoring cutoff...")
    exits = compute_exit_reasons(annual_filers)
    exits.to_csv(EXIT_OUT_PATH, index=False)
    print(f"Wrote {EXIT_OUT_PATH}: {len(exits)} exited CIKs")
    print(exits["exit_reason"].value_counts().to_string())


if __name__ == "__main__":
    main()
