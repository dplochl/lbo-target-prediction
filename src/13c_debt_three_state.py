"""
Stage 13c — Total debt with the three-state missing-data rule (design decision 2026-09-21).

Why: a plain "no debt tag => 0" fill was verified against actual balance sheets of the
sponsor_like_ptp firms and gave false zeros for ~22% of the zero-filled firms - debt sat
only under a dimension member (InnerWorkings, Kennedy-Wilson: credit-facility / debt-type
axis, no non-dimensional total) or under a filer-custom tag (Kidpik), or under a standard
tag missing from the list (Silver Bay: LineOfCreditFacilityAmountOutstanding).

States per firm-year (accession):
  1. 'value'         non-dimensional standard debt tags give total debt > 0 -> use it.
  2. 'no_debt_tag'   no debt-like fact of any kind -> debt = 0, no_debt_tag = 1.
     'explicit_zero' debt tags present but all <= 0 (e.g. "Revolving line of credit: 0")
                     -> debt = 0, no_debt_tag = 0 (a reported zero, not an absence).
  3. 'uncertain'     no positive non-dimensional value, but a debt-like fact > 0 exists only
                     under a dimension member or a custom tag -> debt missing,
                     debt_uncertain = 1.

Definition of debt: traditional interest-bearing debt (bank/line-of-credit borrowings, notes,
bonds, convertibles, current + non-current), consistent with the leverage definitions in
Jiang (2019) / Bos et al. (2024). Finance/capital leases and operating leases are NOT debt
(known, documented limitation: e.g. Mindbody's $14.9M finance-lease obligation counts as
zero). Combined tags that bundle debt with capital leases (LongTermDebtAndCapitalLease...) are
part of the normal waterfall (most firms report their bulk long-term debt under them; the
lease component is usually small) and flagged `debt_includes_leases` when used. An earlier
version used them only when nothing else was found, which understated debt by ~99% for
firms with a small separate short-term tag (Heinz FY2012).

Value construction is a priority waterfall to avoid double counting, and is approximate:
  current   = DebtCurrent, else the sum of the short-term components;
  non-current = LongTermDebtNoncurrent, else LongTermDebt (which already includes its
              current portion, so the current part is then only the short-term components),
              else the sum of the alternative long-term instrument tags.

Input : data/interim/stage_13a_num_candidates.csv (non-dimensional standard-tag facts)
        + a fresh num.txt pass collecting dimensional / custom debt-like facts.
Output: data/interim/stage_13c_debt_dim_custom.csv  (adsh, tag, is_dim, is_custom; checkpointed)
        data/interim/stage_13c_debt_states.csv      (accession, total_debt, debt_state,
                                                      no_debt_tag, debt_uncertain, debt_includes_leases)
"""

import io
import re
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_PATH = REPO_ROOT / "data" / "processed" / "firm_year_panel.csv"
RAW_QUARTERS_PATH = REPO_ROOT / "data" / "interim" / "stage_11_raw_quarters.csv"
CAND_PATH = REPO_ROOT / "data" / "interim" / "stage_13a_num_candidates.csv"
DIM_PATH = REPO_ROOT / "data" / "interim" / "stage_13c_debt_dim_custom.csv"
DONE_PATH = REPO_ROOT / "data" / "interim" / "stage_13c_quarters_done.txt"
STATES_PATH = REPO_ROOT / "data" / "interim" / "stage_13c_debt_states.csv"

FIRST_YEAR, LAST_YEAR = 2012, 2025
USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.3

CURRENT_TOTAL = "DebtCurrent"
CURRENT_PARTS = ["LongTermDebtCurrent", "ShortTermBorrowings", "NotesPayableCurrent", "LinesOfCreditCurrent",
                 "LoansPayableCurrent", "ConvertibleNotesPayableCurrent", "ConvertibleDebtCurrent", "SecuredDebtCurrent",
                 "OtherNotesPayableCurrent", "ShortTermBankLoansAndNotesPayable", "ShortTermNonBankLoansAndNotesPayable",
                 "OtherShortTermBorrowings", "NotesPayableRelatedPartiesClassifiedCurrent"]
NONCURRENT_TOTAL = "LongTermDebtNoncurrent"
LONG_TERM_DEBT_INCL_CURRENT = "LongTermDebt"
NONCURRENT_ALTERNATES = ["LongTermNotesPayable", "LongTermLineOfCredit", "LongTermLoansPayable", "LongTermLoansFromBank",
                         "SeniorLongTermNotes", "SeniorNotes", "ConvertibleLongTermNotesPayable", "ConvertibleDebtNoncurrent",
                         "ConvertibleNotesPayable", "SecuredLongTermDebt", "SecuredDebt", "UnsecuredDebt", "SubordinatedDebt",
                         "JuniorSubordinatedNotes", "OtherLongTermDebtNoncurrent", "OtherLongTermDebt",
                         "NotesPayableRelatedPartiesNoncurrent", "LoansPayable", "NotesPayable", "LineOfCredit",
                         "LineOfCreditFacilityAmountOutstanding", "OtherBorrowings",
                         "FederalHomeLoanBankAdvancesLongTerm", "AdvancesFromFederalHomeLoanBanks",
                         "JuniorSubordinatedDebentureOwedToUnconsolidatedSubsidiaryTrust"]
