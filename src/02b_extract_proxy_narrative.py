"""
Stage 2b — Extract buyer identification + "Background of the Merger" narrative
text from each Stage 2a proxy candidate's primary document.

This is deliberately heuristic (regex/section-heading based), not an LLM call:
per design_memo.md v1.3 Section 1, actual PE-vs-not classification happens in
Stage 2 using an LLM reading this narrative text as evidence - this stage's
job is just to gather that evidence cheaply for ~thousands of candidates, most
of which are ordinary strategic M&A and will be excluded at classification
time. The buyer/parent/merger-sub names extracted here are best-effort
signals to help that later classification, not authoritative themselves.

Heuristics were built and validated against two real filings before writing
this script (AirTran/Southwest 2011 - a two-named-party "cover letter" style;
Panera/JAB 2017 - a "Parent"/"Merger Sub" defined-term style), since merger
proxies don't have a structured header field for buyer identity the way
SC 13E-3's Subject/Filed-by split does.

Output:
- data/interim/stage_02b_proxy_narrative.csv: cik, buyer_raw, parent_entity,
  merger_sub_entity, background_section_found (bool), narrative_text_path
- data/interim/proxy_narrative_text/{cik}.txt: the extracted narrative text
  (kept out of the CSV - some "Background of the Merger" sections run to
  several thousand words)
"""

import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
CANDIDATES_PATH = REPO_ROOT / "data" / "interim" / "stage_02a_proxy_candidates.csv"
RAW_DOC_DIR = REPO_ROOT / "data" / "raw" / "edgar_proxy_documents"
NARRATIVE_DIR = REPO_ROOT / "data" / "interim" / "proxy_narrative_text"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_02b_proxy_narrative.csv"

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.2
NARRATIVE_MAX_CHARS = 20_000
HEAD_SEARCH_CHARS = 8_000  # buyer/parent identification only needs the cover letter/summary, near the top

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

# --- Buyer / Parent / Merger Sub identification heuristics ----------------

STOPWORD_STARTS = {
    "a", "an", "the", "which", "incorporated", "organized", "wholly",
    "owned", "subsidiary", "under", "pursuant", "together", "collectively",
    "have", "has",
}
SUFFIX_LOOKAHEAD = (
    r"(?!\s*(?:Inc\.|Incorporated|Corp\.|Corporation|LLC|L\.L\.C\.|Co\.|"
    r"Ltd\.|L\.P\.|LP|N\.V\.|B\.V\.|LLP|plc))"
)
LEADING_CONJ_RE = re.compile(r"^(?:and|or)\s+", re.I)
LEADING_ALLCAPS_RUN_RE = re.compile(r"^(?:[A-Z0-9]{2,}[\s—\-,]+)+")


def nearest_proper_noun_before(text: str, anchor_start: int, lookback: int = 250) -> str | None:
    """
    Walk backward from an anchor phrase (e.g. "which we refer to as Parent")
    to find the entity name describing it, skipping stopwords, boilerplate
    ALL-CAPS headline fragments, and corporate-suffix false splits
    (treats "X, Inc." as one segment, not two).
    """
    window = text[max(0, anchor_start - lookback):anchor_start]
    segments = [s.strip() for s in re.split(r",\s*" + SUFFIX_LOOKAHEAD, window) if s.strip()]
    for seg in reversed(segments):
        seg = LEADING_CONJ_RE.sub("", seg)
        seg = LEADING_ALLCAPS_RUN_RE.sub("", seg).strip()
        if not seg:
            continue
        first_word = seg.split()[0].lower()
        if seg[:1].isupper() and first_word not in STOPWORD_STARTS:
            return seg
    return None


