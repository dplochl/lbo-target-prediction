"""
Stage 11b — EntityPublicFloat per firm-year, for the listing filter in Stage 12.

Why not DERA num.txt: DERA's Financial Statement Data Sets omit most cover-page
(dei) facts - EntityPublicFloat is present for only ~3 of ~4,300 10-Ks in
2013 Q1. The XBRL frames API (data.sec.gov/api/xbrl/frames/dei/EntityPublicFloat/
USD/CY{yyyy}Q{q}I.json) returns, for one calendar instant, every filer's float
together with the accession number (`accn`) of the filing that reported it, so
each value is tied to the *same filing* as the firm-year (not a different year's
float). Calendar quarters 2011Q1-2025Q4 are fetched because the float is measured
at the end of the issuer's second fiscal quarter, which precedes the 10-K by
up to ~a year (June-FYE firms report a December float from the prior calendar year).

Frames caveat found while validating Stage 12: frames return the *latest* filing that
re-reports the same float instant, which is often a later 10-Q or 10-K/A rather than
the original 10-K (e.g. Delta Natural Gas, Datto FY2021), so the accession link
fails for ~6% of firm-years. For CIKs with any unlinked firm-year the EDGAR Company
Facts file (data.sec.gov/api/xbrl/companyfacts) is fetched and its dei
EntityPublicFloat facts (which carry the exact accn + form of every filing that
reported them) are added to the lookup.

Firm-year link: a firm-year's float is taken from its own accession (the 10-K or
10-K/A kept in stage_11_annual_filers.csv). If that accession has no float (e.g. a
partial 10-K/A without a cover page), any other 10-K/10-K-A accession for the same
CIK and fiscal year in stage_11_raw_quarters.csv is used, and `float_source`
records which.

Output: data/interim/stage_11b_float_frames.csv        (raw frames, checkpointed)
        data/interim/stage_11b_public_float.csv        (cik, fiscal_year, entity_public_float,
                                                        float_date, float_accn, float_source)
"""

import time
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
ANNUAL_PATH = REPO_ROOT / "data" / "interim" / "stage_11_annual_filers.csv"
RAW_QUARTERS_PATH = REPO_ROOT / "data" / "interim" / "stage_11_raw_quarters.csv"
FRAMES_PATH = REPO_ROOT / "data" / "interim" / "stage_11b_float_frames.csv"
COMPANYFACTS_PATH = REPO_ROOT / "data" / "interim" / "stage_11b_companyfacts_float.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_11b_public_float.csv"

FIRST_CALENDAR_YEAR = 2011
LAST_CALENDAR_YEAR = 2025
USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.3

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def fetch_frame(period: str) -> pd.DataFrame | None:
    url = f"https://data.sec.gov/api/xbrl/frames/dei/EntityPublicFloat/USD/{period}.json"
    try:
        resp = get_with_hard_timeout(SESSION, url, hard_timeout_seconds=120, timeout=(10, 60))
    except (requests.RequestException, HardTimeoutError) as e:
        print(f"  [warn] {period}: {e}", flush=True)
        return None
    if resp.status_code == 404:  # frame does not exist for this period
        return pd.DataFrame(columns=["accn", "cik", "end", "val", "frame"])
    resp.raise_for_status()
    df = pd.DataFrame(resp.json()["data"])[["accn", "cik", "end", "val"]]
    df["frame"] = period
    return df


def collect_frames() -> pd.DataFrame:
    done = set(pd.read_csv(FRAMES_PATH, dtype=str)["frame"]) if FRAMES_PATH.exists() else set()
    for year in range(FIRST_CALENDAR_YEAR, LAST_CALENDAR_YEAR + 1):
        for quarter in range(1, 5):
            period = f"CY{year}Q{quarter}I"
            if period in done:
                continue
            df = fetch_frame(period)
            time.sleep(REQUEST_DELAY_SECONDS)
            if df is None:
                continue
            df.to_csv(FRAMES_PATH, mode="a", header=not FRAMES_PATH.exists(), index=False)
            print(f"  {period}: {len(df)} floats", flush=True)
    return pd.read_csv(FRAMES_PATH, dtype={"accn": str, "cik": str})