LEASE_COMBINED_NONCURRENT = "LongTermDebtAndCapitalLeaseObligations"
LEASE_COMBINED_CURRENT = "LongTermDebtAndCapitalLeaseObligationsCurrent"
LEASE_COMBINED_TOTAL = "DebtAndCapitalLeaseObligations"
LEASE_COMBINED = [LEASE_COMBINED_NONCURRENT, LEASE_COMBINED_CURRENT, LEASE_COMBINED_TOTAL]
CONVERTIBLE_DEBT_TOTAL = "ConvertibleDebt"  # total tag; used only when no current/non-current split exists (audit fix)
ALL_STANDARD_DEBT_TAGS = ([CURRENT_TOTAL, NONCURRENT_TOTAL, LONG_TERM_DEBT_INCL_CURRENT] + CURRENT_PARTS
                          + NONCURRENT_ALTERNATES + LEASE_COMBINED + [CONVERTIBLE_DEBT_TOTAL])

DEBT_LIKE = re.compile(r"Debt|Borrowing|NotesPayable|LineOfCredit|LinesOfCredit|Loans?Payable|LoansFrom|LongTermLoans|"
                       r"Debenture|SeniorNotes|SeniorLongTermNotes|ConvertibleNotes|Revolv|TermLoan|CreditFacility|"
                       r"MortgageNotes|MortgagesPayable|NotesAndLoans", re.IGNORECASE)
NOT_DEBT = re.compile(r"Securit|Receivable|Asset|Deposit|Unamortized|Gain|Loss|Extinguish|FairValue|Repayment|Proceeds|"
                      r"Payments|Issuance|Interest|Rate|Guarant|Covenant|Maturit|Percent|Ratio|Restricted|Investment|"
                      r"HeldToMaturity|AvailableForSale|Lease|Compensation|Discount|Premium|Cost|Expense|Amortization|"
                      r"Increase|Decrease|Conversion|Shares|Capacity|Maximum|Remaining|Available|Undrawn|Commitment|Fee",
                      re.IGNORECASE)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def collect_quarter(year: int, quarter: int, wanted: set[str]) -> pd.DataFrame | None:
    url = f"https://www.sec.gov/files/dera/data/financial-statement-data-sets/{year}q{quarter}.zip"
    try:
        resp = get_with_hard_timeout(SESSION, url, hard_timeout_seconds=300, timeout=(10, 120))
        resp.raise_for_status()
    except (requests.RequestException, HardTimeoutError) as e:
        print(f"  [warn] {year}q{quarter}: {e}", flush=True)
        return None
    kept = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        with zf.open("sub.txt") as f:
            sub = pd.read_csv(f, sep="\t", dtype=str, usecols=["adsh", "period"])
        period = sub[sub["adsh"].isin(wanted)].set_index("adsh")["period"]
        with zf.open("num.txt") as f:
            for chunk in pd.read_csv(f, sep="\t", dtype=str, chunksize=1_000_000,
                                     usecols=["adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value"]):
                chunk = chunk[chunk["adsh"].isin(period.index) & chunk["coreg"].isna() & (chunk["uom"] == "USD")
                              & (chunk["qtrs"] == "0")]
                if chunk.empty:
                    continue
                chunk = chunk[chunk["ddate"] == chunk["adsh"].map(period)]
                is_dim, is_custom = chunk["segments"].notna(), chunk["version"] == chunk["adsh"]
                chunk = chunk[(is_dim | is_custom) & chunk["tag"].str.contains(DEBT_LIKE)
                              & ~chunk["tag"].str.contains(NOT_DEBT)]
                chunk = chunk[pd.to_numeric(chunk["value"], errors="coerce") > 0]
                if chunk.empty:
                    continue
                out = chunk[["adsh", "tag"]].copy()
                out["is_dim"] = chunk["segments"].notna()
                out["is_custom"] = chunk["version"] == chunk["adsh"]
                kept.append(out.drop_duplicates())
    return pd.concat(kept).drop_duplicates() if kept else pd.DataFrame(columns=["adsh", "tag", "is_dim", "is_custom"])


