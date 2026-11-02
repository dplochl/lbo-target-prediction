"""
Stage 10 — Episode-clustering fix for the "wrong deal" bug (Stage 2b).

Found via Golden Entertainment during goldstandard sampling:
pick_representative_filing() picked a filing by form-type priority alone
(DEFM14A over PREM14A) with no date logic, so a company with multiple
unrelated M&A events over its history (e.g. Golden's 2015 Lakes/Golden
Gaming reverse merger AND its real 2025 Sartini take-private) could get
its narrative extracted from the wrong, unrelated episode.

Scope: the 139 candidates confirmed to have a >18-month gap between some
consecutive filing dates (a >2yr filing-date span alone was checked first
and found to include one legitimate long negotiation - Rite Aid Corp -
so the max-gap check is what actually identifies genuinely separate
deal episodes, not just a long single negotiation).

Fix logic:
1. Cluster a candidate's own proxy filings into episodes (consecutive
   filings <=18 months apart are the same episode).
2. If the event also has an SC 13E-3 (sc13e3_present), anchor to the
   SC 13E-3's filing dates and pick whichever proxy episode is closest.
   If no episode is within 18 months of the anchor, treat it as "no
   relevant proxy episode" (proxy_present is functionally not usable for
   this event) rather than picking a distant wrong one - the exact
   situation this schema hadn't handled for Ashford Inc.
3. Otherwise (proxy-only), pick the most recent episode.
4. Within the chosen episode, apply the original form-type priority
   (DEFM14A > DEFM14C > PREM14A > PREM14C) to pick the exact filing.

Output: data/interim/stage_10_episode_fix.csv
    cik, chosen_accession, chosen_form, chosen_date, episode_relevant,
    background_found, buyer_name, buyer_name_quote, buyer_type,
    buyer_type_quote, abstained
"""

import importlib
import time
from pathlib import Path

import pandas as pd

narrative_mod = importlib.import_module("02b_extract_proxy_narrative")
classify_mod = importlib.import_module("05_buyer_classification")

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = REPO_ROOT / "data" / "interim" / "stage_02a_proxy_candidates.csv"
COMBINED_EVENTS_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"
AFFECTED_PATH = Path("/tmp/episode_affected_ciks.csv")
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_10_episode_fix.csv"

GAP_THRESHOLD_YEARS = 1.5

FIELDNAMES = [
    "cik", "chosen_accession", "chosen_form", "chosen_date", "episode_relevant",
    "background_found", "buyer_name", "buyer_name_quote", "buyer_type",
    "buyer_type_quote", "abstained",
]


def cluster_episodes(dates: list) -> list[list[int]]:
    idx_sorted = sorted(range(len(dates)), key=lambda i: dates[i])
    episodes = [[idx_sorted[0]]]
    for prev_i, cur_i in zip(idx_sorted, idx_sorted[1:]):
        gap = (dates[cur_i] - dates[prev_i]).days / 365.25
        if gap <= GAP_THRESHOLD_YEARS:
            episodes[-1].append(cur_i)
        else:
            episodes.append([cur_i])
    return episodes


def pick_episode_and_filing(cand_row: pd.Series, event_row: pd.Series):
    accessions = cand_row["all_accession_numbers"].split(";")
    forms = cand_row["all_form_types"].split(";")
    urls = cand_row["all_primary_doc_urls"].split(";")
    dates = pd.to_datetime(cand_row["all_filing_dates"].split(";")).tolist()

    episodes = cluster_episodes(dates)

    sc13e3_dates_str = event_row.get("sc13e3_filing_dates")
    if pd.notna(sc13e3_dates_str):
        anchor = pd.to_datetime(sc13e3_dates_str.split(";")).max()

        def episode_dist(ep):
            return min(abs((dates[i] - anchor).days) for i in ep) / 365.25

        target_episode = min(episodes, key=episode_dist)
        if episode_dist(target_episode) > GAP_THRESHOLD_YEARS:
            return None, None, None, None, False  # no relevant proxy episode
    else:
        target_episode = max(episodes, key=lambda ep: max(dates[i] for i in ep))

    for wanted in ("DEFM14A", "DEFM14C", "PREM14A", "PREM14C"):
        for i in target_episode:
            if forms[i] == wanted and urls[i]:
                return accessions[i], forms[i], dates[i], urls[i], True
    return None, None, None, None, True


def load_already_processed() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    return set(pd.read_csv(OUT_PATH, dtype={"cik": str})["cik"])