def unlinked_ciks(frames: pd.DataFrame) -> list[str]:
    """CIKs with at least one firm-year whose own or sibling accession has no float in the frames."""
    linked = link_to_firm_years(frames.assign(_dummy=1))
    return sorted(linked.loc[linked["float_source"] == "missing", "cik"].unique())


def collect_companyfacts(ciks: list[str]) -> pd.DataFrame:
    cols = ["cik", "accn", "end", "val", "form"]
    done = set(pd.read_csv(COMPANYFACTS_PATH, dtype=str)["cik"]) if COMPANYFACTS_PATH.exists() else set()
    todo = [c for c in ciks if c not in done]
    print(f"Company Facts: {len(todo)} CIKs to fetch ({len(done)} already cached)", flush=True)
    for i, cik in enumerate(todo, 1):
        try:
            resp = get_with_hard_timeout(SESSION, f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json",
                                         hard_timeout_seconds=120, timeout=(10, 60))
        except (requests.RequestException, HardTimeoutError) as e:
            print(f"  [warn] {cik}: {e}", flush=True)
            time.sleep(REQUEST_DELAY_SECONDS)
            continue
        rows = []
        if resp.status_code == 200:
            facts = resp.json().get("facts", {}).get("dei", {}).get("EntityPublicFloat", {}).get("units", {}).get("USD", [])
            rows = [{"cik": cik, "accn": x["accn"], "end": x["end"], "val": x["val"], "form": x.get("form")} for x in facts]
        # A marker row (accn empty) records "fetched, nothing found" so a resume does not refetch.
        out = pd.DataFrame(rows or [{"cik": cik, "accn": None, "end": None, "val": None, "form": None}], columns=cols)
        out.to_csv(COMPANYFACTS_PATH, mode="a", header=not COMPANYFACTS_PATH.exists(), index=False)
        if i % 100 == 0:
            print(f"  {i}/{len(todo)}", flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)
    return pd.read_csv(COMPANYFACTS_PATH, dtype={"cik": str, "accn": str}).dropna(subset=["accn"])


def link_to_firm_years(frames: pd.DataFrame) -> pd.DataFrame:
    frames = frames.drop_duplicates("accn").set_index("accn")
    annual = pd.read_csv(ANNUAL_PATH, dtype={"cik": str})
    raw = pd.read_csv(RAW_QUARTERS_PATH, dtype={"cik": str}).dropna(subset=["accession", "fiscal_year"])
    raw["fiscal_year"] = raw["fiscal_year"].astype(int)
    siblings = raw.groupby(["cik", "fiscal_year"])["accession"].apply(list).to_dict()

    rows = []
    for r in annual.itertuples():
        accn, source = r.accession, "own_filing"
        if accn not in frames.index:
            accn, source = next((a for a in siblings.get((r.cik, r.fiscal_year), []) if a in frames.index), None), "sibling_filing"
        if accn is None:
            rows.append({"cik": r.cik, "fiscal_year": r.fiscal_year, "entity_public_float": None,
                         "float_date": None, "float_accn": None, "float_source": "missing"})
        else:
            f = frames.loc[accn]
            rows.append({"cik": r.cik, "fiscal_year": r.fiscal_year, "entity_public_float": f["val"],
                         "float_date": f["end"], "float_accn": accn, "float_source": source})
    return pd.DataFrame(rows)


def main():
    frames = collect_frames()
    print(f"{len(frames)} float facts across {frames['frame'].nunique()} calendar frames")
    cf = collect_companyfacts(unlinked_ciks(frames))
    combined = pd.concat([frames[["accn", "cik", "end", "val"]], cf[["accn", "cik", "end", "val"]]])
    out = link_to_firm_years(combined)
    out.to_csv(OUT_PATH, index=False)
    print(out["float_source"].value_counts().to_string())
    print(f"float > 0: {(out['entity_public_float'] > 0).sum()} of {len(out)} firm-years")


if __name__ == "__main__":
    main()