def collect_dim_custom() -> None:
    panel = pd.read_csv(PANEL_PATH, dtype={"cik": str}, usecols=["accession"])
    raw = pd.read_csv(RAW_QUARTERS_PATH, dtype=str, usecols=["accession", "_year", "_quarter"]).dropna(subset=["accession"])
    panel = panel.join(raw.drop_duplicates("accession").set_index("accession"), on="accession")
    done = set(DONE_PATH.read_text().split()) if DONE_PATH.exists() else set()
    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        for quarter in range(1, 5):
            key = f"{year}q{quarter}"
            if key in done:
                continue
            wanted = set(panel.loc[(panel["_year"] == str(year)) & (panel["_quarter"] == str(quarter)), "accession"])
            if wanted:
                df = collect_quarter(year, quarter, wanted)
                time.sleep(REQUEST_DELAY_SECONDS)
                if df is None:
                    continue
                df.to_csv(DIM_PATH, mode="a", header=not DIM_PATH.exists(), index=False)
                print(f"  {key}: {len(df)} dimensional/custom debt-like facts", flush=True)
            done.add(key)
            DONE_PATH.write_text("\n".join(sorted(done)))


def compute_states() -> pd.DataFrame:
    panel = pd.read_csv(PANEL_PATH, dtype={"cik": str}, usecols=["accession"])
    cand = pd.read_csv(CAND_PATH, dtype={"adsh": str, "tag": str, "qtrs": str}, usecols=["adsh", "tag", "qtrs", "value"])
    cand = cand[(cand["qtrs"] == "0") & cand["tag"].isin(ALL_STANDARD_DEBT_TAGS)]
    cand["value"] = pd.to_numeric(cand["value"], errors="coerce")
    cand = cand.dropna(subset=["value"]).drop_duplicates(["adsh", "tag"])
    facts = {a: dict(zip(g["tag"], g["value"])) for a, g in cand.groupby("adsh")}
    dim = pd.read_csv(DIM_PATH, dtype=str)
    dim_accessions = set(dim["adsh"])

    def pos(t, tag):
        return max(t.get(tag, 0.0), 0.0)

    rows = []
    for acc in panel["accession"]:
        t = facts.get(acc, {})
        includes_leases = False
        # current part; LongTermDebt already includes its own current portion
        if CURRENT_TOTAL in t:
            current = pos(t, CURRENT_TOTAL)
        else:
            lt_total_covers_current = LONG_TERM_DEBT_INCL_CURRENT in t and NONCURRENT_TOTAL not in t
            parts = [p for p in CURRENT_PARTS if not (p == "LongTermDebtCurrent" and lt_total_covers_current)]
            current = sum(pos(t, p) for p in parts)
            # combined debt-and-capital-lease current tag, only when no pure long-term-debt current tag exists
            if "LongTermDebtCurrent" not in t and not lt_total_covers_current and pos(t, LEASE_COMBINED_CURRENT) > 0:
                current += pos(t, LEASE_COMBINED_CURRENT)
                includes_leases = True
        if NONCURRENT_TOTAL in t:
            noncurrent = pos(t, NONCURRENT_TOTAL)
        elif LONG_TERM_DEBT_INCL_CURRENT in t:
            noncurrent = pos(t, LONG_TERM_DEBT_INCL_CURRENT)
        elif pos(t, LEASE_COMBINED_NONCURRENT) > 0:
            # The combined debt-and-capital-lease tag is what many firms use for their bulk long-term debt
            # (e.g. Heinz FY2012: $4.8B); treating it as a last resort understated debt by ~99%.
            noncurrent = pos(t, LEASE_COMBINED_NONCURRENT)
            includes_leases = True
        else:
            noncurrent = sum(pos(t, p) for p in NONCURRENT_ALTERNATES)
            # found in the 15-case extreme-row audit (Remsleep FY2020: $44,867 ConvertibleDebt ignored)
            if "ConvertibleDebtCurrent" not in t and "ConvertibleDebtNoncurrent" not in t:
                noncurrent += pos(t, CONVERTIBLE_DEBT_TOTAL)
        total = current + noncurrent
        if pos(t, LEASE_COMBINED_TOTAL) > total:  # a reported total of debt and capital leases
            total, includes_leases = pos(t, LEASE_COMBINED_TOTAL), True

        any_std_fact = any(tag in t for tag in ALL_STANDARD_DEBT_TAGS)
        if total > 0:
            state, debt, no_tag, uncertain = "value", total, 0, 0
        elif acc in dim_accessions:
            state, debt, no_tag, uncertain = "uncertain", None, 0, 1
        elif any_std_fact:
            state, debt, no_tag, uncertain = "explicit_zero", 0.0, 0, 0
        else:
            state, debt, no_tag, uncertain = "no_debt_tag", 0.0, 1, 0
        rows.append({"accession": acc, "total_debt": debt, "debt_state": state, "no_debt_tag": no_tag,
                     "debt_uncertain": uncertain, "debt_includes_leases": includes_leases})
    return pd.DataFrame(rows)


def main():
    collect_dim_custom()
    states = compute_states()
    states.to_csv(STATES_PATH, index=False)
    print("\nDebt state distribution (all panel firm-years):")
    print((states["debt_state"].value_counts()).to_string())
    print((states["debt_state"].value_counts(normalize=True) * 100).round(1).to_string())
    print(f"debt_includes_leases fallback used: {int(states['debt_includes_leases'].sum())}")


if __name__ == "__main__":
    main()
