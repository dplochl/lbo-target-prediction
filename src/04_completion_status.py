"""
Stage 4 — Deal completion status, per design_memo.md v1.4 Section 1a.

Motivating problem: Form 25 alone can't confirm a *specific* deal completed
(Stein Mart: Form 25 present, but from an unrelated later bankruptcy, not the
collapsed original take-private deal). This uses EDGAR's full-text search
(efts.sec.gov) to check for explicit closing or termination language in 8-Ks
filed within ~12 months of an event's last SC 13E-3/proxy filing date -
scoped to a specific CIK and date window, so this doesn't require downloading
thousands of full 8-K documents (a phrase search is a server-side exact
match, so a hit is already solid textual evidence - no need to separately
re-fetch and regex-verify each hit).

Three categories only (not an earlier 5-category proposal):
- completed: completion-language 8-K found in window, OR Form 25 falls in
  window with no conflicting (termination) evidence.
- not_completed: termination-language 8-K found in window with no
  conflicting completion evidence, or no evidence of any kind found.
- uncertain: completion and termination evidence both present in the same
  window (the Stein Mart pattern - Form 25 or a completion phrase alongside
  explicit termination language means the automated signal can't cleanly
  tell which event the delisting is actually attributable to). Excluded
  from the main analysis, not manually adjudicated case by case, per v1.4.

Output: data/interim/stage_04_completion_status.csv
    cik, completion_status, last_filing_date, window_end,
    completion_evidence_accession, completion_evidence_items,
    termination_evidence_accession, termination_evidence_items,
    form25_in_window
"""

import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
COMBINED_EVENTS_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_04_completion_status.csv"

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.2
WINDOW_DAYS = 365

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

# Deliberately past-tense subject-verb-object phrasing ("X completed the
# merger"), not bare noun phrases ("completion of the merger", "termination
# of the merger agreement") - the noun-phrase forms turned out to match
# generic forward-looking risk-factor boilerplate in merger-*announcement*
# 8-Ks just as easily as genuine closing/termination announcements (caught
# by testing against Panera/JAB 2017, a deal that closed cleanly but whose
# announcement 8-K falsely matched both the completion and termination
# queries before this fix).
COMPLETION_QUERY = (
    '"completed the merger" OR "consummated the merger" OR '
    '"the merger was completed" OR "the merger has been completed" OR '
    '"the merger was consummated"'
)
TERMINATION_QUERY = (
    '"terminated the merger agreement" OR "mutually agreed to terminate" OR '
    '"abandoned the merger" OR "will not proceed with the merger" OR '
    '"the merger agreement was terminated"'
)

FIELDNAMES = [
    "cik", "completion_status", "last_filing_date", "window_end",
    "completion_evidence_accession", "completion_evidence_items",
    "termination_evidence_accession", "termination_evidence_items",
    "form25_in_window",
]


def full_text_search(query: str, cik: str, start_date: str, end_date: str) -> dict | None:
    url = (
        "https://efts.sec.gov/LATEST/search-index"
        f"?q={requests.utils.quote(query)}&forms=8-K&ciks={cik}"
        f"&startdt={start_date}&enddt={end_date}"
    )
    # efts.sec.gov returns transient 500s under load (confirmed by hand -
    # retrying the identical query moments later succeeded). A bare failure
    # here silently means "no evidence found", which would bias results
    # toward not_completed for whatever event happened to be in flight
    # during a bad patch - so retry before giving up.
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
        print(f"  [warn] FTS query failed for CIK {cik} after retries: {last_error}", flush=True)
        return None
    time.sleep(REQUEST_DELAY_SECONDS)

    data = resp.json()
    hits = data.get("hits", {}).get("hits", [])
    if not hits:
        return None
    # earliest hit in the window is the most likely genuine signal
    hits.sort(key=lambda h: h["_source"].get("file_date", ""))
    top = hits[0]
    return {
        "accession": top["_source"].get("adsh"),
        "items": ";".join(top["_source"].get("items", [])),
        "file_date": top["_source"].get("file_date"),
    }


def last_filing_date(row: pd.Series) -> str | None:
    dates = []
    for col in ("sc13e3_filing_dates", "proxy_filing_dates"):
        val = row.get(col)
        if pd.notna(val):
            dates.extend(val.split(";"))
    return max(dates) if dates else None


def determine_status(cik: str, last_date: str, form25_present, form25_date) -> dict:
    start_dt = datetime.strptime(last_date, "%Y-%m-%d")
    end_dt = start_dt + timedelta(days=WINDOW_DAYS)
    start_str, end_str = start_dt.strftime("%Y-%m-%d"), end_dt.strftime("%Y-%m-%d")

    completion_hit = full_text_search(COMPLETION_QUERY, cik, start_str, end_str)
    termination_hit = full_text_search(TERMINATION_QUERY, cik, start_str, end_str)

    form25_in_window = bool(
        form25_present and pd.notna(form25_date) and start_str <= form25_date <= end_str
    )

    has_completion_signal = bool(completion_hit) or form25_in_window
    has_termination_signal = bool(termination_hit)

    if has_completion_signal and has_termination_signal:
        status = "uncertain"
    elif has_termination_signal:
        status = "not_completed"
    elif has_completion_signal:
        status = "completed"
    else:
        status = "not_completed"

    return {
        "cik": cik,
        "completion_status": status,
        "last_filing_date": last_date,
        "window_end": end_str,
        "completion_evidence_accession": completion_hit["accession"] if completion_hit else None,
        "completion_evidence_items": completion_hit["items"] if completion_hit else None,
        "termination_evidence_accession": termination_hit["accession"] if termination_hit else None,
        "termination_evidence_items": termination_hit["items"] if termination_hit else None,
        "form25_in_window": form25_in_window,
    }


def load_already_processed() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    return set(pd.read_csv(OUT_PATH, dtype={"cik": str})["cik"])


def main():
    import csv

    events = pd.read_csv(COMBINED_EVENTS_PATH, dtype={"cik": str})
    already_done = load_already_processed()
    if already_done:
        print(f"Resuming: {len(already_done)} events already checkpointed.", flush=True)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not OUT_PATH.exists()
    n_processed = 0
    n_skipped_no_date = 0

    with open(OUT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
            f.flush()

        for _, row in events.iterrows():
            if row["cik"] in already_done:
                continue

            last_date = last_filing_date(row)
            if not last_date:
                n_skipped_no_date += 1
                continue

            result = determine_status(row["cik"], last_date, row.get("form25_present"), row.get("form25_date"))
            writer.writerow(result)
            f.flush()

            n_processed += 1
            if n_processed % 50 == 0:
                print(f"  ...processed {n_processed} events", flush=True)

    print(f"Skipped (no filing date): {n_skipped_no_date}")
    result = pd.read_csv(OUT_PATH, dtype={"cik": str})
    print(result["completion_status"].value_counts().to_string())
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
