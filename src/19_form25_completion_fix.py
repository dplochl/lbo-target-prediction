"""
Stage 19 — Form 25 selection fix and completion-status recheck.

Bug found while investigating a Random-Forest error-analysis lead (Heinz, Safeway): Stage 2c's
lookup_form_25() takes the EARLIEST Form-25-type filing in a CIK's ENTIRE history
(`sorted(matches, key=date)[0]`), not the one associated with the tracked deal. A company can
file Form 25 / 25-NSE multiple times over its life for reasons unrelated to the tracked event
(a delisted debt/preferred class, a prior unrelated going-private-and-relisting cycle, etc.).
Heinz: stored form25_date 2007-01-18 (25), but a 25-NSE was actually filed 2013-06-12 and
2013-07-18, squarely around its real March-2013 Berkshire/3G deal. Safeway: stored 2002-11-04,
but 25-NSE filings exist 2014-06-27 and 2015-01-30, around its real April-2014 Albertsons deal.
Both were consequently scored `not_completed` by Stage 4 (form25_in_window was False using the
stale date), which silently excluded them from sponsor_like_ptp/institutional_pe_only, since
those require completion_status == 'completed'.

Rule adopted for which Form 25 belongs to a tracked deal (documented here, not just in code):
a Form-25-type filing is attributed to an event only if it falls in
[last_filing_date, last_filing_date + 365 days] - the SAME window Stage 4 already uses for its
completion/termination full-text-search evidence, so "was there a delisting tied to this deal"
and "was there closing/termination language tied to this deal" use one consistent window. If
several qualify, the earliest one in the window is used (the first formal delisting step).
A Form 25 outside that window is an INDEPENDENT delisting, unrelated to the tracked deal, and
does not count as evidence for or against it - form25_present becomes False for that event
(the CIK may still deregister some other class of security, or have delisted/relisted for an
unrelated reason years earlier or later; this pipeline is only trying to verify one specific deal).

Recomputation of completion_status reuses the Stage 4 full-text-search results already cached in
stage_04_completion_status.csv (completion_evidence_accession / termination_evidence_accession) -
those are unaffected by this fix and are not re-fetched. Only the form25_in_window input changes.

Input : data/interim/combined_events.csv, data/interim/stage_04_completion_status.csv
Output: data/interim/stage_19_form25_history.csv     (cik, form, filing_date - every Form-25-type
                                                        filing per form25_present==True CIK; checkpointed)
        data/interim/stage_19_completion_recheck.csv (cik, old_form25_date, new_form25_date,
                                                        old_completion_status, new_completion_status,
                                                        old_form25_present, new_form25_present)
"""

import time
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"
STAGE04_PATH = REPO_ROOT / "data" / "interim" / "stage_04_completion_status.csv"
HISTORY_PATH = REPO_ROOT / "data" / "interim" / "stage_19_form25_history.csv"
DONE_PATH = REPO_ROOT / "data" / "interim" / "stage_19_ciks_done.txt"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_19_completion_recheck.csv"

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.2
WINDOW_DAYS = 365  # same window Stage 4 uses for its full-text-search evidence

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

FORM_25_TYPES = {"25", "25/A", "25-NSE", "25-NSE/A"}


def get_all_filings(cik: str) -> list[tuple[str, str]]:
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


def fetch_histories(ciks: list[str]) -> None:
    done = set(pd.read_csv(HISTORY_PATH, dtype=str)["cik"]) if HISTORY_PATH.exists() else set()
    done |= set(pd.read_csv(DONE_PATH, dtype=str)["cik"]) if DONE_PATH.exists() else set()
    todo = [c for c in ciks if c not in done]
    print(f"Form 25 history refetch: {len(todo)} CIKs to fetch ({len(done)} already done)", flush=True)
    for i, cik in enumerate(todo, 1):
        try:
            history = get_all_filings(cik)
        except (requests.RequestException, HardTimeoutError) as e:
            print(f"  [warn] {cik}: {e}", flush=True)
            continue
        rows = [{"cik": cik, "form": f, "filing_date": d} for f, d in history if f in FORM_25_TYPES]
        out = pd.DataFrame(rows or [{"cik": cik, "form": None, "filing_date": None}])  # marker row if none found
        out.to_csv(HISTORY_PATH, mode="a", header=not HISTORY_PATH.exists(), index=False)
        pd.DataFrame({"cik": [cik]}).to_csv(DONE_PATH, mode="a", header=not DONE_PATH.exists(), index=False)
        if i % 200 == 0:
            print(f"  {i}/{len(todo)}", flush=True)


