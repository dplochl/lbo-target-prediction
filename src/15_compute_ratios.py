"""
Stage 15 — Financial ratios (features) from the fundamentals table.

Input : data/processed/firm_year_fundamentals.csv  (Stage 14)
Output: data/processed/firm_year_ratios.csv        (keys + ratios + dummies)
Usage : python src/15_compute_ratios.py            full run
        python src/15_compute_ratios.py --sample   validation run on a few known firms, prints values, writes nothing
        python src/15_compute_ratios.py --dry-run  full computation, prints coverage/distribution stats, writes nothing

Conventions
  - Ratios are raw (unwinsorized). Winsorization thresholds must be fitted on the training
    sample only, so they belong to the modeling stage, not here.
  - Every ratio is missing when an input is missing or a denominator is <= 0 (rules per ratio
    below); no fill except the fills already made in Stage 14.
  - Lags (prior year, 2 years back) are keyed on `period_end` dates, not on `fiscal_year`
    (DERA's fiscal-year tag is inconsistent): the previous observation of the same firm must
    be 335-395 days earlier, otherwise the lag is treated as missing.
  - Timing: balance-sheet and flow items are at the 10-K period end (= prediction date);
    `entity_public_float` is measured at the end of the firm's second fiscal quarter (about
    six months earlier), so valuation ratios pair a mid-year float with year-end accounts.
    Same convention for every firm; no look-ahead.

Formulas
  leverage             = total_debt / total_assets            (missing if debt_uncertain = 1)
  cash_ratio           = cash / total_assets
  roa                  = net_income / total_assets            (year-end assets)
  fcf                  = operating_cash_flow - capex           (missing if either is missing)
  fcf_to_assets        = fcf / total_assets
  fcf_vol              = sample std (ddof=1) of fcf_to_assets over years t, t-1, t-2 when all three are
                         present and consecutive; after Jiang's FCFFvol, scaled by assets so firms of
                         different size are comparable. Fallback (decision 2026-09-21): when only years
                         t and t-1 are available, the std of those two, with fcf_vol_2y_fallback = 1
                         (a two-point std is noisier; use the flag in robustness runs)
  interest_coverage    = operating_income / interest_expense   (interest_expense > 0 only)
  no_interest_expense  = 1 if there is no interest expense to report: interest_expense is 0, or it is
                         untagged and debt is zero (no_debt_tag = 1 or a reported zero debt) - economically
                         "no debt, no interest"; coverage is then missing (or unbounded)
  interest_data_missing = 1 if interest_expense is untagged although the firm has (or may have) debt
                         (positive or uncertain debt) - a data gap, not an economic zero
      the two flags are mutually exclusive and both 0 when interest_expense > 0
  ppe_to_assets        = ppe_net / total_assets                (Jiang PPEpct; ppe_net is zero-filled, ppe_no_tag flags it)
  rd_to_revenue        = rd_expense / revenue                  (Jiang RDpct; revenue > 0 only, so pre-revenue
                         R&D firms are missing here because revenue is zero-filled - see rd_to_assets)
  rd_to_assets         = rd_expense / total_assets             (defined for pre-revenue firms as well)
  log_assets           = ln(total_assets)                      (total_assets > 0)
  current_ratio        = assets_current / liabilities_current  (liabilities_current > 0)
  log_float            = ln(entity_public_float)               (from Stage 14)
  float_to_book        = entity_public_float / equity          (equity != 0, float > 0)
  neg_book_dummy       = 1 if equity < 0
  neg_book_x_float_to_book       = neg_book_dummy * float_to_book
  float_to_earnings    = entity_public_float / net_income      (net_income != 0, float > 0)
  neg_earnings_dummy   = 1 if net_income < 0
  neg_earnings_x_float_to_earnings = neg_earnings_dummy * float_to_earnings
      The three-part treatment (raw ratio, negative dummy, dummy x ratio) follows Jiang (2019),
      who includes PtoB together with a negative-P/B dummy and the negPtoB x PtoB interaction
      instead of dropping negative-book firms.
  ebit                 = operating_income; if missing, pretax_income + interest_expense
  altman_z2            = 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4   (Altman Z'' for non-manufacturers)
      X1 = (assets_current - liabilities_current) / total_assets
      X2 = retained_earnings / total_assets
      X3 = ebit / total_assets
      X4 = entity_public_float / total_liabilities   (float stands in for the market value of equity)
      no constant term (it does not affect a logit); missing for financials (fin_sic_60_64) where
      the model is not valid, and where any input is missing or total_liabilities <= 0
  altman_z2_book       = same with X4 = equity / total_liabilities (Altman's book-equity form),
                         kept as a robustness variant
  sales_growth_1y      = revenue_t / revenue_{t-1} - 1         (both years' revenue real, i.e.
                         revenue_no_tag = 0, prior revenue > 0)

Not built (documented gaps): analyst-coverage growth (no data source), ownership concentration
(dropped with the market-cap decision, design_memo.md Section 6).
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
IN_PATH = REPO_ROOT / "data" / "processed" / "firm_year_fundamentals.csv"
OUT_PATH = REPO_ROOT / "data" / "processed" / "firm_year_ratios.csv"

SAMPLE_CIKS = ["0000826083", "0000724606", "0000086144", "0000046640", "0001724570", "0000277375"]  # Dell, Panera, Safeway, Heinz, Datto, Delta Natural Gas


def safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    """num/den where den > 0, else NaN."""
    return num / den.where(den > 0)


def add_lags(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Prior-year and two-years-back values, matched on period_end spacing (335-395 days)."""
    df = df.sort_values(["cik", "period_end"]).copy()
    g = df.groupby("cik")
    days1 = (df["period_end"] - g["period_end"].shift(1)).dt.days
    ok1 = days1.between(335, 395)
    days2 = (g["period_end"].shift(1) - g["period_end"].shift(2)).dt.days
    ok2 = ok1 & days2.between(335, 395)
    for c in cols:
        df[f"{c}_lag1"] = g[c].shift(1).where(ok1)
        df[f"{c}_lag2"] = g[c].shift(2).where(ok2)
    return df


