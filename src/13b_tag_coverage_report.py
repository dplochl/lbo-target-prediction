"""
Stage 13b — Tag-frequency and coverage report on the Stage 13a candidate pool.

For each core concept, an ordered list of candidate XBRL tags is applied as a
waterfall per firm-year (first tag present wins). Reports, per concept:
  - how often each tag is the one used (share of the panel's 10-K filings),
  - overall coverage (at least one usable tag), for all firms and non-financials,
  - coverage by fiscal year (to catch tag-switch gaps, e.g. the 2018 ASC 606 revenue tags).

Reporting only - the final extraction script comes after these decisions.

Output: data/interim/stage_13b_tag_coverage.csv (concept, tag, n_first_hit, pct_of_panel)
        data/interim/stage_13b_coverage_by_year.csv
"""

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_PATH = REPO_ROOT / "data" / "processed" / "firm_year_panel.csv"
CAND_PATH = REPO_ROOT / "data" / "interim" / "stage_13a_num_candidates.csv"
TAG_OUT = REPO_ROOT / "data" / "interim" / "stage_13b_tag_coverage.csv"
YEAR_OUT = REPO_ROOT / "data" / "interim" / "stage_13b_coverage_by_year.csv"

# concept -> (qtrs, ordered tag list); qtrs 0 = instant (balance sheet), 4 = annual flow
CONCEPTS = {
    "total_assets": ("0", ["Assets", "LiabilitiesAndStockholdersEquity"]),
    "cash": ("0", ["CashAndCashEquivalentsAtCarryingValue",
                   "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash", "CashAndDueFromBanks",
                   "CashCashEquivalentsAndFederalFundsSold"]),
    "total_liabilities": ("0", ["Liabilities"]),
    "total_debt_any_tag": ("0", ["LongTermDebtNoncurrent", "LongTermDebt", "LongTermDebtAndCapitalLeaseObligations",
                                 "LongTermDebtCurrent", "LongTermDebtAndCapitalLeaseObligationsCurrent", "DebtCurrent",
                                 "ShortTermBorrowings", "NotesPayableCurrent", "NotesPayable", "LongTermNotesPayable",
                                 "ConvertibleNotesPayableCurrent", "ConvertibleNotesPayable",
                                 "ConvertibleLongTermNotesPayable", "ConvertibleDebtCurrent", "ConvertibleDebtNoncurrent",
                                 "SecuredDebt", "UnsecuredDebt", "SubordinatedDebt", "OtherBorrowings",
                                 "ShortTermBankLoansAndNotesPayable", "OtherLongTermDebtNoncurrent",
                                 "SecuredLongTermDebt", "NotesPayableRelatedPartiesClassifiedCurrent",
                                 "NotesPayableRelatedPartiesNoncurrent", "LinesOfCreditCurrent", "LineOfCredit",
                                 "LongTermLineOfCredit", "LoansPayableCurrent", "LongTermLoansPayable", "LoansPayable",
                                 "DebtAndCapitalLeaseObligations", "SeniorNotes", "SeniorLongTermNotes",
                                 "JuniorSubordinatedNotes", "OtherShortTermBorrowings", "SecuredDebtCurrent",
                                 "OtherNotesPayableCurrent", "OtherLongTermDebt", "LongTermLoansFromBank",
                                 "ShortTermNonBankLoansAndNotesPayable", "FederalHomeLoanBankAdvancesLongTerm",
                                 "AdvancesFromFederalHomeLoanBanks",
                                 "JuniorSubordinatedDebentureOwedToUnconsolidatedSubsidiaryTrust"]),
    "revenue": ("4", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                      "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet",
                      "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense"]),
    "net_income": ("4", ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]),
    "operating_cash_flow": ("4", ["NetCashProvidedByUsedInOperatingActivities",
                                  "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"]),
    "ppe_net": ("0", ["PropertyPlantAndEquipmentNet",
                      "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
                      "PublicUtilitiesPropertyPlantAndEquipmentNet"]),
    "rd_expense": ("4", ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
                         "ResearchAndDevelopmentExpenseSoftwareExcludingAcquiredInProcessCost"]),
    "stockholders_equity": ("0", ["StockholdersEquity",
                                  "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]),
}


def main():
    panel = pd.read_csv(PANEL_PATH, dtype={"cik": str}, usecols=["cik", "fiscal_year", "accession", "sic", "fin_sic_60_67"])
    cand = pd.read_csv(CAND_PATH, dtype={"adsh": str, "tag": str, "qtrs": str, "ddate": str})
    cand["value"] = pd.to_numeric(cand["value"], errors="coerce")
    cand = cand.dropna(subset=["value"]).drop_duplicates(["adsh", "tag", "qtrs"])
    n = len(panel)
    tag_rows, year_rows = [], []
    have = panel[["accession", "fiscal_year", "fin_sic_60_67"]].copy()

    for concept, (qtrs, tags) in CONCEPTS.items():
        sub = cand[(cand["qtrs"] == qtrs) & cand["tag"].isin(tags)]
        piv = sub.pivot(index="adsh", columns="tag", values="value").reindex(columns=tags)
        first_hit = piv.notna().idxmax(axis=1).where(piv.notna().any(axis=1))
        merged = have.join(first_hit.rename("first_tag"), on="accession")
        used = merged["first_tag"].notna()
        for tag in tags:
            k = int((merged["first_tag"] == tag).sum())
            present = int(piv[tag].notna().sum()) if tag in piv else 0
            tag_rows.append({"concept": concept, "tag": tag, "n_first_hit": k, "pct_first_hit": round(k / n * 100, 1),
                             "n_present_at_all": present})
        nonfin = ~merged["fin_sic_60_67"].astype(bool)
        print(f"{concept:22s} coverage all {used.mean():6.1%} | non-financial {used[nonfin].mean():6.1%} | "
              f"financial {used[~nonfin].mean():6.1%}")
        by_year = used.groupby(merged["fiscal_year"]).mean().round(3)
        for fy, v in by_year.items():
            year_rows.append({"concept": concept, "fiscal_year": fy, "coverage": v})

    pd.DataFrame(tag_rows).to_csv(TAG_OUT, index=False)
    yr = pd.DataFrame(year_rows).pivot(index="fiscal_year", columns="concept", values="coverage")
    yr.to_csv(YEAR_OUT)
    print("\nCoverage by fiscal year:")
    print((yr * 100).round(0).astype(int).to_string())
    print("\nFirst-hit tag usage:")
    print(pd.DataFrame(tag_rows).query("n_first_hit > 0")[["concept", "tag", "n_first_hit", "pct_first_hit"]].to_string(index=False))


if __name__ == "__main__":
    main()
