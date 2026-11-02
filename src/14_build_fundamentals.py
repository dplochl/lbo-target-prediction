"""
Stage 14 — Firm-year fundamentals table: one row per panel firm-year with every raw
accounting concept extracted from DERA num.txt, the fill rules applied, and the
float-based size measure. This is the input to the feature (ratio) formulas; no ratios
are computed here.

Sources : data/processed/firm_year_panel.csv     (keys, SIC, float)
          data/interim/stage_13a_num_candidates.csv, stage_13d_num_extra.csv (facts)
          data/interim/stage_13c_debt_states.csv (three-state debt)
          data/interim/stage_11c_period_end.csv  (period end)
Output  : data/processed/firm_year_fundamentals.csv

All facts are non-dimensional, standard-tag, USD, dated at the filing's own period end.
Each concept is a priority waterfall (first tag present wins); `*_source` columns record
which tier was used where several exist.

Fill rules
  - revenue, ppe_net, rd_expense: zero-fill when no tag is found, with an indicator
    (`revenue_no_tag`, `ppe_no_tag`, `rd_no_tag` = 1). Decision 2026-09-21. Revenue and PP&E
    first use industry-specific fallback tags (real estate, oil and gas, brokerage, utilities,
    REIT property...) so the zero-fill is not applied to firms that do report revenue/PP&E
    under an industry tag. R&D absence means "no R&D line", so a zero is the natural value.
  - debt: three-state rule (Stage 13c): value / zero + no_debt_tag / missing + debt_uncertain.
  - capex: set to 0 with `capex_zero_fill` = 1 where capex is untagged AND the firm has no PP&E
    (`ppe_no_tag` = 1): no PP&E means structurally no capex to report. Decision 2026-09-21.
    Firms that report PP&E but no capex tag stay missing.
  - `revenue_zero_suspect` / `ppe_zero_suspect` flag the zero-fills contradicted by other facts
    (cost of revenue / inventory reported without revenue; capex reported without PP&E).
  - everything else stays missing when no tag is found (no fill).
Derived where the identity is exact: total_liabilities = total_assets - equity when
`Liabilities` is untagged (`liabilities_derived`); gross_profit = revenue - cost_of_revenue
when `GrossProfit` is untagged and both parts are real (not zero-filled) facts
(`gross_profit_derived`); PP&E net = gross - accumulated depreciation when net is untagged.

Market value: EntityPublicFloat is the market-value measure (true market cap unavailable,
see design_memo.md Section 6); `log_float` = ln(float), missing when float = 0.
"""

from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_PATH = REPO_ROOT / "data" / "processed" / "firm_year_panel.csv"
CAND_A = REPO_ROOT / "data" / "interim" / "stage_13a_num_candidates.csv"
CAND_D = REPO_ROOT / "data" / "interim" / "stage_13d_num_extra.csv"
DEBT_PATH = REPO_ROOT / "data" / "interim" / "stage_13c_debt_states.csv"
PERIOD_PATH = REPO_ROOT / "data" / "interim" / "stage_11c_period_end.csv"
OUT_PATH = REPO_ROOT / "data" / "processed" / "firm_year_fundamentals.csv"