def last_filing_date(row: pd.Series) -> str | None:
    dates = []
    for col in ("sc13e3_filing_dates", "proxy_filing_dates"):
        val = row.get(col)
        if pd.notna(val):
            dates.extend(val.split(";"))
    return max(dates) if dates else None


def main():
    events = pd.read_csv(EVENTS_PATH, dtype={"cik": str})
    with_form25 = events[events["form25_present"] == True]  # noqa: E712
    print(f"Events with form25_present=True: {len(with_form25)}")
    fetch_histories(sorted(with_form25["cik"].unique()))

    history = pd.read_csv(HISTORY_PATH, dtype=str).dropna(subset=["form"])
    history["filing_date"] = pd.to_datetime(history["filing_date"])
    stage04 = pd.read_csv(STAGE04_PATH, dtype={"cik": str})

    rows = []
    for _, ev in with_form25.iterrows():
        cik = ev["cik"]
        last_date_str = last_filing_date(ev)
        if not last_date_str:
            continue
        last_date = pd.Timestamp(last_date_str)
        window_end = last_date + pd.Timedelta(days=WINDOW_DAYS)
        h = history[history["cik"] == cik]
        in_window = h[(h["filing_date"] >= last_date) & (h["filing_date"] <= window_end)].sort_values("filing_date")
        new_present, new_date, new_form = (True, in_window.iloc[0]["filing_date"].date().isoformat(), in_window.iloc[0]["form"]) \
            if len(in_window) else (False, None, None)

        s4 = stage04[stage04["cik"] == cik]
        old_status = s4["completion_status"].iloc[0] if len(s4) else None
        has_completion = bool(s4["completion_evidence_accession"].notna().iloc[0]) if len(s4) else False
        has_termination = bool(s4["termination_evidence_accession"].notna().iloc[0]) if len(s4) else False
        new_has_completion = has_completion or new_present
        if new_has_completion and has_termination:
            new_status = "uncertain"
        elif has_termination:
            new_status = "not_completed"
        elif new_has_completion:
            new_status = "completed"
        else:
            new_status = "not_completed"

        rows.append({
            "cik": cik, "company_name": ev["company_name"], "first_event_date": ev["first_event_date"],
            "buyer_type": ev.get("buyer_type"), "sponsor_like_ptp": ev.get("sponsor_like_ptp"),
            "institutional_pe_only": ev.get("institutional_pe_only"),
            "old_form25_present": ev["form25_present"], "old_form25_date": ev["form25_date"],
            "new_form25_present": new_present, "new_form25_date": new_date, "new_form25_form_type": new_form,
            "old_completion_status": old_status, "new_completion_status": new_status,
            "status_changed": old_status != new_status,
        })

    out = pd.DataFrame(rows)
    out.to_csv(OUT_PATH, index=False)
    print(f"\nWrote {OUT_PATH}: {len(out)} events with form25_present=True and a usable filing date")
    print(f"form25 attribution changed (old date != new date or presence flipped): "
          f"{int(((out.old_form25_date.astype(str) != out.new_form25_date.astype(str)) | (out.old_form25_present != out.new_form25_present)).sum())}")
    print(f"completion_status changed: {int(out.status_changed.sum())}")
    print(out[out.status_changed]["old_completion_status"].value_counts().rename("old status (of changed rows)").to_string())
    print("->")
    print(out[out.status_changed]["new_completion_status"].value_counts().rename("new status (of changed rows)").to_string())
    flips_to_completed = out[out.status_changed & (out.new_completion_status == "completed")]
    print(f"\nFlipped TO completed: {len(flips_to_completed)}")
    qualifying_buyer = flips_to_completed[flips_to_completed["buyer_type"].isin(["pe_sponsor", "family", "management"])]
    print(f"  of which buyer_type already pe_sponsor/family/management (potential new sponsor_like_ptp candidates): {len(qualifying_buyer)}")
    print(qualifying_buyer[["cik", "company_name", "first_event_date", "buyer_type", "sponsor_like_ptp"]].to_string(index=False))


if __name__ == "__main__":
    main()
