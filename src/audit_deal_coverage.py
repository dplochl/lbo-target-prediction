"""
Coverage audit: sanity-check Stage 1 (data/interim/stage_01_events.csv)
against a hand-picked list of well-known US PE take-private deals, 2010-2025.

For each deal:
1. Is its CIK present in stage_01_events.csv?
2. Independently (via SEC's data.sec.gov submissions API, not the Stage 1
   pipeline itself) - did the company actually file an SC 13E-3, or only a
   merger proxy (DEFM14A) / tender-offer response (SC 14D9)?

This is a deliberate second, independent check of Stage 1's own claim (SC
13E-3 requires affiliate involvement - see PROGRESS.md 2026-09-17 entry) using
a different EDGAR endpoint than Stage 1 uses (submissions API vs. full-index +
filing index pages), so a bug shared by both wouldn't silently agree.
"""

import time
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import get_user_agent

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_01_PATH = REPO_ROOT / "data" / "interim" / "stage_01_events.csv"

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})
REQUEST_DELAY_SECONDS = 0.2

# Hand-picked deals: mix of management-led/affiliate-involved (expected to
# require SC 13E-3) and pure third-party sponsor deals (expected to NOT
# require it, per Rule 13e-3's affiliate-involvement trigger).
CANDIDATE_DEALS = [
    # name, cik, year, expected structure
    ("Dell Inc.", "0000826083", 2013, "management-led (Michael Dell + Silver Lake)"),
    ("Dole Food Company", "0000018169", 2013, "management-led (David Murdock MBO)"),
    ("J.Crew Group", "0001051251", 2011, "management participation (TPG/Leonard Green + Mickey Drexler)"),
    ("Stein Mart", "0000884940", 2020, "controlling family-led (Stein family; deal later collapsed)"),
    ("Del Monte Foods", "0000866873", 2011, "pure third-party sponsor (KKR/Vestar/Centerview)"),
    ("PetSmart", "0000863157", 2015, "pure third-party sponsor (BC Partners)"),
    ("TIBCO Software", "0001085280", 2014, "pure third-party sponsor (Vista Equity Partners)"),
    ("Panera Bread", "0000724606", 2017, "pure third-party (JAB Holding)"),
    ("Web.com Group", "0001095291", 2018, "pure third-party sponsor (Siris Capital)"),
    ("Cablevision Systems", "0001053112", 2016, "controlling family (Dolan) + strategic acquirer (Altice)"),
]

# DEFM14C/PREM14C: the information-statement equivalent of DEFM14A, used
# when a controlling shareholder approves the merger by written consent
# instead of a shareholder vote (e.g. Cablevision/Dolan family in 2016) -
# found by checking a "(none found)" result by hand, not anticipated up front.
RELEVANT_FORMS = {
    "SC 13E3", "SC 13E3/A",
    "DEFM14A", "DEFM14C", "PREM14C",
    "SC 14D9", "SC 14D9/A",
}


def get_filing_history(cik: str) -> list[tuple[str, str]]:
    """Return [(form_type, filing_date), ...] from SEC's submissions API."""
    url = f"https://data.sec.gov/submissions/CIK{cik}.json"
    resp = SESSION.get(url, timeout=30)
    resp.raise_for_status()
    time.sleep(REQUEST_DELAY_SECONDS)

    data = resp.json()
    recent = data["filings"]["recent"]
    history = list(zip(recent["form"], recent["filingDate"]))

    # Older filings live in separate paginated files, not the "recent" block.
    for older_file in data["filings"].get("files", []):
        older_url = f"https://data.sec.gov/submissions/{older_file['name']}"
        older_resp = SESSION.get(older_url, timeout=30)
        older_resp.raise_for_status()
        time.sleep(REQUEST_DELAY_SECONDS)
        older_data = older_resp.json()
        history.extend(zip(older_data["form"], older_data["filingDate"]))

    return history


def summarize_relevant_forms(history: list[tuple[str, str]], year: int) -> str:
    """Collapse a filing history into a short 'SC 13E3 (2013); DEFM14A (2013)' summary."""
    window_start = f"{year - 1}-01-01"
    window_end = f"{year + 1}-12-31"
    seen = {}
    for form, date in history:
        if form in RELEVANT_FORMS and window_start <= date <= window_end:
            seen.setdefault(form, date)  # keep earliest date per form type
    if not seen:
        return "(none found)"
    return "; ".join(f"{form} ({date[:4]})" for form, date in sorted(seen.items(), key=lambda kv: kv[1]))


def main():
    stage_01 = pd.read_csv(STAGE_01_PATH, dtype={"cik": str})
    stage_01_ciks = set(stage_01["cik"])

    rows = []
    for name, cik, year, structure in CANDIDATE_DEALS:
        in_stage_01 = cik in stage_01_ciks
        try:
            history = get_filing_history(cik)
            has_13e3 = any(f.startswith("SC 13E3") for f, d in history if f"{year - 1}" <= d[:4] <= f"{year + 1}")
            forms_summary = summarize_relevant_forms(history, year)
        except requests.RequestException as e:
            has_13e3 = None
            forms_summary = f"(lookup failed: {e})"

        rows.append(
            {
                "deal": name,
                "year": year,
                "structure": structure,
                "in_stage_01_events": "yes" if in_stage_01 else "no",
                "actual_SC_13E3_filed": (
                    "yes" if has_13e3 else "no" if has_13e3 is False else "unknown"
                ),
                "relevant_filings_found": forms_summary,
            }
        )

    result = pd.DataFrame(rows)

    print(result.to_string(index=False))

    # Flag any mismatch between "did we capture it" and "did EDGAR actually
    # require a 13E-3" - the two should always agree, since Stage 1 is built
    # entirely from SC 13E-3 filings.
    mismatches = result[
        (result["in_stage_01_events"] == "yes") & (result["actual_SC_13E3_filed"] == "no")
        | (result["in_stage_01_events"] == "no") & (result["actual_SC_13E3_filed"] == "yes")
    ]
    print()
    if len(mismatches):
        print("MISMATCHES (Stage 1 coverage disagrees with actual SC 13E-3 filing history):")
        print(mismatches.to_string(index=False))
    else:
        print("No mismatches: Stage 1 coverage agrees with actual SC 13E-3 filing history for all deals checked.")

    out_path = REPO_ROOT / "data" / "interim" / "coverage_audit.csv"
    result.to_csv(out_path, index=False)
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
