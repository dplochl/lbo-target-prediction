"""
Stage 8 — Detect SPAC business-combination proxies swept into Stage 2a's
DEFM14A/PREM14A/DEFM14C/PREM14C scan.

Problem: a SPAC (blank-check shell company) files its own DEFM14A/PREM14A
to get shareholder approval for its business combination - structurally
the *opposite* of a going-private event (a private target becomes public
via the SPAC's shell, not a public company leaving public markets). Stage
2a's full-index scan for those four form types has no way to distinguish
this from a genuine going-private merger proxy, so SPACs got swept in
(caught during manual review of the unknown_buyer_type list - 9 known
cases: Capitol Acquisition Corp III, Black Ridge Acquisition Corp, Leisure
Acquisition Corp, Software Acquisition Group, 10X Capital Venture
Acquisition Corp, Hudson Executive Investment Corp, Power & Digital
Infrastructure Acquisition Corp, 26 Capital Acquisition Corp, EJF
Acquisition Corp).

Detection method: EDGAR full-text search for the exact phrase "is a blank
check company" - restricted to this CIK's own DEFM14A/PREM14A/DEFM14C/
PREM14C filings. Went through two rejected alternatives first: (1) a
company-name pattern like "Acquisition Corp" catches only ~263 candidates
and misses known cases with no such naming convention (Churchill Capital
Corp, Trio Merger Corp); (2) the bare phrase "blank check company" (no
"is a") produces false positives - caught on EngageSmart, Inc. and
Endeavor Group Holdings, Inc., where the phrase only appears in a
director's biography describing past board service at other, unrelated
SPACs ("...served on the board of Thunder Bridge Acquisition II, a blank
check company..."), not describing the filer itself. Requiring the exact
"is a blank check company" construction is what every genuine SPAC's own
boilerplate uses to describe itself (confirmed against all 9 known cases
plus both false positives before adopting this phrase) and excludes the
third-party biographical pattern, which never uses "is a" directly before
the phrase. Also doesn't depend on the CIK's *current* SEC metadata, which
for a completed SPAC merger reflects the post-merger operating company,
not the shell that filed the proxy (e.g. CIK 1708341 is now named "All In
FutureTech Alliance, Inc." with a completely different SIC code -
confirmed by checking, not assumed).

This does not remove anything from combined_events.csv - it only adds an
is_spac_reverse_merger flag, so the exclusion is a documented, reversible
filter applied at analysis time, not a silent deletion.

Output: data/interim/stage_08_spac_flags.csv
    cik, is_spac_reverse_merger, spac_evidence_accession
"""

import time
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = REPO_ROOT / "data" / "interim" / "stage_02a_proxy_candidates.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_08_spac_flags.csv"

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.2

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

FIELDNAMES = ["cik", "is_spac_reverse_merger", "spac_evidence_accession"]


def check_spac(cik: str) -> dict:
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        '?q=%22is%20a%20blank%20check%20company%22'
        "&forms=DEFM14A,PREM14A,DEFM14C,PREM14C&ciks=" + cik
    )
    last_error = None
    for attempt in range(3):
        try:
            resp = get_with_hard_timeout(SESSION, url)
            resp.raise_for_status()
            last_error = None
            break
        except (requests.RequestException, HardTimeoutError) as e:
            last_error = e
            time.sleep(2 * (attempt + 1))
    if last_error is not None:
        print(f"  [warn] SPAC check failed for CIK {cik} after retries: {last_error}", flush=True)
        return {"cik": cik, "is_spac_reverse_merger": None, "spac_evidence_accession": None}
    time.sleep(REQUEST_DELAY_SECONDS)

    data = resp.json()
    hits = data.get("hits", {}).get("hits", [])
    if hits:
        return {"cik": cik, "is_spac_reverse_merger": True, "spac_evidence_accession": hits[0]["_source"].get("adsh")}
    return {"cik": cik, "is_spac_reverse_merger": False, "spac_evidence_accession": None}


def load_already_processed() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    return set(pd.read_csv(OUT_PATH, dtype={"cik": str})["cik"])


def main():
    import csv

    candidates = pd.read_csv(CANDIDATES_PATH, dtype={"cik": str})
    already_done = load_already_processed()
    if already_done:
        print(f"Resuming: {len(already_done)} already checkpointed.", flush=True)
    remaining = candidates[~candidates["cik"].isin(already_done)]
    print(f"Checking {len(remaining)} remaining candidates (of {len(candidates)} total)...")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not OUT_PATH.exists()
    n_processed = 0

    with open(OUT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
            f.flush()

        for _, row in remaining.iterrows():
            writer.writerow(check_spac(row["cik"]))
            f.flush()
            n_processed += 1
            if n_processed % 200 == 0:
                print(f"  ...checked {n_processed}/{len(remaining)}", flush=True)

    result = pd.read_csv(OUT_PATH, dtype={"cik": str})
    print(f"SPAC business-combination proxies detected: {result['is_spac_reverse_merger'].sum()}/{len(result)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
