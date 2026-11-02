"""
Stage 7 — Re-run buyer/PE classification for "both sources" events that
previously abstained, using Stage 5's now-widened evidence window (full
20,000-char narrative instead of a 6,000-char truncation).

Motivating bug (found while checking Dell by name after Stage 6): Dell has
both an SC 13E-3 and a DEFM14A, and Stage 2b's narrative extraction for its
DEFM14A clearly names Michael Dell and Silver Lake Partners - but Stage 5's
old 6,000-char evidence window cut off while the "Background of the Merger"
narrative was still mid-negotiation (Special Committee just formed, no
confirmed buyer yet), so the model correctly-but-overcautiously abstained.
Fixed in Stage 5 (build_evidence_text now sends the full ~20,000-char
narrative Stage 2b already extracts, no truncation).

Scope: only "both sources" events (sc13e3_present AND proxy_present) that
abstained under the old window - not a full re-run of all 4,085. Events
that already classified successfully are left as-is; SC-13E-3-only events
were already handled by Stage 6 and are out of scope here.

Output: data/interim/stage_07_widened_reclassification.csv
    cik, buyer_name, buyer_name_quote, buyer_type, buyer_type_quote, abstained
"""

import importlib
import time
from pathlib import Path

import pandas as pd

classify_mod = importlib.import_module("05_buyer_classification")

REPO_ROOT = Path(__file__).resolve().parent.parent
COMBINED_EVENTS_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_07_widened_reclassification.csv"

FIELDNAMES = ["cik", "buyer_name", "buyer_name_quote", "buyer_type", "buyer_type_quote", "abstained"]


def load_already_processed() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    return set(pd.read_csv(OUT_PATH, dtype={"cik": str})["cik"])


def main():
    import csv
    import os

    classify_mod.load_dotenv_if_present()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set - add it to .env before running this script.")
    client = __import__("anthropic").Anthropic()

    events = pd.read_csv(COMBINED_EVENTS_PATH, dtype={"cik": str})
    target = events[
        (events["sc13e3_present"] == True)  # noqa: E712
        & (events["proxy_present"] == True)  # noqa: E712
        & (events["abstained"] == True)  # noqa: E712
    ]
    print(f"Target events (both sources, previously abstained): {len(target)}")

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

        for _, row in target.iterrows():
            if row["cik"] in already_done:
                continue

            evidence_text = classify_mod.build_evidence_text(row)
            try:
                result = classify_mod.classify(client, evidence_text)
            except Exception as e:
                print(f"  [warn] classification failed for CIK {row['cik']}: {e}", flush=True)
                result = {"abstain": True, "buyer_name": None, "buyer_name_quote": None, "buyer_type": None, "buyer_type_quote": None}

            writer.writerow({
                "cik": row["cik"],
                "buyer_name": result.get("buyer_name"),
                "buyer_name_quote": result.get("buyer_name_quote"),
                "buyer_type": result.get("buyer_type"),
                "buyer_type_quote": result.get("buyer_type_quote"),
                "abstained": result.get("abstain", True),
            })
            f.flush()
            time.sleep(0.1)

            n_processed += 1
            if n_processed % 25 == 0:
                print(f"  ...processed {n_processed} events", flush=True)

    result = pd.read_csv(OUT_PATH, dtype={"cik": str})
    print(f"Re-abstained: {result['abstained'].sum()}/{len(result)}")
    print(f"Newly classified: {(~result['abstained']).sum()}/{len(result)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
