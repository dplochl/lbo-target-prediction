# Data Dictionary

**Status:** template — to be filled in as the pipeline is built. See `design_memo.md` for the design decisions this schema must respect (no-look-ahead rule in particular).

---

## Core table: `firm_year_panel`

**Primary key:** `firm_id` + `fiscal_year`

One row per US-domestic, exchange-listed company per year (the full risk-set population, per `design_memo.md` Section 2 — not a pre-balanced sample).

| Column | Type | Definition | Source | Notes |
|---|---|---|---|---|
| `firm_id` | string | Internal stable identifier | — | |
| `cik` | string | SEC CIK number | EDGAR | zero-padded to 10 digits |
| `ticker` | string | Stock ticker | Yahoo Finance | may change over time — store historically if possible |
| `fiscal_year` | int | Fiscal year of observation | EDGAR | |
| `prediction_date` | date | Fiscal year-end / last available statement date | EDGAR | this is the no-look-ahead cutoff — **no feature may use data with a filing/effective date after this** |
| `going_private_event` | 0/1 | Broad outcome: any deduplicated 13E-3 event within horizon | EDGAR (SC 13E-3) | see design_memo §1 |
| `pe_backed_going_private` | 0/1 | Main outcome: PE involvement confirmed | keyword screen → LLM classification (planned) | see design_memo §1 |
| `event_date` | date | Date of the (first, deduplicated) 13E-3 filing, if any | EDGAR | null if no event |
| `industry_sic` | string | Full SIC code | EDGAR | keep the specific code, not just a financial/non-financial flag |
| `financial_sector_60_64` | 0/1 | SIC 60–64 exclusion flag (main spec) | derived from `industry_sic` | design_memo §3, variant A |
| `financial_sector_60_67` | 0/1 | SIC 60–67 exclusion flag (robustness) | derived from `industry_sic` | design_memo §3, variant B |
| `domestic_issuer` | 0/1 | Files 10-K, never 20-F | EDGAR submissions API | |
| `data_available` | 0/1 | Sufficient financial data to compute the full feature set this year | derived | exclusion reason should be logged, not just dropped silently |
| `source_filing` | string | Filing accession number backing this row's event label (if any) | EDGAR | for traceability |

### SIC code reference (for the financial-sector flags above)

| SIC Range | Category |
|---|---|
| 60 | Depository institutions |
| 61 | Non-depository credit institutions |
| 62 | Security and commodity brokers |
| 63 | Insurance carriers |
| 64 | Insurance agents |
| 65 | Real estate |
| 67 | Holding and investment offices |

---

## Feature tables (to be added as they're built)

Each feature needs, at minimum: **formula**, **raw data source**, **point in time used (relative to `prediction_date`)**, **transformation** (e.g. winsorized, log), and **missing-value rule**. Example row format (from `design_memo.md` discussion, to be populated):

| Variable | Formula | Raw Source | Timing | Transformation | Missing Rule |
|---|---|---|---|---|---|
| Leverage | Debt / Assets | EDGAR | prediction_date | winsorized | TBD |
| Cash ratio | Cash / Assets | EDGAR | prediction_date | winsorized | TBD |
| Altman Z'' | (formula) | EDGAR | prediction_date | non-linear composite | TBD — min. data requirement |
| Institutional ownership | Ownership % / shares outstanding | DEF 14A | prediction_date | possibly lagged | TBD — missing = unknown? |

### Features decided so far (formulas final; extraction script not yet written)

