"""
Stage 13d — Additional num.txt tags for the Altman Z / interest-coverage / margin family
of features, plus a few structural balance-sheet items and industry-specific revenue and
PP&E tags found to matter when checking the zero-fill rule (Stage 14).

Same filters as Stage 13a: panel accessions only, standard taxonomy tags, no co-registrant
rows, NO dimensional rows (segments empty), USD, value dated at the filing's own period end,
qtrs 0 (instant) or 4 (annual). Exact tag names (no pattern matching).

Output: data/interim/stage_13d_num_extra.csv  (adsh, tag, ddate, qtrs, value)
"""

import io
import time
import zipfile
from pathlib import Path

import pandas as pd
import requests

from edgar_utils import HardTimeoutError, get_user_agent, get_with_hard_timeout

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_PATH = REPO_ROOT / "data" / "processed" / "firm_year_panel.csv"
RAW_QUARTERS_PATH = REPO_ROOT / "data" / "interim" / "stage_11_raw_quarters.csv"
OUT_PATH = REPO_ROOT / "data" / "interim" / "stage_13d_num_extra.csv"
DONE_PATH = REPO_ROOT / "data" / "interim" / "stage_13d_quarters_done.txt"

USER_AGENT = get_user_agent()  # SEC_USER_AGENT from the environment or .env (see edgar_utils)
REQUEST_DELAY_SECONDS = 0.3

TAGS = {
    # income statement / cash flow (annual, qtrs 4)
    "OperatingIncomeLoss", "GrossProfit", "CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold",
    "CostOfServices", "SellingGeneralAndAdministrativeExpense", "GeneralAndAdministrativeExpense",
    "OperatingExpenses", "CostsAndExpenses", "IncomeTaxExpenseBenefit",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
    "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
    "InterestExpense", "InterestExpenseDebt", "InterestExpenseLongTermDebt", "InterestExpenseNonoperating",
    "InterestIncomeExpenseNet", "InterestIncomeExpenseNonoperatingNet", "InterestPaid", "InterestPaidNet",
    "DepreciationDepletionAndAmortization", "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNet",
    "Depreciation", "AmortizationOfIntangibleAssets",
    # industry-specific revenue tags not matched by the Stage 13a name pattern
    "InterestAndDividendIncomeOperating", "PremiumsEarnedNet", "InterestAndFeeIncomeLoansAndLeases",
    # balance sheet (instant, qtrs 0)
    "RetainedEarningsAccumulatedDeficit", "Goodwill", "IntangibleAssetsNetExcludingGoodwill", "InventoryNet",
    "AccountsReceivableNetCurrent", "AccountsPayableCurrent", "TreasuryStockValue",
    "AccumulatedDepreciationDepletionAndAmortizationPropertyPlantAndEquipment", "PropertyPlantAndEquipmentGross",
    "RealEstateInvestmentPropertyNet", "OilAndGasPropertyFullCostMethodNet", "OilAndGasPropertySuccessfulEffortMethodNet",
    "AssetsCurrent", "LiabilitiesCurrent",
}

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": USER_AGENT})


def process_quarter(key: str, wanted: set[str]) -> pd.DataFrame | None:
    try:
        resp = get_with_hard_timeout(SESSION, f"https://www.sec.gov/files/dera/data/financial-statement-data-sets/{key}.zip",
                                     hard_timeout_seconds=300, timeout=(10, 120))
        resp.raise_for_status()
    except (requests.RequestException, HardTimeoutError) as e:
        print(f"  [warn] {key}: {e}", flush=True)
        return None
    kept = []
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        with zf.open("sub.txt") as f:
            sub = pd.read_csv(f, sep="\t", dtype=str, usecols=["adsh", "period"])
        period = sub[sub["adsh"].isin(wanted)].set_index("adsh")["period"]
        with zf.open("num.txt") as f:
            for chunk in pd.read_csv(f, sep="\t", dtype=str, chunksize=1_000_000,
                                     usecols=["adsh", "tag", "version", "ddate", "qtrs", "uom", "segments", "coreg", "value"]):
                chunk = chunk[chunk["adsh"].isin(period.index) & chunk["coreg"].isna() & chunk["segments"].isna()
                              & (chunk["uom"] == "USD") & chunk["qtrs"].isin(["0", "4"])
                              & (chunk["version"] != chunk["adsh"]) & chunk["tag"].isin(TAGS)]
                if chunk.empty:
                    continue
                chunk = chunk[chunk["ddate"] == chunk["adsh"].map(period)]
                kept.append(chunk[["adsh", "tag", "ddate", "qtrs", "value"]])
    return pd.concat(kept) if kept else pd.DataFrame(columns=["adsh", "tag", "ddate", "qtrs", "value"])


def main():
    panel = pd.read_csv(PANEL_PATH, dtype={"cik": str}, usecols=["accession"])
    raw = pd.read_csv(RAW_QUARTERS_PATH, dtype=str, usecols=["accession", "_year", "_quarter"]).dropna(subset=["accession"])
    panel = panel.join(raw.drop_duplicates("accession").set_index("accession"), on="accession")
    done = set(DONE_PATH.read_text().split()) if DONE_PATH.exists() else set()
    for year in range(2012, 2026):
        for quarter in range(1, 5):
            key = f"{year}q{quarter}"
            wanted = set(panel.loc[(panel["_year"] == str(year)) & (panel["_quarter"] == str(quarter)), "accession"])
            if key in done or not wanted:
                continue
            df = process_quarter(key, wanted)
            time.sleep(REQUEST_DELAY_SECONDS)
            if df is None:
                continue
            df.to_csv(OUT_PATH, mode="a", header=not OUT_PATH.exists(), index=False)
            done.add(key)
            DONE_PATH.write_text("\n".join(sorted(done)))
            print(f"  {key}: {len(df)} rows", flush=True)


if __name__ == "__main__":
    main()
