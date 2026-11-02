"""
Stage 2c — Form 25 (delisting) lookup for every candidate CIK from both event
sources (Stage 1's SC 13E-3 cohort + Stage 2a's proxy candidates).

Per design_memo.md v1.3 Section 1: Form 25 is used ONLY to verify a company
actually left public markets and roughly when - never as standalone
confirmation that a deal was PE-backed. This script does not classify
anything; it just stamps a delisting flag/date onto each CIK.

Matches both "25" (issuer's own Exchange Act Rule 12h-3 deregistration) and
"25-NSE" (the exchange's own notice of removal from listing) - and their "/A"
amendments - since either can be the first available signal that a company's
securities actually left the exchange.

Output: data/interim/form25_lookup.csv
    cik, form25_present, form25_date, form25_form_type
"""

import time
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_01_PATH = REPO_ROOT / "data" / "interim" / "stage_01_events.csv"
STAGE_02A_PATH = REPO_ROOT / "data" / "interim" / "stage_02a_proxy_candidates.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "form25_lookup.csv"

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.2

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

FORM_25_TYPES = {"25", "25/A", "25-NSE", "25-NSE/A"}


def get_all_filings(cik: str) -> list[tuple[str, str]]:
    """Return [(form_type, filing_date), ...] across all of a CIK's history."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = get_with_hard_timeout(SESSION, url)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)

    data = resp.json()
    recent = data["filings"]["recent"]
    history = list(zip(recent["form"], recent["filingDate"]))

    for older_file in data["filings"].get("files", []):
        older_resp = get_with_hard_timeout(SESSION, f"https://data.sec.gov/submissions/{older_file['name']}")
        older_resp.raise_for_status()
        time.sleep(REQUEST_DELAY_SECONDS)
        older_data = older_resp.json()
        history.extend(zip(older_data["form"], older_data["filingDate"]))

    return history


def lookup_form_25(cik: str) -> dict:
    try:
        history = get_all_filings(cik)
    except (requests.RequestException, HardTimeoutError) as e:
        print(f"  [warn] lookup failed for CIK {cik}: {e}", flush=True)
        return {"cik": cik, "form25_present": None, "form25_date": None, "form25_form_type": f"(lookup failed: {e})"}

    matches = sorted(
        ((form, date) for form, date in history if form in FORM_25_TYPES),
        key=lambda x: x[1],
    )
    if matches:
        form, date = matches[0]
        return {"cik": cik, "form25_present": True, "form25_date": date, "form25_form_type": form}
    return {"cik": cik, "form25_present": False, "form25_date": None, "form25_form_type": None}


FIELDNAMES = ["cik", "form25_present", "form25_date", "form25_form_type"]


def load_already_processed() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    done = pd.read_csv(OUT_PATH, dtype={"cik": str})
    return set(done["cik"])


def main():
    import csv

    stage_01 = pd.read_csv(STAGE_01_PATH, dtype={"cik": str})
    stage_02a = pd.read_csv(STAGE_02A_PATH, dtype={"cik": str})
    all_ciks = sorted(set(stage_01["cik"]) | set(stage_02a["cik"]))

    already_done = load_already_processed()
    if already_done:
        print(f"Resuming: {len(already_done)} CIKs already checkpointed, skipping those.", flush=True)
    remaining = [cik for cik in all_ciks if cik not in already_done]
    print(f"Looking up Form 25 status for {len(remaining)} remaining CIKs (of {len(all_ciks)} total)...")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not OUT_PATH.exists()
    with open(OUT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
            f.flush()

        for i, cik in enumerate(remaining, start=1):
            writer.writerow(lookup_form_25(cik))
            f.flush()
            if i % 50 == 0:
                print(f"  ...looked up {i}/{len(remaining)}", flush=True)

    result = pd.read_csv(OUT_PATH, dtype={"cik": str})
    print(f"Form 25 found for {result['form25_present'].sum()}/{len(result)} CIKs")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