| Variable | Formula | Raw Source | Timing | Transformation | Missing Rule |
|---|---|---|---|---|---|
| `total_debt` | Traditional interest-bearing debt: current + non-current bank/line-of-credit borrowings, notes, bonds, convertibles. **Finance and operating leases are excluded** (consistent with Jiang 2019 / Bos et al. 2024). Priority waterfall over standard us-gaap tags (`src/13c_debt_three_state.py`), so approximate | DERA num.txt, non-dimensional standard-tag facts | Balance-sheet date of the 10-K (= prediction date) | Scale by assets for leverage; winsorize | Three-state rule below |
| `no_debt_tag` | 1 if the filing has no debt-like fact of any kind (then `total_debt` = 0) | same | same | binary | never missing |
| `debt_uncertain` | 1 if debt-like facts > 0 exist only under a dimension member or a filer-custom tag, so no consolidated total is available (then `total_debt` is missing) | num.txt incl. dimensional/custom rows | same | binary | never missing |
| `debt_includes_leases` | 1 if `total_debt` came from a combined debt-and-capital-lease tag (fallback only) | same | same | binary | never missing |
| `log_float` | ln(`entity_public_float`) - the size variable | EntityPublicFloat (`src/11b_public_float.py`; `entity_public_float`, `float_date`) | Float date (end of the firm's 2nd fiscal quarter), which precedes the filing - no look-ahead | Natural log | Missing for first-listing-year rows with float = 0 |
| `float_to_book`, `float_to_earnings` | `entity_public_float` / stockholders' equity; `entity_public_float` / net income - float-based valuation multiples (float is the market value of non-affiliate shares, so it understates true market cap by the affiliate stake) | float + DERA equity / net income at the balance-sheet date | Float date vs. balance-sheet date (float is measured about 6 months before fiscal year-end; same convention for every firm) | Raw ratio (winsorize in the modeling stage, thresholds fitted on training data only) | Non-positive denominators are not left missing: each ratio comes with a negative dummy (`neg_book_dummy`, `neg_earnings_dummy`) and the dummy x ratio interaction, following Jiang (2019), who includes P/B together with a negative-P/B dummy (`negPtoB`) and the `negPtoB` x `PtoB` interaction rather than dropping negative-book firms. Undefined only when the denominator is exactly 0 or float = 0 |

**Market value: `EntityPublicFloat` is the primary measure; true market cap is not available (documented limitation).** True market cap (price x shares) would need a paid or delisted-history source: Yahoo Finance systematically drops delisted and acquired tickers (1 of 115 known sponsor_like_ptp firms had a Yahoo price at its float date; Panera and the original Dell are absent), so a Yahoo-based market cap would be missing almost only for targets and leak the outcome. Consequently the insider/affiliate-ownership-concentration proxy (which needed market cap vs. float) is dropped. If Católica confirms WRDS/CRSP access, recomputing the valuation multiples with true market cap is a planned robustness check, not part of the main pipeline.

### Fundamentals table: `data/processed/firm_year_fundamentals.csv` (`src/14_build_fundamentals.py`)

One row per panel firm-year (72,161), joined to the panel on `accession`. Raw accounting concepts only, no ratios yet. All facts are annual, non-dimensional, standard us-gaap tags, USD, dated at the 10-K's period end (= prediction date). "Coverage" is the share of rows with a value before any fill.

| Group | Columns | Coverage / rule |
|---|---|---|
| Keys, meta | `cik`, `company_name`, `sic`, `afs`, `fiscal_year`, `form`, `filed_date`, `accession`, `period_end`, `fye`, `fin_sic_60_64`, `fin_sic_60_67` | complete |
| Size / market value | `entity_public_float`, `float_date`, `log_float` | float-based; `log_float` missing where float = 0 (first-listing-year rows) |
| Balance sheet | `total_assets` (99.0%), `assets_current` (79.2%; 95.7% non-fin), `liabilities_current` (79.1%), `cash` (97.5%), `equity` (97.0%), `total_liabilities` (98.5%, `liabilities_derived` = 1 when computed as assets - equity), `retained_earnings` (92.3%), `goodwill` (45.9%), `intangibles_ex_goodwill` (33.3%), `inventory` (41.5%), `receivables` (49.7%), `payables` (56.8%) | goodwill / intangibles / inventory / receivables / payables absent = not reported, left missing |
| Income statement | `revenue`, `net_income` (98.3%), `operating_income` (75.6%; 88.4% non-fin), `pretax_income` (73.8%), `income_tax` (71.8%), `cost_of_revenue` (48.7%), `gross_profit` (50.6%, `gross_profit_derived`), `sga_expense` (79.0%, `sga_expense_source`), `interest_expense` (77.5%, `interest_expense_source` = expense / cash_paid), `depreciation_amortization` (82.0%), `rd_expense` | missing left missing except revenue and R&D (see below) |
| Cash flow / PP&E | `operating_cash_flow` (97.5%), `capex` (87.2% after fill; `capex_source`, `capex_zero_fill`), `ppe_net` (`ppe_net_source`, `ppe_net_derived`) | capex = 0 with `capex_zero_fill` = 1 where capex is untagged and the firm has no PP&E (`ppe_no_tag` = 1), 15.0% of rows; firms with PP&E but no capex tag stay missing |
| Debt | `total_debt`, `debt_state`, `no_debt_tag`, `debt_uncertain`, `debt_includes_leases` | three-state rule below; 97.7% non-missing |
| Zero-fill indicators | `revenue_no_tag`, `ppe_no_tag`, `rd_no_tag`, `revenue_zero_suspect`, `ppe_zero_suspect` | see below |

**Zero-fill with indicators (decision 2026-09-21): `revenue`, `ppe_net`, `rd_expense`.** When no tag is found the value is 0 and the matching `*_no_tag` = 1. Revenue and PP&E first use industry-specific fallback tags (`revenue_source`, `ppe_net_source` = std / industry: real estate, oil and gas, brokerage, regulated utilities, insurance, REIT property), which cut the revenue gap from 24.8% to 15.3% of rows. Remaining zero-fills: revenue 15.3% (14.1% non-financial), PP&E 17.5%, R&D 68.5%. Checked against other facts, some zero-fills are probably wrong: `revenue_zero_suspect` = no revenue tag but cost of revenue or inventory reported (1.2% of rows); `ppe_zero_suspect` = no PP&E tag but capex reported (2.0%). These are flagged, not corrected. Note the zero-fill indicators are strongly outcome-related (`revenue_no_tag`: 2.1% among h=3 positives vs 15.5% among negatives), which mostly reflects that the population includes shells, blank-check and pre-revenue firms that are never LBO targets; treat the indicators as informative features but check them in robustness runs.

**Debt three-state rule (verified against actual balance sheets of the sponsor_like_ptp firms).** (1) A non-dimensional standard debt tag gives total debt > 0: use the value. (2) No debt-like fact at all: `total_debt` = 0 and `no_debt_tag` = 1 (a reported zero, e.g. "Revolving line of credit: 0", also gives 0 but `no_debt_tag` = 0). (3) Debt-like fact only under a dimension or custom tag: `total_debt` missing and `debt_uncertain` = 1. A plain zero-fill was rejected because it produced false zeros for ~22% of the zero-filled firms in the check. Panel-wide state mix: value 70.9%, no_debt_tag 25.0%, uncertain 2.3%, explicit zero 1.8%. **Audit fix (2026-09-21):** the standard total tag `ConvertibleDebt` is now used when no current / non-current convertible split exists (262 firm-years). **Scope decision (2026-09-21):** related-party loans and shareholder advances tagged `DueToRelatedParties*` are NOT counted as debt: they are typically informal, often interest-free insider loans, economically different from market debt (no third-party credit assessment, no market interest rate), and Jiang (2019) / Bos et al. (2024) define leverage around traditional interest-bearing debt; same kind of decision as the finance-lease exclusion. Formal notes payable to related parties (`NotesPayableRelatedParties*`) stay in the waterfall. Effect seen in the audit: Tengjun and Remsleep understate debt by construction. **Known limitation:** finance leases are not counted as debt (e.g. Mindbody's $14.9M finance-lease obligation gives `total_debt` = 0); 6,290 firm-years use a combined debt-and-capital-lease tag and are flagged `debt_includes_leases`. **Correction (2026-09-21):** these combined tags (`LongTermDebtAndCapitalLease...`) were first used only as a last resort, which understated debt by ~99% for firms that also had a small separate short-term tag (Heinz FY2012: $46M instead of $5.0B). Found when a validation run showed Heinz leverage of 0.004; after the fix 2,908 firm-years changed (1,494 by more than 10x).

### Ratios: `data/processed/firm_year_ratios.csv` (`src/15_compute_ratios.py`)

Raw, unwinsorized. Lags are keyed on `period_end` (prior observation 335-395 days earlier), not on `fiscal_year`. Full formulas and rules are in the script docstring.

| Ratio | Formula | Notes |
|---|---|---|
| `leverage` | `total_debt` / `total_assets` | missing when `debt_uncertain` = 1 |
| `cash_ratio` | `cash` / `total_assets` | |
| `roa` | `net_income` / `total_assets` | year-end assets |
| `fcf`, `fcf_to_assets` | `operating_cash_flow` - `capex`; divided by assets | missing if capex or operating cash flow missing |
| `fcf_vol`, `fcf_vol_2y_fallback` | sample std of `fcf_to_assets` over years t, t-1, t-2 | after Jiang's `FCFFvol`, scaled by assets. When only t and t-1 are available the std of those two is used and `fcf_vol_2y_fallback` = 1 (noisier; use the flag in robustness runs). Coverage 67.6% (strict 3-year would be 48.7%); 19% of the non-missing values are fallbacks |
| `interest_coverage` | `operating_income` / `interest_expense` | only where interest expense > 0; raw (winsorize in the modeling stage, thresholds fitted on training data only) |
| `no_interest_expense` | 1 if interest expense is 0, or untagged while debt is zero (`no_debt_tag` = 1 or a reported zero) | economic "no debt, no interest"; 93.7% overlap with `no_debt_tag` = 1; 17.6% of rows |
| `interest_data_missing` | 1 if interest expense is untagged although the firm has or may have debt | a data gap, not an economic zero; 9.1% of rows. The two flags are mutually exclusive, both 0 when interest > 0 (73.3%) |
| `ppe_to_assets` | `ppe_net` / `total_assets` | Jiang `PPEpct`; `ppe_net` is zero-filled (`ppe_no_tag`) |
| `rd_to_revenue` | `rd_expense` / `revenue` | Jiang `RDpct`; defined only where revenue > 0, so pre-revenue R&D firms are missing because revenue is zero-filled (`revenue_no_tag`) - use `rd_to_assets` for them |
| `rd_to_assets` | `rd_expense` / `total_assets` | defined for pre-revenue firms as well |
| `log_assets` | ln(`total_assets`) | size |
| `current_ratio` | `assets_current` / `liabilities_current` | 77.8% coverage (94.3% non-financial) |
| `log_float` | ln(float) | size |
| `float_to_book`, `neg_book_dummy`, `neg_book_x_float_to_book` | see above | Jiang negPtoB precedent |
| `float_to_earnings`, `neg_earnings_dummy`, `neg_earnings_x_float_to_earnings` | see above | |
| `altman_z2` | 6.56 X1 + 3.26 X2 + 6.72 X3 + 1.05 X4; X1 = (current assets - current liabilities)/assets, X2 = retained earnings/assets, X3 = EBIT/assets, X4 = float / total liabilities | Altman Z'' for non-manufacturers, no constant; float replaces market value of equity; missing for SIC 60-64 |
| `altman_z2_book` | same with X4 = book equity / total liabilities | Altman's book-equity form, robustness variant |
| `sales_growth_1y` | revenue_t / revenue_(t-1) - 1 | only where both years' revenue is real (not zero-filled) |

The ratios file (`firm_year_ratios.csv`, 72,161 rows x 46 columns) also carries the key columns, `afs`, the SIC financial flags, `entity_public_float` and the fill/indicator flags (`no_debt_tag`, `debt_uncertain`, `debt_includes_leases`, `revenue_no_tag`, `ppe_no_tag`, `rd_no_tag`, `revenue_zero_suspect`, `ppe_zero_suspect`, `capex_zero_fill`, `liabilities_derived`); labels stay in `firm_year_panel.csv` (join on `accession`).

**Reading the missing-indicators (modeling stage).** The baseline and Elastic Net add a missing-indicator for any ratio that is missing for more than 2% of training rows. These indicators mostly measure observability, not economics: `sales_growth_1y_missing` (31.5% of training rows; 77% of those have no consecutive prior-year filing, 52% are FY2012 where history is structurally impossible, 19% have zero-filled revenue) and `fcf_vol_missing` (FY2012-2013 have no volatility history) are missingness / observability proxies for short or incomplete reporting history, not growth or volatility signals. They are kept in the models and annotated, not dropped. The Random Forest uses NaN natively and no indicators.

Not built (documented gaps): analyst-coverage growth (no data source available), ownership concentration (dropped with the market-cap decision).

See `PROGRESS.md` → Literature Review → "Recommended covariate structure" for the full candidate variable list before Lasso/Elastic Net selection.