def main():
    import csv, os

    classify_mod.load_dotenv_if_present()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set.")
    client = __import__("anthropic").Anthropic()

    affected = pd.read_csv(AFFECTED_PATH, dtype={"cik": str})["cik"].tolist()
    candidates = pd.read_csv(CANDIDATES_PATH, dtype={"cik": str})
    events = pd.read_csv(COMBINED_EVENTS_PATH, dtype={"cik": str})
    print(f"Target: {len(affected)} episode-affected events")

    already_done = load_already_processed()
    if already_done:
        print(f"Resuming: {len(already_done)} already checkpointed.", flush=True)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not OUT_PATH.exists()
    n_processed = 0

    with open(OUT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
            f.flush()

        for cik in affected:
            if cik in already_done:
                continue

            cand_row = candidates[candidates["cik"] == cik]
            event_row = events[events["cik"] == cik]
            if len(cand_row) == 0 or len(event_row) == 0:
                continue
            cand_row = cand_row.iloc[0]
            event_row = event_row.iloc[0]

            accession, form, date, url, relevant = pick_episode_and_filing(cand_row, event_row)
            result = {
                "cik": cik, "chosen_accession": accession, "chosen_form": form,
                "chosen_date": date, "episode_relevant": relevant,
                "background_found": False, "buyer_name": None, "buyer_name_quote": None,
                "buyer_type": None, "buyer_type_quote": None, "abstained": True,
            }

            if url:
                text = narrative_mod.fetch_document_text(url, accession)
                if text:
                    head = text[:narrative_mod.HEAD_SEARCH_CHARS]
                    buyer_raw = narrative_mod.extract_buyer_raw(head)
                    parent_merger = narrative_mod.extract_parent_and_merger_sub(head)
                    background = narrative_mod.extract_background_section(text)

                    narrative_path = None
                    if background and len(background.strip()) >= 500:
                        result["background_found"] = True
                        narrative_path = narrative_mod.NARRATIVE_DIR / f"{cik}.txt"
                        narrative_path.write_text(background, encoding="utf-8")

                    combined_row = event_row.copy()
                    combined_row["buyer_raw"] = buyer_raw
                    combined_row["parent_entity"] = parent_merger.get("parent_entity")
                    combined_row["merger_sub_entity"] = parent_merger.get("merger_sub_entity")
                    combined_row["narrative_text_path"] = (
                        str(narrative_path.relative_to(REPO_ROOT)) if narrative_path else None
                    )

                    evidence_text = classify_mod.build_evidence_text(combined_row)
                    try:
                        clf = classify_mod.classify(client, evidence_text)
                        result.update({
                            "buyer_name": clf.get("buyer_name"),
                            "buyer_name_quote": clf.get("buyer_name_quote"),
                            "buyer_type": clf.get("buyer_type"),
                            "buyer_type_quote": clf.get("buyer_type_quote"),
                            "abstained": clf.get("abstain", True),
                        })
                    except Exception as e:
                        print(f"  [warn] classification failed for {cik}: {e}", flush=True)
            elif not relevant:
                # no matching proxy episode - try the SC 13E-3's own text instead,
                # same approach as Stage 6, reusing its functions directly.
                sc13e3_mod = importlib.import_module("06_sc13e3_reclassification")
                sc13e3_accessions = event_row.get("sc13e3_accession_numbers")
                if pd.notna(sc13e3_accessions):
                    sc_accession = sc13e3_accessions.split(";")[0]
                    sc_url = sc13e3_mod.fetch_primary_doc_url(cik, sc_accession)
                    if sc_url:
                        sc_text = sc13e3_mod.fetch_document_text(sc_url, sc_accession)
                        if sc_text:
                            filing_persons = sc13e3_mod.extract_filing_persons(sc_text)
                            combined_row = event_row.copy()
                            combined_row["filed_by_names"] = filing_persons
                            combined_row["narrative_text_path"] = None
                            evidence_text = classify_mod.build_evidence_text(combined_row)
                            try:
                                clf = classify_mod.classify(client, evidence_text)
                                result.update({
                                    "buyer_name": clf.get("buyer_name"),
                                    "buyer_name_quote": clf.get("buyer_name_quote"),
                                    "buyer_type": clf.get("buyer_type"),
                                    "buyer_type_quote": clf.get("buyer_type_quote"),
                                    "abstained": clf.get("abstain", True),
                                })
                            except Exception as e:
                                print(f"  [warn] SC13E3-fallback classification failed for {cik}: {e}", flush=True)

            writer.writerow(result)
            f.flush()
            time.sleep(0.1)

            n_processed += 1
            if n_processed % 25 == 0:
                print(f"  ...processed {n_processed}/{len(affected) - len(already_done)}", flush=True)

    result = pd.read_csv(OUT_PATH, dtype={"cik": str})
    print(f"Episode not relevant (fell back to SC13E3 text): {(~result['episode_relevant']).sum()}/{len(result)}")
    print(f"Newly classified (not abstained): {(~result['abstained']).sum()}/{len(result)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
