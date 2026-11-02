"""
Stage 1 — Collect going-private (SC 13E-3) events from SEC EDGAR.

What this does:
1. Downloads the quarterly EDGAR full-index (form.idx) for 2010-2025 and
   pulls out every row filed as "SC 13E3" or "SC 13E3/A" (the amendment form).
2. For each unique filing (accession number), fetches the filing's own
   "-index.htm" page and reads EDGAR's own Subject/Filed-by labels to work
   out which CIK is the actual target company being taken private, versus
   which CIKs are joint filers (e.g. the PE sponsor, holding entities).
   This matters because the full-index lists one row per filer, not one row
   per target company — without this step a PE sponsor's own CIK would get
   miscounted as a separate "event".
3. Deduplicates by the *subject company's* CIK, per design_memo.md Section 1:
   multiple filings/amendments for the same company's deal count as one event.

Output: data/interim/stage_01_events.csv
    cik, company_name, first_event_date, all_filing_dates,
    all_accession_numbers, filed_by_ciks, filed_by_names

`filed_by_ciks` / `filed_by_names` are extra columns (beyond the original
request) added per the author's decision on 2026-09-17: keep every filer, but
separate the target company from joint filers using EDGAR's own Subject/Filed-by
labels, and carry the joint filers (e.g. PE sponsors) along for later PE
classification instead of dropping them or miscounting them as target companies.
"""

import re
import time
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

from edgar_utils import get_user_agent

# --- Config -------------------------------------------------------------

START_YEAR = 2010
END_YEAR = 2025
FORM_TYPES = {"SC 13E3", "SC 13E3/A"}

# SEC requires a descriptive User-Agent identifying the requester.
USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)

# SEC's fair-access guideline caps automated traffic at 10 req/sec.
# We stay well under that.
REQUEST_DELAY_SECONDS = 0.2

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_INDEX_DIR = REPO_ROOT / "data" / "raw" / "edgar_full_index"
RAW_FILING_DIR = REPO_ROOT / "data" / "raw" / "edgar_filing_index_pages"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_01_events.csv"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


# --- Step 1: download + parse quarterly full-index files ----------------

def download_full_index(year: int, quarter: int) -> Path:
    """Download (or reuse a cached copy of) one quarter's form.idx file."""
    RAW_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    local_path = RAW_INDEX_DIR / f"{year}_QTR{quarter}_form.idx"
    if local_path.exists():
        return local_path

    url = f"https://www.sec.gov/Archives/edgar/full-index/{year}/QTR{quarter}/form.idx"
    resp = SESSION.get(url, timeout=30)
    resp.raise_for_status()
    local_path.write_text(resp.text, encoding="latin-1")
    time.sleep(REQUEST_DELAY_SECONDS)
    return local_path


SC_13E3_LINE_RE = re.compile(
    r"^(SC 13E3(?:/A)?)\s+(.+?)\s+(\d{1,10})\s+(\d{4}-\d{2}-\d{2})\s+(edgar/\S+)\s*$"
)


def parse_form_idx(path: Path) -> list[dict]:
    """
    Extract SC 13E3 / SC 13E3-A rows from a form.idx file via regex rather
    than fixed-width column slicing: the field widths in these files are
    wider than their header labels (e.g. the Date Filed column overflows a
    few characters past where "File Name" starts in the header), so slicing
    by header label position silently truncates values. Anchoring on the
    known form-type prefix and the unambiguous date/filename patterns is
    robust to that padding.
    """
    rows = []
    for line in path.read_text(encoding="latin-1").splitlines():
        m = SC_13E3_LINE_RE.match(line)
        if not m:
            continue
        form_type, company_name, cik, date_filed, file_name = m.groups()
        rows.append(
            {
                "Form Type": form_type,
                "Company Name": company_name.strip(),
                "CIK": cik,
                "Date Filed": date_filed,
                "File Name": file_name,
            }
        )
    return rows


def collect_raw_filing_rows() -> pd.DataFrame:
    """Download all quarters 2010-2025 and filter to SC 13E3 / SC 13E3/A rows."""
    all_rows = []
    for year in range(START_YEAR, END_YEAR + 1):
        for quarter in range(1, 5):
            idx_path = download_full_index(year, quarter)
            for row in parse_form_idx(idx_path):
                if row["Form Type"] in FORM_TYPES:
                    all_rows.append(row)

    df = pd.DataFrame(all_rows)
    df["cik_path"] = df["File Name"].str.extract(r"edgar/data/(\d+)/")[0]
    df["accession"] = df["File Name"].str.extract(r"/(\d{10}-\d{2}-\d{6})\.txt$")[0]
    return df


# --- Step 2: resolve each filing's subject company vs. joint filers -----

CIK_RE = re.compile(r"(\d{10})")