# concept -> (qtrs, tiers); tiers is an ordered list of (source_label, [tags]) - first tier with a hit wins
CONCEPTS = {
    "total_assets": ("0", [("assets", ["Assets"]), ("liab_and_equity", ["LiabilitiesAndStockholdersEquity"])]),
    "assets_current": ("0", [("std", ["AssetsCurrent"])]),
    "liabilities_current": ("0", [("std", ["LiabilitiesCurrent"])]),
    "cash": ("0", [("std", ["CashAndCashEquivalentsAtCarryingValue",
                            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents", "Cash", "CashAndDueFromBanks",
                            "CashCashEquivalentsAndFederalFundsSold"])]),
    "equity": ("0", [("std", ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"])]),
    "total_liabilities_reported": ("0", [("std", ["Liabilities"])]),
    "retained_earnings": ("0", [("std", ["RetainedEarningsAccumulatedDeficit"])]),
    "goodwill": ("0", [("std", ["Goodwill"])]),
    "intangibles_ex_goodwill": ("0", [("std", ["IntangibleAssetsNetExcludingGoodwill"])]),
    "inventory": ("0", [("std", ["InventoryNet"])]),
    "receivables": ("0", [("std", ["AccountsReceivableNetCurrent"])]),
    "payables": ("0", [("std", ["AccountsPayableCurrent"])]),
    "ppe_net": ("0", [("std", ["PropertyPlantAndEquipmentNet",
                               "PropertyPlantAndEquipmentAndFinanceLeaseRightOfUseAssetAfterAccumulatedDepreciationAndAmortization",
                               "PublicUtilitiesPropertyPlantAndEquipmentNet"]),
                      ("industry", ["RealEstateInvestmentPropertyNet", "OilAndGasPropertyFullCostMethodNet",
                                    "OilAndGasPropertySuccessfulEffortMethodNet"])]),
    "revenue": ("4", [("std", ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet",
                               "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet",
                               "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense"]),
                      ("industry", ["RealEstateRevenueNet", "OilAndGasRevenue", "OilAndGasSalesRevenue",
                                    "RegulatedAndUnregulatedOperatingRevenue", "UnregulatedOperatingRevenue",
                                    "HealthCareOrganizationRevenue", "ContractsRevenue", "BrokerageCommissionsRevenue",
                                    "InvestmentBankingRevenue", "OperatingLeasesIncomeStatementLeaseRevenue",
                                    "InterestAndDividendIncomeOperating", "PremiumsEarnedNet",
                                    "InterestAndFeeIncomeLoansAndLeases"])]),
    "net_income": ("4", [("std", ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"])]),
    "operating_cash_flow": ("4", [("std", ["NetCashProvidedByUsedInOperatingActivities",
                                           "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"])]),
    "capex": ("4", [("std", ["PaymentsToAcquirePropertyPlantAndEquipment"]),
                    ("productive_assets", ["PaymentsToAcquireProductiveAssets",
                                           "PaymentsToAcquireOtherPropertyPlantAndEquipment"])]),
    "rd_expense": ("4", [("std", ["ResearchAndDevelopmentExpense", "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
                                  "ResearchAndDevelopmentExpenseSoftwareExcludingAcquiredInProcessCost"])]),
    "operating_income": ("4", [("std", ["OperatingIncomeLoss"])]),
    "pretax_income": ("4", [("std", [
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"])]),
    "income_tax": ("4", [("std", ["IncomeTaxExpenseBenefit"])]),
    "interest_expense": ("4", [("expense", ["InterestExpense", "InterestExpenseDebt", "InterestExpenseLongTermDebt",
                                            "InterestExpenseNonoperating"]),
                               ("cash_paid", ["InterestPaidNet", "InterestPaid"])]),
    "depreciation_amortization": ("4", [("std", ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                                                 "DepreciationAmortizationAndAccretionNet", "Depreciation"])]),
    "cost_of_revenue": ("4", [("std", ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold", "CostOfServices"])]),
    "gross_profit_reported": ("4", [("std", ["GrossProfit"])]),
    "sga_expense": ("4", [("sga", ["SellingGeneralAndAdministrativeExpense"]), ("ga_only", ["GeneralAndAdministrativeExpense"])]),
    "ppe_gross": ("0", [("std", ["PropertyPlantAndEquipmentGross"])]),
    "ppe_accum_depr": ("0", [("std", ["AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment"])]),
}


def waterfall(wide: pd.DataFrame, tiers) -> tuple[pd.Series, pd.Series]:
    value = pd.Series(np.nan, index=wide.index)
    source = pd.Series(None, index=wide.index, dtype=object)
    for label, tags in tiers:
        for tag in tags:
            if tag not in wide.columns:
                continue
            take = value.isna() & wide[tag].notna()
            value[take] = wide.loc[take, tag]
            source[take] = label
    return value, source


def main():
    panel = pd.read_csv(PANEL_PATH, dtype={"cik": str},
                        usecols=["cik", "company_name", "fiscal_year", "accession", "sic", "afs", "form", "filed_date",
                                 "entity_public_float", "float_date", "fin_sic_60_64", "fin_sic_60_67"])
    facts = pd.concat([pd.read_csv(CAND_A, dtype={"adsh": str, "tag": str, "qtrs": str}, usecols=["adsh", "tag", "qtrs", "value"]),
                       pd.read_csv(CAND_D, dtype={"adsh": str, "tag": str, "qtrs": str}, usecols=["adsh", "tag", "qtrs", "value"])])
    facts["value"] = pd.to_numeric(facts["value"], errors="coerce")
    facts = facts.dropna(subset=["value"]).drop_duplicates(["adsh", "tag", "qtrs"])
    wide = {q: facts[facts["qtrs"] == q].pivot(index="adsh", columns="tag", values="value") for q in ("0", "4")}
    acc = pd.Index(panel["accession"])

    out = panel.copy()
    for concept, (qtrs, tiers) in CONCEPTS.items():
        w = wide[qtrs].reindex(acc)
        value, source = waterfall(w, tiers)
        out[concept] = value.to_numpy()
        if len(tiers) > 1:
            out[f"{concept}_source"] = source.to_numpy()

    # derived identities
    out["liabilities_derived"] = out["total_liabilities_reported"].isna() & out["total_assets"].notna() & out["equity"].notna()
    out["total_liabilities"] = np.where(out["liabilities_derived"], out["total_assets"] - out["equity"],
                                        out["total_liabilities_reported"])
    out["ppe_net_derived"] = out["ppe_net"].isna() & out["ppe_gross"].notna() & out["ppe_accum_depr"].notna()
    out.loc[out["ppe_net_derived"], "ppe_net"] = out["ppe_gross"] - out["ppe_accum_depr"]
    out["gross_profit_derived"] = (out["gross_profit_reported"].isna() & out["revenue"].notna() & out["cost_of_revenue"].notna())
    out["gross_profit"] = np.where(out["gross_profit_derived"], out["revenue"] - out["cost_of_revenue"], out["gross_profit_reported"])

    # zero-fill with indicators (revenue / PP&E after the industry fallbacks, so only true absences are filled)
    for col, flag in (("revenue", "revenue_no_tag"), ("ppe_net", "ppe_no_tag"), ("rd_expense", "rd_no_tag")):
        out[flag] = out[col].isna().astype(int)
        out[col] = out[col].fillna(0.0)
    out["capex_zero_fill"] = (out["capex"].isna() & (out["ppe_no_tag"] == 1)).astype(int)
    out.loc[out["capex_zero_fill"] == 1, "capex"] = 0.0
    # gross profit derived above used real revenue only (before the fill); keep it that way.
    # Zero-fills that are probably wrong: revenue "absent" although cost of revenue or inventory is reported
    # (real revenue under an unlisted tag, ~1% of rows), PP&E "absent" although capex is reported (~2%).
    out["revenue_zero_suspect"] = ((out["revenue_no_tag"] == 1) & (out["cost_of_revenue"].notna() | out["inventory"].notna())).astype(int)
    out["ppe_zero_suspect"] = ((out["ppe_no_tag"] == 1) & out["capex"].notna()).astype(int)

    debt = pd.read_csv(DEBT_PATH)
    out = out.merge(debt, on="accession", how="left", validate="one_to_one")
    period = pd.read_csv(PERIOD_PATH, dtype=str)[["accession", "period", "fye"]].rename(columns={"period": "period_end"})
    out = out.merge(period, on="accession", how="left", validate="one_to_one")
    out["period_end"] = pd.to_datetime(out["period_end"], format="%Y%m%d")

    out["log_float"] = np.log(out["entity_public_float"].where(out["entity_public_float"] > 0))
    out = out.drop(columns=["total_liabilities_reported", "ppe_gross", "ppe_accum_depr", "gross_profit_reported"])
    out.to_csv(OUT_PATH, index=False)
    print(f"Wrote {OUT_PATH}: {len(out)} rows, {out.shape[1]} columns")
    report(out)


def report(out: pd.DataFrame) -> None:
    labels = pd.read_csv(PANEL_PATH, usecols=["accession", "y_pe_main_h3", "h3_observed"])
    out = out.merge(labels, on="accession")
    nonfin = ~out["fin_sic_60_67"].astype(bool)
    pos = (out["y_pe_main_h3"] == 1) & out["h3_observed"]
    neg = (out["y_pe_main_h3"] == 0) & out["h3_observed"]
    cols = ["total_assets", "assets_current", "liabilities_current", "cash", "equity", "total_liabilities", "retained_earnings",
            "goodwill", "intangibles_ex_goodwill", "inventory", "receivables", "payables", "net_income", "operating_cash_flow",
            "capex", "operating_income", "pretax_income", "income_tax", "interest_expense", "depreciation_amortization",
            "cost_of_revenue", "gross_profit", "sga_expense", "total_debt"]
    rows = []
    for c in cols:
        rows.append({"column": c, "all": out[c].notna().mean(), "non_fin": out.loc[nonfin, c].notna().mean(),
                     "positives(h3)": out.loc[pos, c].notna().mean(), "negatives(h3)": out.loc[neg, c].notna().mean()})
    r = pd.DataFrame(rows).set_index("column")
    print("\nAvailability (share of firm-years with a value):")
    print((r * 100).round(1).to_string())
    print("\nFilled-concept indicators (share of rows with no tag => zero-filled):")
    for f in ("revenue_no_tag", "ppe_no_tag", "rd_no_tag", "no_debt_tag", "debt_uncertain", "revenue_zero_suspect", "ppe_zero_suspect"):
        print(f"  {f:16s} all {out[f].mean():6.1%} | non-fin {out.loc[nonfin, f].mean():6.1%} | "
              f"positives {out.loc[pos, f].mean():6.1%} | negatives {out.loc[neg, f].mean():6.1%}")
    print("\nDerived / source shares:")
    print(f"  liabilities_derived {out['liabilities_derived'].mean():.1%} | gross_profit_derived {out['gross_profit_derived'].mean():.1%} "
          f"| ppe_net_derived {out['ppe_net_derived'].mean():.1%}")
    print("  revenue source:", out["revenue_source"].value_counts(dropna=False).to_dict())
    print("  ppe source:", out["ppe_net_source"].value_counts(dropna=False).to_dict())
    print("  interest source:", out["interest_expense_source"].value_counts(dropna=False).to_dict())


if __name__ == "__main__":
    main()
