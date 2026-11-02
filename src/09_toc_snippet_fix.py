"""
Stage 9 — Scoped fix for the plural-regex TOC-snippet bug (Stage 2b).

Found while diagnosing Golden Entertainment during goldstandard sampling:
BACKGROUND_HEADING_PATTERNS only matched singular "Background of the
Transaction", so documents phrased "Background of the Transactions"
(plural) matched mid-word, breaking the TOC-vs-body detection and
silently extracting the Table of Contents entry instead of the real
narrative. Fixed in 02b_extract_proxy_narrative.py; this stage re-runs
extraction + reclassification only for candidates showing the failure
signature (short narrative, mostly whitespace/digits - a genuine ToC
snippet, not a legitimately brief narrative excerpt).

Scope: exactly the 169 flagged CIKs, not a blind rerun of all 3,666.

Output: data/interim/stage_09_toc_fix.csv
    cik, background_found, buyer_name, buyer_name_quote, buyer_type, buyer_type_quote, abstained
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
SUSPECTS_PATH = Path("/tmp/toc_suspects.csv")
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_09_toc_fix.csv"

FIELDNAMES = ["cik", "background_found", "buyer_name", "buyer_name_quote", "buyer_type", "buyer_type_quote", "abstained"]


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

    suspects = pd.read_csv(SUSPECTS_PATH, dtype={"cik": str})["cik"].tolist()
    candidates = pd.read_csv(CANDIDATES_PATH, dtype={"cik": str})
    events = pd.read_csv(COMBINED_EVENTS_PATH, dtype={"cik": str})
    print(f"Target: {len(suspects)} TOC-snippet-suspect candidates")

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

        for cik in suspects:
            if cik in already_done:
                continue

            cand_row = candidates[candidates["cik"] == cik]
            event_row = events[events["cik"] == cik]
            if len(cand_row) == 0 or len(event_row) == 0:
                continue
            cand_row = cand_row.iloc[0]
            event_row = event_row.iloc[0]

            accession, url = narrative_mod.pick_representative_filing(cand_row)
            result = {
                "cik": cik, "background_found": False,
                "buyer_name": None, "buyer_name_quote": None,
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

            writer.writerow(result)
            f.flush()
            time.sleep(0.1)

            n_processed += 1
            if n_processed % 25 == 0:
                print(f"  ...processed {n_processed}/{len(suspects) - len(already_done)}", flush=True)

    result = pd.read_csv(OUT_PATH, dtype={"cik": str})
    print(f"Background now genuinely found: {result['background_found'].sum()}/{len(result)}")
    print(f"Newly classified (not abstained): {(~result['abstained']).sum()}/{len(result)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