def compute(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["period_end"] = pd.to_datetime(df["period_end"])
    ta, fl = df["total_assets"], df["entity_public_float"]

    df["leverage"] = safe_div(df["total_debt"], ta)
    df["cash_ratio"] = safe_div(df["cash"], ta)
    df["roa"] = safe_div(df["net_income"], ta)
    df["fcf"] = df["operating_cash_flow"] - df["capex"]
    df["fcf_to_assets"] = safe_div(df["fcf"], ta)

    interest = df["interest_expense"]
    zero_debt = (df["no_debt_tag"] == 1) | (df["debt_state"] == "explicit_zero")
    interest_absent = ~(interest > 0)
    df["no_interest_expense"] = (interest_absent & ((interest == 0) | zero_debt)).astype(int)
    df["interest_data_missing"] = (interest_absent & (df["no_interest_expense"] == 0)).astype(int)
    df["interest_coverage"] = df["operating_income"] / interest.where(interest > 0)

    df["ppe_to_assets"] = safe_div(df["ppe_net"], ta)
    df["rd_to_revenue"] = safe_div(df["rd_expense"], df["revenue"])
    df["rd_to_assets"] = safe_div(df["rd_expense"], ta)
    df["log_assets"] = np.log(ta.where(ta > 0))
    df["current_ratio"] = safe_div(df["assets_current"], df["liabilities_current"])

    fl_pos = fl.where(fl > 0)
    book, ni = df["equity"], df["net_income"]
    df["float_to_book"] = fl_pos / book.where(book != 0)
    df["neg_book_dummy"] = (book < 0).astype(int)
    df["neg_book_x_float_to_book"] = df["neg_book_dummy"] * df["float_to_book"]
    df["float_to_earnings"] = fl_pos / ni.where(ni != 0)
    df["neg_earnings_dummy"] = (ni < 0).astype(int)
    df["neg_earnings_x_float_to_earnings"] = df["neg_earnings_dummy"] * df["float_to_earnings"]

    df["ebit"] = df["operating_income"].fillna(df["pretax_income"] + df["interest_expense"])
    x1 = safe_div(df["assets_current"] - df["liabilities_current"], ta)
    x2 = safe_div(df["retained_earnings"], ta)
    x3 = safe_div(df["ebit"], ta)
    tl = df["total_liabilities"]
    core = 6.56 * x1 + 3.26 * x2 + 6.72 * x3
    valid = ~df["fin_sic_60_64"].astype(bool)
    df["altman_z2"] = (core + 1.05 * safe_div(fl_pos, tl)).where(valid)
    df["altman_z2_book"] = (core + 1.05 * safe_div(book, tl)).where(valid)

    df = add_lags(df, ["fcf_to_assets", "revenue", "revenue_no_tag"])
    fcf3 = df[["fcf_to_assets", "fcf_to_assets_lag1", "fcf_to_assets_lag2"]]
    vol3 = fcf3.std(axis=1, ddof=1).where(fcf3.notna().all(axis=1))
    fcf2 = df[["fcf_to_assets", "fcf_to_assets_lag1"]]
    vol2 = fcf2.std(axis=1, ddof=1).where(fcf2.notna().all(axis=1))
    df["fcf_vol"] = vol3.fillna(vol2)
    df["fcf_vol_2y_fallback"] = (vol3.isna() & vol2.notna()).astype(int)
    rev_ok = (df["revenue_no_tag"] == 0) & (df["revenue_no_tag_lag1"] == 0) & (df["revenue_lag1"] > 0)
    df["sales_growth_1y"] = (df["revenue"] / df["revenue_lag1"] - 1).where(rev_ok)
    return df.sort_values(["cik", "fiscal_year"])


RATIO_COLS = ["leverage", "cash_ratio", "roa", "fcf", "fcf_to_assets", "fcf_vol", "fcf_vol_2y_fallback", "interest_coverage",
              "no_interest_expense", "interest_data_missing", "ppe_to_assets", "rd_to_revenue", "rd_to_assets", "log_assets",
              "current_ratio", "log_float", "float_to_book", "neg_book_dummy", "neg_book_x_float_to_book", "float_to_earnings",
              "neg_earnings_dummy", "neg_earnings_x_float_to_earnings", "ebit", "altman_z2", "altman_z2_book",
              "sales_growth_1y"]
KEY_COLS = ["cik", "company_name", "fiscal_year", "accession", "period_end", "sic", "afs", "fin_sic_60_64", "fin_sic_60_67",
            "entity_public_float"]
FLAG_COLS = ["no_debt_tag", "debt_uncertain", "debt_includes_leases", "revenue_no_tag", "ppe_no_tag", "rd_no_tag",
             "revenue_zero_suspect", "ppe_zero_suspect", "capex_zero_fill", "liabilities_derived"]


def main():
    sample = "--sample" in sys.argv
    dry_run = "--dry-run" in sys.argv
    df = pd.read_csv(IN_PATH, dtype={"cik": str})
    if sample:
        df = df[df["cik"].isin(SAMPLE_CIKS)]
    out = compute(df)
    if sample:
        pd.set_option("display.width", 250)
        show = ["company_name", "fiscal_year", "leverage", "cash_ratio", "roa", "fcf_to_assets", "fcf_vol",
                "interest_coverage", "log_float", "float_to_book", "float_to_earnings", "altman_z2", "sales_growth_1y"]
        print(out[show].round(3).to_string(index=False))
        return
    if dry_run:
        print(f"Dry run: {len(out)} rows computed, nothing written")
    else:
        out[KEY_COLS + FLAG_COLS + RATIO_COLS].to_csv(OUT_PATH, index=False)
        print(f"Wrote {OUT_PATH}: {len(out)} rows")
    labels = pd.read_csv(REPO_ROOT / "data" / "processed" / "firm_year_panel.csv", usecols=["accession", "y_pe_main_h3", "h3_observed"])
    out = out.merge(labels, on="accession")
    nonfin = ~out["fin_sic_60_67"].astype(bool)
    pos = (out["y_pe_main_h3"] == 1) & out["h3_observed"]
    neg = (out["y_pe_main_h3"] == 0) & out["h3_observed"]
    rows = [{"ratio": c, "all": out[c].notna().mean(), "non_fin": out.loc[nonfin, c].notna().mean(),
             "positives(h3)": out.loc[pos, c].notna().mean(), "negatives(h3)": out.loc[neg, c].notna().mean(),
             "p1": out[c].quantile(0.01), "median": out[c].median(), "p99": out[c].quantile(0.99)} for c in RATIO_COLS]
    print(pd.DataFrame(rows).set_index("ratio").round(3).to_string())


if __name__ == "__main__":
    main()