def extract_buyer_raw(head_text: str) -> str | None:
    """Best-effort buyer identification from the cover letter / opening summary."""
    text = re.sub(r"\s+", " ", head_text)
    toc_matches = list(re.finditer(r"Table of Contents", text, re.I))
    if toc_matches:
        text = text[toc_matches[-1].end():]

    m = re.search(r"have entered into an? Agreement and Plan of Merger", text, re.I)
    if m:
        name = nearest_proper_noun_before(text, m.start())
        if name:
            return name

    m = re.search(
        r"entered into (?:a|an) (?:Agreement and Plan of Merger|merger agreement)"
        r"(?:,? dated[^,]*,)?\s*with\s+([A-Z][\w&,\.' ]+?)(?:,|\. | a )",
        text, re.I,
    )
    if m:
        return m.group(1).strip()
    return None


def extract_parent_and_merger_sub(head_text: str) -> dict:
    text = re.sub(r"\s+", " ", head_text)
    result = {}
    for role, pat in [
        ("parent_entity", r"which we refer to as[,]?\s*[“\"]?Parent"),
        ("merger_sub_entity", r"which we refer to as[,]?\s*[“\"]?Merger Sub"),
    ]:
        m = re.search(pat, text, re.I)
        if m:
            name = nearest_proper_noun_before(text, m.start())
            if name:
                result[role] = name
    return result


# --- "Background of the Merger" section extraction -------------------------

BACKGROUND_HEADING_PATTERNS = [
    # "s?" for the plural "Transactions" phrasing some filers use (e.g.
    # multi-step going-private structures) - found via Golden Entertainment,
    # where the singular-only pattern matched mid-word, leaving a stray "s"
    # right after the match that silently broke the TOC-vs-body detection
    # below (its regex expects clean whitespace immediately after the match).
    r"background of the mergers?",
    r"background of the transactions?",
    r"background and reasons for the mergers?",
]
NEXT_SECTION_PATTERNS = [
    r"reasons for the merger",
    r"recommendation of the board",
    r"opinion of",
    r"interests of",
    r"certain effects of the merger",
    r"financing of the merger",
]


def find_real_section_start(text: str, patterns: list[str], window: int = 100) -> int | None:
    """
    Find a section heading's actual body (not its table-of-contents entry or
    an inline cross-reference like `See "Background of the Merger"`).
    A real heading is not immediately preceded by a quote mark and not
    immediately followed by "beginning on page" or a bare page number.
    """
    candidates = []
    for pat in patterns:
        candidates.extend(re.finditer(pat, text, re.I))
    candidates.sort(key=lambda m: m.start())

    for m in candidates:
        after = text[m.end():m.end() + window]
        before = text[max(0, m.start() - 5):m.start()]
        is_toc_or_xref = bool(
            re.search(r"beginning on page", after, re.I)
            or re.search(r'["“”]\s*$', before)
            or re.search(r"^[\s\xa0]*\d{1,4}[\s\xa0]*\n", after)
        )
        if not is_toc_or_xref:
            return m.start()
    return None


def extract_background_section(full_text: str) -> str | None:
    start = find_real_section_start(full_text, BACKGROUND_HEADING_PATTERNS)
    if start is None:
        return None

    end = start + NARRATIVE_MAX_CHARS
    next_heading_pos = find_real_section_start(
        full_text[start + 200:start + NARRATIVE_MAX_CHARS], NEXT_SECTION_PATTERNS
    )
    if next_heading_pos is not None:
        end = start + 200 + next_heading_pos

    return full_text[start:end].strip()


# --- Fetching ---------------------------------------------------------------