def fetch_filing_index_page(cik_path: str, accession: str) -> str:
    """Download (or reuse a cached copy of) one filing's -index.htm page."""
    RAW_FILING_DIR.mkdir(parents=True, exist_ok=True)
    local_path = RAW_FILING_DIR / f"{accession}.htm"
    if local_path.exists():
        return local_path.read_text(encoding="utf-8")

    accession_nodash = accession.replace("-", "")
    url = (
        f"https://www.sec.gov/Archives/edgar/data/{cik_path}/"
        f"{accession_nodash}/{accession}-index.htm"
    )

    last_error = None
    for attempt in range(3):
        try:
            resp = SESSION.get(url, timeout=30)
            resp.raise_for_status()
            local_path.write_text(resp.text, encoding="utf-8")
            time.sleep(REQUEST_DELAY_SECONDS)
            return resp.text
        except requests.RequestException as e:
            last_error = e
            time.sleep(2 * (attempt + 1))  # backoff for transient 503s/timeouts
    raise last_error


def parse_subject_and_filers(html: str) -> tuple[str | None, str | None, list[tuple[str, str]]]:
    """
    Read EDGAR's own "(Subject)" / "(Filed by)" labels off a filing's index
    page to separate the target company from joint filers (e.g. PE sponsors).

    Returns (subject_cik, subject_name, [(filer_cik, filer_name), ...]).
    filed_by list excludes the subject company itself.
    """
    soup = BeautifulSoup(html, "html.parser")

    subject = None
    filers = []
    for div in soup.find_all("div", class_="filerDiv"):
        span = div.find("span", class_="companyName")
        if not span:
            continue
        # The name and role tag are the span's own leading text node; the
        # nested <acronym>/<a> (CIK info) must not be pulled into it.
        text = span.contents[0].strip() if span.contents else ""
        link = span.find("a")
        cik_match = CIK_RE.search(link.get_text(strip=True)) if link else None
        if not cik_match:
            continue
        cik = cik_match.group(1)

        if "(Subject)" in text:
            name = text.replace("(Subject)", "").strip()
            subject = (cik, name)
        elif "(Filed by)" in text:
            name = text.replace("(Filed by)", "").strip()
            filers.append((cik, name))

    subject_cik, subject_name = subject if subject else (None, None)
    filed_by = [(cik, name) for cik, name in filers if cik != subject_cik]
    return subject_cik, subject_name, filed_by


# --- Step 3: build per-CIK events, deduplicated ----------------------------

def build_events(raw_rows: pd.DataFrame) -> pd.DataFrame:
    unique_filings = (
        raw_rows.drop_duplicates(subset="accession")
        .sort_values("Date Filed")
        .to_dict("records")
    )

    filing_records = []
    for i, row in enumerate(unique_filings, start=1):
        try:
            html = fetch_filing_index_page(row["cik_path"], row["accession"])
            subject_cik, subject_name, filed_by = parse_subject_and_filers(html)
        except requests.RequestException as e:
            print(f"  [warn] skipping {row['accession']}: {e}")
            continue

        if subject_cik is None:
            print(f"  [warn] no Subject Company found for {row['accession']}, skipping")
            continue

        filing_records.append(
            {
                "subject_cik": subject_cik,
                "subject_name": subject_name,
                "filing_date": row["Date Filed"],
                "accession": row["accession"],
                "filed_by_ciks": [c for c, _ in filed_by],
                "filed_by_names": [n for _, n in filed_by],
            }
        )

        if i % 50 == 0:
            print(f"  ...resolved {i}/{len(unique_filings)} filings")

    filings_df = pd.DataFrame(filing_records)

    events = []
    for cik, group in filings_df.groupby("subject_cik"):
        group = group.sort_values("filing_date")
        all_filed_by_ciks = sorted(set(c for ciks in group["filed_by_ciks"] for c in ciks))
        all_filed_by_names = sorted(set(n for names in group["filed_by_names"] for n in names))
        events.append(
            {
                "cik": cik.zfill(10),
                "company_name": group["subject_name"].iloc[0],
                "first_event_date": group["filing_date"].min(),
                "all_filing_dates": ";".join(group["filing_date"]),
                "all_accession_numbers": ";".join(group["accession"]),
                "filed_by_ciks": ";".join(all_filed_by_ciks),
                "filed_by_names": ";".join(all_filed_by_names),
            }
        )

    return pd.DataFrame(events).sort_values("first_event_date")


# --- Main -----------------------------------------------------------------

def main():
    print(f"Downloading EDGAR full-index files for {START_YEAR}-{END_YEAR}...")
    raw_rows = collect_raw_filing_rows()
    print(f"Raw SC 13E3 / SC 13E3-A index rows: {len(raw_rows)}")

    n_unique_accessions = raw_rows["accession"].nunique()
    print(f"Unique accession numbers (filings): {n_unique_accessions}")

    print("Resolving each filing's subject company vs. joint filers...")
    events = build_events(raw_rows)
    print(f"Unique subject companies (deduplicated events): {len(events)}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    events.to_csv(OUT_PATH, index=False)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
