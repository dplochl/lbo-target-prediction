"""
Stage 3 — Merge the two event sources into one combined event table.

Per design_memo.md v1.3 Section 1: "a combined event schema, not two parallel
systems" - each event carries source flags (sc13e3_present, proxy_present,
form25_present), placeholder type flags (affiliate_involved, third_party_pe,
pe_backed) that Stage 2's later LLM classification will fill in, and a
classification_status workflow field. Every row from this build gets
classification_status = "pending" since no classification has run yet.

data/interim/stage_01_events.csv (the SC-13E-3-only cohort) is left untouched
- it remains the `sc13e3_only` robustness cohort per design_memo.md.

classification_status enum (locked 2026, confirmed by the author so Stage 2
doesn't need a schema migration later):
    pending, included, excluded_no_pe_evidence,
    excluded_deal_not_completed, uncertain_needs_manual_review

Output: data/interim/combined_events.csv
"""

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGE_01_PATH = REPO_ROOT / "data" / "interim" / "stage_01_events.csv"
STAGE_02A_PATH = REPO_ROOT / "data" / "interim" / "stage_02a_proxy_candidates.csv"
STAGE_02B_PATH = REPO_ROOT / "data" / "interim" / "stage_02b_proxy_narrative.csv"
FORM_25_PATH = REPO_ROOT / "data" / "interim" / "form25_lookup.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"


def main():
    sc13e3 = pd.read_csv(STAGE_01_PATH, dtype={"cik": str})
    proxy = pd.read_csv(STAGE_02A_PATH, dtype={"cik": str})
    narrative = pd.read_csv(STAGE_02B_PATH, dtype={"cik": str})
    form25 = pd.read_csv(FORM_25_PATH, dtype={"cik": str})

    proxy = proxy.merge(narrative, on="cik", how="left")

    sc13e3 = sc13e3.rename(columns={
        "company_name": "sc13e3_company_name",
        "first_event_date": "sc13e3_first_event_date",
        "all_filing_dates": "sc13e3_filing_dates",
        "all_accession_numbers": "sc13e3_accession_numbers",
    })
    proxy = proxy.rename(columns={
        "company_name": "proxy_company_name",
        "first_filing_date": "proxy_first_filing_date",
        "all_filing_dates": "proxy_filing_dates",
        "all_accession_numbers": "proxy_accession_numbers",
        "all_form_types": "proxy_form_types",
    })

    combined = pd.merge(sc13e3, proxy, on="cik", how="outer")
    combined = combined.merge(form25, on="cik", how="left")

    combined["company_name"] = combined["sc13e3_company_name"].fillna(combined["proxy_company_name"])
    combined["sc13e3_present"] = combined["sc13e3_accession_numbers"].notna()
    combined["proxy_present"] = combined["proxy_accession_numbers"].notna()
    combined["first_event_date"] = combined[
        ["sc13e3_first_event_date", "proxy_first_filing_date"]
    ].min(axis=1)

    # Placeholders for Stage 2's LLM classification - not computed here.
    combined["affiliate_involved"] = pd.NA
    combined["third_party_pe"] = pd.NA
    combined["pe_backed"] = pd.NA
    combined["classification_status"] = "pending"

    columns = [
        "cik", "company_name", "first_event_date",
        "sc13e3_present", "proxy_present", "form25_present", "form25_date", "form25_form_type",
        "sc13e3_first_event_date", "sc13e3_filing_dates", "sc13e3_accession_numbers",
        "filed_by_ciks", "filed_by_names",
        "proxy_first_filing_date", "proxy_filing_dates", "proxy_accession_numbers", "proxy_form_types",
        "buyer_raw", "parent_entity", "merger_sub_entity",
        "background_section_found", "narrative_text_path",
        "affiliate_involved", "third_party_pe", "pe_backed", "classification_status",
    ]
    combined = combined[columns].sort_values("first_event_date")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(OUT_PATH, index=False)

    print(f"Total combined events: {len(combined)}")
    print(f"  SC 13E-3 only:        {(combined['sc13e3_present'] & ~combined['proxy_present']).sum()}")
    print(f"  Proxy only:           {(~combined['sc13e3_present'] & combined['proxy_present']).sum()}")
    print(f"  Both sources:         {(combined['sc13e3_present'] & combined['proxy_present']).sum()}")
    print(f"  Form 25 confirmed:    {combined['form25_present'].sum()}")
    print(f"Wrote {OUT_PATH}")

    print("\nNote: data/interim/stage_01_events.csv (726 events) is left untouched")
    print("as the sc13e3_only robustness cohort per design_memo.md v1.3.")


if __name__ == "__main__":
    main()