def fetch_document_text(url: str, accession: str) -> str | None:
    """
    Fetch (or reuse a cached copy of) a document, extract its text, then
    delete the raw file immediately - these documents run 1-5MB+ each and
    there's no reason to keep ~3,700 of them once their text is extracted.
    Disk usage this way stays bounded to ~1 document at a time instead of
    accumulating to several GB (this pipeline filled a disk once already).
    """
    RAW_DOC_DIR.mkdir(parents=True, exist_ok=True)
    local_path = RAW_DOC_DIR / f"{accession}.htm"

    if local_path.exists():
        html = local_path.read_text(encoding="utf-8", errors="ignore")
    else:
        try:
            resp = get_with_hard_timeout(SESSION, url, hard_timeout_seconds=60, timeout=(10, 30))
            resp.raise_for_status()
        except (requests.RequestException, HardTimeoutError) as e:
            print(f"  [warn] failed to fetch {accession}: {e}", flush=True)
            return None
        html = resp.text
        local_path.write_text(html, encoding="utf-8")
        time.sleep(REQUEST_DELAY_SECONDS)

    text = BeautifulSoup(html, "html.parser").get_text("\n")
    local_path.unlink(missing_ok=True)
    return text


def pick_representative_filing(row: pd.Series) -> tuple[str, str] | tuple[None, None]:
    """Prefer the definitive filing (DEFM14A/DEFM14C) over a preliminary one."""
    if pd.isna(row["all_primary_doc_urls"]):
        # pandas reads Stage 2a's empty-string field (URL resolution failed
        # for every filing of this candidate) back as NaN, not "".
        return None, None

    accessions = row["all_accession_numbers"].split(";")
    forms = row["all_form_types"].split(";")
    urls = row["all_primary_doc_urls"].split(";")

    for wanted in ("DEFM14A", "DEFM14C", "PREM14A", "PREM14C"):
        for form, url in zip(forms, urls):
            if form == wanted and url:
                accession = accessions[forms.index(form)]
                return accession, url
    return None, None


FIELDNAMES = [
    "cik", "buyer_raw", "parent_entity", "merger_sub_entity",
    "background_section_found", "narrative_text_path",
]


def load_already_processed() -> set[str]:
    """Resume support: a CIK already in the checkpoint CSV is not redone."""
    if not OUT_PATH.exists():
        return set()
    done = pd.read_csv(OUT_PATH, dtype={"cik": str})
    return set(done["cik"])


def main():
    import csv

    candidates = pd.read_csv(CANDIDATES_PATH, dtype={"cik": str})
    NARRATIVE_DIR.mkdir(parents=True, exist_ok=True)
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    already_done = load_already_processed()
    if already_done:
        print(f"Resuming: {len(already_done)} candidates already checkpointed, skipping those.", flush=True)

    write_header = not OUT_PATH.exists()
    n_processed = 0
    with open(OUT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if write_header:
            writer.writeheader()
            f.flush()

        for i, row in candidates.iterrows():
            if row["cik"] in already_done:
                continue

            accession, url = pick_representative_filing(row)
            result = {
                "cik": row["cik"],
                "buyer_raw": None,
                "parent_entity": None,
                "merger_sub_entity": None,
                "background_section_found": False,
                "narrative_text_path": None,
            }

            if url:
                text = fetch_document_text(url, accession)
                if text:
                    head = text[:HEAD_SEARCH_CHARS]
                    result["buyer_raw"] = extract_buyer_raw(head)
                    result.update(extract_parent_and_merger_sub(head))

                    background = extract_background_section(text)
                    if background:
                        result["background_section_found"] = True
                        narrative_path = NARRATIVE_DIR / f"{row['cik']}.txt"
                        narrative_path.write_text(background, encoding="utf-8")
                        result["narrative_text_path"] = str(
                            narrative_path.relative_to(REPO_ROOT)
                        )

            # Written and flushed immediately - a kill/crash mid-run loses at
            # most the one candidate in flight, not the whole run (this
            # pipeline has already been killed once for a disk-space issue).
            writer.writerow(result)
            f.flush()

            n_processed += 1
            if n_processed % 50 == 0:
                print(f"  ...processed {n_processed} new candidates (of {len(candidates) - len(already_done)} remaining)", flush=True)

    out = pd.read_csv(OUT_PATH, dtype={"cik": str})
    print(f"Buyer identified: {out['buyer_raw'].notna().sum()}/{len(out)}")
    print(f"Background section found: {out['background_section_found'].sum()}/{len(out)}")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
