"""
Stage 5 — Buyer identification / PE classification via a single LLM pass,
per design_memo.md v1.4 Section 1a.

Requires ANTHROPIC_API_KEY in the environment (see README/setup) - this
script calls the Anthropic API directly (not run "as" Claude Code), so it
needs its own credential the same as any other script using the `anthropic`
package already listed in requirements.txt.

For each event, feeds the LLM whatever evidence text is available:
- The Stage 2b-extracted "Background of the Merger" narrative (proxy-sourced
  events only - stage_02b never ran against SC-13E-3-only events' own filing
  text, so those 419 events rely on the fields below instead)
- buyer_raw / parent_entity / merger_sub_entity (Stage 2b's regex-based
  best-effort extraction, ~8% hit rate - weak signal, included anyway since
  the LLM can weigh it)
- filed_by_names (Stage 1's Subject/Filed-by split - for SC-13E-3 events,
  the joint-filer names, e.g. a PE sponsor's own entity name, are often
  themselves strong evidence)

The model must return `abstain` rather than guess when there's no clear
textual support - this is the sole confidence mechanism (replacing an earlier
3-tier confidence-routing proposal per the v1.4 scope decision). Abstained cases are
excluded from the main PE-backed sample, not routed to manual review.

Output: data/interim/stage_05_buyer_classification.csv
    cik, buyer_name, buyer_name_quote, buyer_type, buyer_type_quote, abstained
"""

import json
import os
import time
from pathlib import Path

import anthropic
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
COMBINED_EVENTS_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_05_buyer_classification.csv"

# Bulk-classification task with a bounded, well-specified output schema -
# not a task that needs frontier reasoning. Model is a constant here so it's
# a one-line change if a different tradeoff is wanted.
MODEL = "claude-haiku-4-5-20251001"
REQUEST_DELAY_SECONDS = 0.1

FIELDNAMES = [
    "cik", "buyer_name", "buyer_name_quote", "buyer_type", "buyer_type_quote", "abstained",
]

CLASSIFICATION_TOOL = {
    "name": "classify_buyer",
    "description": "Report the buyer/acquirer identity and type for a going-private transaction based only on the provided evidence text.",
    "input_schema": {
        "type": "object",
        "properties": {
            "abstain": {
                "type": "boolean",
                "description": "True if the evidence text does not clearly support identifying a buyer and its type. When true, all other fields must be null.",
            },
            "buyer_name": {
                "type": ["string", "null"],
                "description": "The name of the acquiring entity (the PE sponsor, strategic acquirer, or individual/family taking the company private), or null if abstaining.",
            },
            "buyer_name_quote": {
                "type": ["string", "null"],
                "description": "A literal quote from the evidence text that supports buyer_name. Must be an exact substring of the evidence text. Null if buyer_name is null.",
            },
            "buyer_type": {
                "type": ["string", "null"],
                "enum": ["pe_sponsor", "strategic", "family", "management", "unknown", None],
                "description": "pe_sponsor: a private equity/financial sponsor. strategic: another operating company acquiring for strategic reasons. family: a controlling family/individual shareholder. management: an MBO led by existing management with no outside sponsor. unknown: buyer identified but type unclear from the text. Null if abstaining.",
            },
            "buyer_type_quote": {
                "type": ["string", "null"],
                "description": "A literal quote from the evidence text that supports buyer_type. Must be an exact substring of the evidence text. Null if buyer_type is null or unknown.",
            },
        },
        "required": ["abstain", "buyer_name", "buyer_name_quote", "buyer_type", "buyer_type_quote"],
    },
}

SYSTEM_PROMPT = """You are classifying the buyer/acquirer of a company in a going-private transaction, using only the evidence text provided. You must call the classify_buyer tool exactly once.

Rules:
- Only use information present in the evidence text. Do not use outside knowledge about the company or deal.
- Every non-null field (buyer_name, buyer_type) must be backed by a literal quote from the evidence text in the corresponding _quote field. The quote must be an exact substring of the evidence text - do not paraphrase.
- If the evidence text is too sparse, generic, or ambiguous to confidently identify a buyer and its type, set abstain=true and leave all other fields null. Abstaining is the correct, preferred choice when unsure - do not guess."""


def build_evidence_text(row: pd.Series) -> str:
    parts = [f"Target company: {row.get('company_name', 'unknown')}"]

    for label, col in [
        ("Buyer (regex-extracted, low confidence)", "buyer_raw"),
        ("Parent entity (regex-extracted)", "parent_entity"),
        ("Merger Sub entity (regex-extracted)", "merger_sub_entity"),
        ("Joint filers on SC 13E-3 (may include the PE sponsor's own entity)", "filed_by_names"),
    ]:
        val = row.get(col)
        if pd.notna(val) and str(val).strip():
            parts.append(f"{label}: {val}")

    narrative_path = row.get("narrative_text_path")
    if pd.notna(narrative_path):
        full_path = REPO_ROOT / narrative_path
        if full_path.exists():
            # Send the whole thing - Stage 2b already caps extraction at
            # 20,000 chars, so there's no reason to truncate further here.
            # A 6,000-char cutoff was previously found to cause false
            # abstentions on long negotiation narratives (Dell: Michael
            # Dell/Silver Lake were named in the visible text, but the
            # narrative hadn't yet named a confirmed buyer by char 6,000,
            # so the model correctly-but-overcautiously abstained).
            text = full_path.read_text(encoding="utf-8")
            parts.append(f"Background of the Merger:\n{text}")

    return "\n\n".join(parts)


def classify(client: anthropic.Anthropic, evidence_text: str) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[CLASSIFICATION_TOOL],
        tool_choice={"type": "tool", "name": "classify_buyer"},
        messages=[{"role": "user", "content": f"Evidence text:\n\n{evidence_text}"}],
    )
    for block in response.content:
        if block.type == "tool_use":
            return block.input
    raise RuntimeError("Model did not return a tool_use block")


def load_already_processed() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    return set(pd.read_csv(OUT_PATH, dtype={"cik": str})["cik"])


def load_dotenv_if_present() -> None:
    """Minimal .env loader (no python-dotenv dependency for one variable)."""
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    import csv

    load_dotenv_if_present()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Add it to a .env file in the repo "
            "root (ANTHROPIC_API_KEY=sk-...) or export it before running this script."
        )

    client = anthropic.Anthropic()
    events = pd.read_csv(COMBINED_EVENTS_PATH, dtype={"cik": str})

    already_done = load_already_processed()
    if already_done:
        print(f"Resuming: {len(already_done)} events already checkpointed.", flush=True)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    write_header = not OUT_PATH.exists()
    n_processed = 0

    with open(OUT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
            f.flush()

        for _, row in events.iterrows():
            if row["cik"] in already_done:
                continue

            evidence_text = build_evidence_text(row)
            try:
                result = classify(client, evidence_text)
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
            time.sleep(REQUEST_DELAY_SECONDS)

            n_processed += 1
            if n_processed % 50 == 0:
                print(f"  ...classified {n_processed} events", flush=True)

    result = pd.read_csv(OUT_PATH, dtype={"cik": str})
    print(f"Abstained: {result['abstained'].sum()}/{len(result)}")
    print(result["buyer_type"].value_counts().to_string())
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
