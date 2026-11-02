"""
Stage 6 — Re-run buyer/PE classification for SC-13E-3-only events that
previously abstained, using evidence extracted from the actual SC 13E-3
document itself (not just filer/subject metadata).

Why: Stage 2b only extracted narrative text from proxy-sourced candidates,
never from SC-13E-3-only events' own filing text - Stage 5 then had to
classify those 419 events using little more than the company name, and
95% (400/419) abstained (vs. 76% for proxy-sourced events). This targets
just that gap.

New evidence source: SC 13E-3's standardized cover page has a "(Name of
Person(s) Filing Statement)" field listing every joint filer by name,
directly above/below a "(Name of Issuer)" field - confirmed on two real
filings (MAXXAM 2010, Regan Holding 2010) before writing this script. This
is more reliable than Stage 2b's free-form-prose buyer regex, since it's a
standardized form field, not prose that varies by filer. Also attempts the
same "Background of the Merger"-style section extraction Stage 2b uses, in
case the specific SC 13E-3 document contains substantive narrative beyond
the cover page (reused via import, not duplicated).

Scope: only the 400 events that are (a) SC-13E-3-only and (b) abstained in
Stage 5's first pass - not a full re-run of all 419 (the 19 that already
classified successfully are left as-is), and not proxy-sourced events
(out of scope for this stage; Stage 2b already covers those).

Output: data/interim/stage_06_sc13e3_reclassification.csv
    cik, filing_persons_block, background_found, buyer_name, buyer_name_quote,
    buyer_type, buyer_type_quote, abstained
"""

import importlib
import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

# Reuse Stage 2b's extraction heuristics and Stage 5's classifier rather
# than duplicating them - both modules have digit-prefixed filenames, so a
# plain `import` statement isn't usable; importlib works fine at runtime.
narrative_mod = importlib.import_module("02b_extract_proxy_narrative")
classify_mod = importlib.import_module("05_buyer_classification")

REPO_ROOT = Path(__file__).resolve().parent.parent
COMBINED_EVENTS_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"
RAW_DOC_DIR = REPO_ROOT / "data" / "raw" / "sc13e3_documents"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_06_sc13e3_reclassification.csv"

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.2

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

FORM_TYPES = {"SC 13E3", "SC 13E3/A"}

# Broadened after Golden Entertainment's 2025 SC 13E-3 used "(Name of
# Subject Company (Issuer))" / "(Names of Filing Persons)" - phrasing
# variants the original patterns (validated only against MAXXAM/Regan
# Holding) didn't cover. Regression-tested against both original cases
# before widening.
ISSUER_LABEL_RE = re.compile(r"\(Name of[^\n]*?Issuer\)", re.I)
FILING_PERSONS_LABEL_RE = re.compile(r"\((?:Name|Names) of (?:Person\(?s?\)? Filing Statement|Filing Persons)\)", re.I)

FIELDNAMES = [
    "cik", "filing_persons_block", "background_found",
    "buyer_name", "buyer_name_quote", "buyer_type", "buyer_type_quote", "abstained",
]


def pick_representative_accession(row: pd.Series) -> str:
    """Earliest SC 13E-3 accession - the original filing most likely to
    contain the fullest initial disclosure (amendments often only update
    specific items)."""
    return row["sc13e3_accession_numbers"].split(";")[0]


def fetch_primary_doc_url(cik: str, accession: str) -> str | None:
    accession_nodash = accession.replace("-", "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/"
        f"{accession_nodash}/{accession}-index.htm"
    )
    try:
        resp = get_with_hard_timeout(SESSION, url)
        resp.raise_for_status()
    except (requests.RequestException, HardTimeoutError) as e:
        print(f"  [warn] index page fetch failed for {accession}: {e}", flush=True)
        return None
    time.sleep(REQUEST_DELAY_SECONDS)

    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="tableFile")
    if not table:
        return None
    for row in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in row.find_all("td")]
        if len(cells) >= 4 and cells[3] in FORM_TYPES:
            link = row.find("a")
            if link and link.get("href"):
                return "https://www.sec.gov" + link["href"]
    return None


def fetch_document_text(url: str, accession: str) -> str | None:
    RAW_DOC_DIR.mkdir(parents=True, exist_ok=True)
    local_path = RAW_DOC_DIR / f"{accession}.htm"

    if local_path.exists():
        html = local_path.read_text(encoding="utf-8", errors="ignore")
    else:
        try:
            resp = get_with_hard_timeout(SESSION, url, hard_timeout_seconds=60, timeout=(10, 30))
            resp.raise_for_status()
        except (requests.RequestException, HardTimeoutError) as e:
            print(f"  [warn] document fetch failed for {accession}: {e}", flush=True)
            return None
        html = resp.text
        local_path.write_text(html, encoding="utf-8")
        time.sleep(REQUEST_DELAY_SECONDS)

    text = BeautifulSoup(html, "html.parser").get_text("\n")
    local_path.unlink(missing_ok=True)  # same disk-safety lesson as Stage 2b
    return text


def extract_filing_persons(text: str) -> str | None:
    issuer_m = ISSUER_LABEL_RE.search(text)
    filers_m = FILING_PERSONS_LABEL_RE.search(text)
    if not (issuer_m and filers_m and issuer_m.end() < filers_m.start()):
        return None
    block = text[issuer_m.end():filers_m.start()].strip()
    return re.sub(r"\s+", " ", block) or None


def build_evidence_text(row: pd.Series, filing_persons_block: str | None, background_text: str | None) -> str:
    parts = [f"Target company: {row.get('company_name', 'unknown')}"]
    if filing_persons_block:
        parts.append(f"SC 13E-3 'Name of Person(s) Filing Statement' (all joint filers): {filing_persons_block}")
    if pd.notna(row.get("filed_by_names")):
        parts.append(f"Joint filers (from EDGAR filer metadata): {row['filed_by_names']}")
    if background_text:
        parts.append(f"Narrative text from the SC 13E-3 document (excerpt):\n{background_text[:6000]}")
    return "\n\n".join(parts)


def load_already_processed() -> set[str]:
    if not OUT_PATH.exists():
        return set()
    return set(pd.read_csv(OUT_PATH, dtype={"cik": str})["cik"])


def main():
    import csv

    classify_mod.load_dotenv_if_present()
    import os
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY not set - add it to .env before running this script.")
    client = __import__("anthropic").Anthropic()

    events = pd.read_csv(COMBINED_EVENTS_PATH, dtype={"cik": str})
    target = events[
        (events["sc13e3_present"] == True)  # noqa: E712
        & (events["proxy_present"] == False)  # noqa: E712
        & (events["abstained"] == True)  # noqa: E712
    ]
    print(f"Target events (SC-13E-3-only, previously abstained): {len(target)}")

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

            accession = pick_representative_accession(row)
            doc_url = fetch_primary_doc_url(row["cik"], accession)

            filing_persons_block = None
            background_text = None
            if doc_url:
                text = fetch_document_text(doc_url, accession)
                if text:
                    filing_persons_block = extract_filing_persons(text)
                    background_text = narrative_mod.extract_background_section(text)

            evidence_text = build_evidence_text(row, filing_persons_block, background_text)
            try:
                result = classify_mod.classify(client, evidence_text)
            except Exception as e:
                print(f"  [warn] classification failed for CIK {row['cik']}: {e}", flush=True)
                result = {"abstain": True, "buyer_name": None, "buyer_name_quote": None, "buyer_type": None, "buyer_type_quote": None}

            writer.writerow({
                "cik": row["cik"],
                "filing_persons_block": filing_persons_block,
                "background_found": background_text is not None,
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
