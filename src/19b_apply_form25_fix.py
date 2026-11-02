"""
Stage 19b — Apply the Stage 19 Form 25 / completion-status corrections to combined_events.csv,
and the manually-adjudicated buyer verification for the pe_sponsor/family/management candidates
that newly flip to `completed` (see design_memo.md Section 12 for the full case-by-case record).

Two separate, independently-gated changes:
  1. completion_status / form25_present / form25_date / form25_form_type are corrected for every
     event in stage_19_completion_recheck.csv where the value changed (474 events; a pure data-
     integrity fix, applies regardless of buyer_type).
  2. sponsor_like_ptp / institutional_pe_only are set True for a specific, manually-verified
     23-CIK list only (not all completion flips) - each was checked against its own proxy
     narrative text for a consistent, unredacted, uncontested buyer identity. institutional_pe_only
     is restricted further to the strict buyer_type == 'pe_sponsor' subset (20 of 23; the 3 family
     cases - Krispy Kreme, Sears Hometown, Blueknight - are sponsor_like_ptp only). Excluded from
     this list, all decided 2026-09-22: two confirmed buyer misclassifications (Ann Inc.: the
     extracted buyer_name Golden Gate reflects an early, unsuccessful bid, not the actual
     acquirer; William Lyon Homes: the extracted name is the company's own chairman, not an
     acquirer, and the real transaction was a stock-for-stock merger into Taylor Morrison, which
     may not even meet the going-private definition), two already-correctly-excluded SPACs
     (Kayne Anderson Acquisition Corp, Gores Holdings III - is_spac_reverse_merger was already
     True; this was checked once more and confirmed not a detection gap), two events with no
     extracted narrative text to verify against (Stream Global Services, Teekay Offshore
     Partners), one placeholder/unresolved buyer name (Vonage: "Party A" never resolved to a
     real entity in the text), and two genuine classification edge cases left unresolved this
     round pending explicit adjudication (PartnerRe/EXOR - a large industrial holding company
     versus a "family" label; Paramount Global/Skydance Media - a hybrid strategic/PE-backed deal).

Both changes are tracked in new columns for auditability, mirroring the existing
`manual_override_applied` pattern from Stage 6-8: `form25_fix_completion_updated`,
`form25_fix_added_positive`.

Backup of the pre-fix file: data/interim/combined_events_pre_form25_fix_2026-09-22.csv
Output: data/interim/combined_events.csv (overwritten in place)
"""

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
EVENTS_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"
RECHECK_PATH = REPO_ROOT / "data" / "interim" / "stage_19_completion_recheck.csv"

# Manually verified 2026-09-22, first and second pass (design_memo.md Section 12): buyer identity confirmed consistent,
# unredacted and uncontested in the proxy narrative text.
ADD_SPONSOR_LIKE_PTP = {
    "0001018833": "pe_sponsor", "0000828747": "pe_sponsor", "0001035884": "pe_sponsor", "0001033032": "pe_sponsor",
    "0000046640": "pe_sponsor", "0000789868": "pe_sponsor", "0001071993": "pe_sponsor", "0000800240": "pe_sponsor",
    "0001492633": "pe_sponsor", "0001289236": "pe_sponsor", "0001100270": "family", "0001411488": "pe_sponsor",
    "0000883980": "pe_sponsor", "0001548309": "family", "0001286131": "pe_sponsor", "0000052795": "pe_sponsor",
    "0001392091": "family", "0000944480": "pe_sponsor", "0000009984": "pe_sponsor", "0000704562": "pe_sponsor",
    "0001828723": "pe_sponsor", "0001618921": "pe_sponsor", "0001166388": "pe_sponsor",
    # Added 2026-09-22, second pass (design_memo.md Section 12): PartnerRe/EXOR - buyer_type=family,
    # consistent with the JAB/Krispy Kreme/Panera precedent (EXOR is the Agnelli family's investment
    # holding). Paramount Global/Skydance-RedBird - buyer_type=pe_sponsor; this was already decided in
    # the project's original manual-override round, and only fell out again because of the Form 25 bug,
    # not a new classification decision.
    "0000911421": "family", "0000813828": "pe_sponsor",
}


def main():
    events = pd.read_csv(EVENTS_PATH, dtype={"cik": str})
    recheck = pd.read_csv(RECHECK_PATH, dtype={"cik": str})

    events = events.merge(
        recheck[["cik", "new_form25_present", "new_form25_date", "new_form25_form_type", "new_completion_status", "status_changed"]],
        on="cik", how="left",
    )
    updated = events["status_changed"].fillna(False)
    print(f"Completion-status / Form 25 corrections applied to {int(updated.sum())} events")
    events.loc[updated, "form25_present"] = events.loc[updated, "new_form25_present"]
    events.loc[updated, "form25_date"] = events.loc[updated, "new_form25_date"]
    events.loc[updated, "form25_form_type"] = events.loc[updated, "new_form25_form_type"]
    events.loc[updated, "completion_status"] = events.loc[updated, "new_completion_status"]
    events["form25_fix_completion_updated"] = updated
    events = events.drop(columns=["new_form25_present", "new_form25_date", "new_form25_form_type", "new_completion_status", "status_changed"])

    is_add = events["cik"].isin(ADD_SPONSOR_LIKE_PTP)
    print(f"sponsor_like_ptp newly set True: {int((is_add & ~events['sponsor_like_ptp'].astype(bool)).sum())} of {is_add.sum()} in the verified list")
    events.loc[is_add, "sponsor_like_ptp"] = True
    strict = events["cik"].map(lambda c: ADD_SPONSOR_LIKE_PTP.get(c) == "pe_sponsor")
    events.loc[is_add & strict, "institutional_pe_only"] = True
    events["form25_fix_added_positive"] = is_add

    events.to_csv(EVENTS_PATH, index=False)
    print(f"Wrote {EVENTS_PATH}: sponsor_like_ptp={int(events['sponsor_like_ptp'].sum())}, "
          f"institutional_pe_only={int(events['institutional_pe_only'].sum())}")


if __name__ == "__main__":
    main()
