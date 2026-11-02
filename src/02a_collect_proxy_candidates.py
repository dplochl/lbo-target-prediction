"""
Stage 2a — Collect merger/information-statement candidates (second event source).

Per design_memo.md v1.3 Section 1: SC 13E-3 (Stage 1) only captures affiliate-
involved going-private deals. This scans the same EDGAR full-index files for
DEFM14A / PREM14A / DEFM14C / PREM14C filings, 2010-2025 - the merger proxy /
information-statement filings used for third-party (non-affiliate) buyouts too.

Note: unlike SC 13E-3, these forms have no "/A" amendment variant in EDGAR's
own form-type taxonomy (revisions are re-filed as PREM14A/PRER14A or as
DEFA14A "additional soliciting material", not "DEFM14A/A") - confirmed by
scanning all cached full-index files before writing this script.

Unlike SC 13E-3, a merger proxy is self-filed by the registrant alone (no
joint "Subject Company" / "Filed by" split - confirmed by inspecting a real
filing's index page), so the target CIK is just the CIK column from the
full-index row directly. No index-page fetch is needed to resolve target
identity, which keeps this stage cheap; the (much heavier) primary-document
fetch for buyer identification and narrative-section extraction is Stage 2b.

Output: data/interim/stage_02a_proxy_candidates.csv
    cik, company_name, first_filing_date, all_filing_dates,
    all_accession_numbers, all_form_types, all_primary_doc_urls
"""

import re
import time
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

START_YEAR = 2010
END_YEAR = 2025
FORM_TYPES = {"DEFM14A", "PREM14A", "DEFM14C", "PREM14C"}

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.2

REPO_ROOT = Path(__file__).resolve().parent.parent
RAW_INDEX_DIR = REPO_ROOT / "data" / "raw" / "edgar_full_index"
RAW_FILING_DIR = REPO_ROOT / "data" / "raw" / "edgar_proxy_index_pages"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_02a_proxy_candidates.csv"

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})

FORM_LINE_RE = re.compile(
    r"^(DEFM14A|PREM14A|DEFM14C|PREM14C)\s+(.+?)\s+(\d{1,10})\s+(\d{4}-\d{2}-\d{2})\s+(edgar/\S+)\s*$"
)


def download_full_index(year: int, quarter: int) -> Path:
    """Reuse the full-index files already cached by Stage 1; download if missing."""
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


def parse_proxy_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="latin-1").splitlines():
        m = FORM_LINE_RE.match(line)
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


def collect_raw_rows() -> pd.DataFrame:
    all_rows = []
    for year in range(START_YEAR, END_YEAR + 1):
        for quarter in range(1, 5):
            idx_path = download_full_index(year, quarter)
            all_rows.extend(parse_proxy_rows(idx_path))
    df = pd.DataFrame(all_rows)
    df["cik_path"] = df["File Name"].str.extract(r"edgar/data/(\d+)/")[0]
    df["accession"] = df["File Name"].str.extract(r"/(\d{10}-\d{2}-\d{6})\.txt$")[0]
    return df


def fetch_primary_doc_url(cik_path: str, accession: str) -> str | None:
    """
    Look up the primary document's URL from the filing's lightweight index
    page (cached), needed later by Stage 2b for narrative-text extraction.
    Returns None if the lookup fails (rare; Stage 2b will just skip those).
    """
    RAW_FILING_DIR.mkdir(parents=True, exist_ok=True)
    local_path = RAW_FILING_DIR / f"{accession}.htm"
    if local_path.exists():
        html = local_path.read_text(encoding="utf-8")
    else:
        accession_nodash = accession.replace("-", "")
        url = (
            f"https://www.sec.gov/Archives/edgar/data/{cik_path}/"
            f"{accession_nodash}/{accession}-index.htm"
        )
        try:
            resp = get_with_hard_timeout(SESSION, url)
            resp.raise_for_status()
        except (requests.RequestException, HardTimeoutError) as e:
            print(f"  [warn] failed to fetch {accession}: {e}", flush=True)
            return None
        html = resp.text
        local_path.write_text(html, encoding="utf-8")
        time.sleep(REQUEST_DELAY_SECONDS)

    # The primary document is the first row of the documents table whose
    # "Type" column matches one of our form types (avoids picking up
    # exhibits/graphics also listed in the same table).
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
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


def build_candidates(raw_rows: pd.DataFrame) -> pd.DataFrame:
    unique_filings = (
        raw_rows.drop_duplicates(subset="accession")
        .sort_values("Date Filed")
        .to_dict("records")
    )

    for i, row in enumerate(unique_filings, start=1):
        row["primary_doc_url"] = fetch_primary_doc_url(row["cik_path"], row["accession"])
        if i % 50 == 0:
            print(f"  ...resolved primary doc for {i}/{len(unique_filings)} filings", flush=True)

    filings_df = pd.DataFrame(unique_filings)

    events = []
    for cik, group in filings_df.groupby("CIK"):
        group = group.sort_values("Date Filed")
        events.append(
            {
                "cik": cik.zfill(10),
                "company_name": group["Company Name"].iloc[0],
                "first_filing_date": group["Date Filed"].min(),
                "all_filing_dates": ";".join(group["Date Filed"]),
                "all_accession_numbers": ";".join(group["accession"]),
                "all_form_types": ";".join(group["Form Type"]),
                "all_primary_doc_urls": ";".join(
                    "" if pd.isna(url) else url for url in group["primary_doc_url"]
                ),
            }
        )

    return pd.DataFrame(events).sort_values("first_filing_date")


def main():
    print(f"Downloading/reusing EDGAR full-index files for {START_YEAR}-{END_YEAR}...")
    raw_rows = collect_raw_rows()
    print(f"Raw DEFM14A/PREM14A/DEFM14C/PREM14C index rows: {len(raw_rows)}")
    print(f"Unique accession numbers (filings): {raw_rows['accession'].nunique()}")

    print("Resolving primary document URL for each filing...")
    candidates = build_candidates(raw_rows)
    print(f"Unique candidate target companies: {len(candidates)}")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    candidates.to_csv(OUT_PATH, index=False)
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
