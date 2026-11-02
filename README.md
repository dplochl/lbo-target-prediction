# LBO / Take-Private Target Prediction

Code for a master's thesis (MSc Business Analytics, Católica Lisbon): which characteristics of publicly listed US companies predict becoming the target of a private-equity take-private?

The thesis applies the six hypotheses of Palepu (1986, *Journal of Accounting and Economics* 8(1)) to US PE take-privates 2012–2025, built entirely from free SEC data:

- **Model A:** Palepu logit (ROE, sales growth, liquidity, leverage, growth–resource imbalance, industry PE activity, size, float/book, float/earnings), with variant A2.
- **Model B:** extended logit (adds free cash flow, cash-flow volatility, interest coverage, PP&E, R&D, cash ratio, Altman Z''), with variant B2 (quadratic size).
- **Model C:** Random forest and XGBoost on the Model B variables, with SHAP.
- Temporal split: training FY2012–2017, embargo FY2018–2019, test FY2020–2022; natural base rate, firm-clustered standard errors, firm bootstrap.

## Data

- **SEC EDGAR** — SC 13E-3 and merger proxies (DEFM14A/PREM14A/DEFM14C/PREM14C) for the event universe, Form 25 for completion, submissions API, XBRL frames / Company Facts (`EntityPublicFloat`).
- **SEC Financial Statement Data Sets (DERA)** — `sub.txt` / `num.txt` for the firm-year population and the accounting variables.
- SEC EDGAR data are U.S. government works and in the **public domain**. They were accessed 17–22 September 2026; the read-only EDGAR check of the watchlist was run on 1 November 2026.
- **Buyer labels** (PE sponsor / strategic / family / management) were produced by an LLM, **Claude Haiku 4.5** (`claude-haiku-4-5-20251001`, Anthropic), from the filing text (`src/05_buyer_classification.py`), with mandatory literal quotes and an abstain option; validated against a 30-case hand-coded gold standard (22 of 22 committed labels correct).

`data/` is not part of the repository. The data used for the results (`data/interim/` and `data/processed/`) are available as a release asset:

- **Download:** [data_backup_2026-09-23.zip](https://github.com/dplochl/lbo-target-prediction/releases/download/v1.0/data_backup_2026-09-23.zip) (release [v1.0](https://github.com/dplochl/lbo-target-prediction/releases/tag/v1.0), 85 MB)
- **SHA-256:** `366c1279f84b257b548cffcd76effb4a5ce5db5deb1fc2f60210a92351010be7`

Unzip it in the repo root (it creates `data/interim/` and `data/processed/`). The LLM classification cannot be reproduced bit for bit, so this archive is the reference copy.

## Repo structure

```
├── src/                 # data pipeline: SEC download -> events -> firm-year panel -> fundamentals -> ratios
├── analysis/            # notebooks 01–10 (sample, Palepu variables, Models A/B/C, robustness, applications, exports)
├── thesis_figures/      # Figures 4.1–4.5 (written by analysis/10_thesis_exports.ipynb)
├── thesis_tables/       # appendix tables, CSV + Markdown (written by analysis/10_thesis_exports.ipynb)
├── results/             # extreme-row audit (validation of the size screen)
├── docs/                # data dictionary
└── data/                # raw / interim / processed (not in the repo, see above)
```

Code comments sometimes refer to `PROGRESS.md` or `design_memo.md`; these are internal project notes and not part of this repository. Notebook text is in German, figures and tables are in English.

## Setup

```bash
python3.13 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in the values
```

- `SEC_USER_AGENT` — the SEC requires an identifying User-Agent (project name + contact email) on every EDGAR request. Used by the pipeline scripts and by notebooks 09/10 (read via `src/edgar_utils.get_user_agent()`).
- `ANTHROPIC_API_KEY` — only for the LLM stages of the pipeline (`src/05`, `06`, `07`, `09`, `10`).

## Reproducing the results

1. **Data:** either download and unzip the release asset above, or run the pipeline in `src/` in numeric order (01 → 15, then 19 and 19b; `audit_deal_coverage.py` is a separate validation). The full pipeline downloads several GB from the SEC and takes hours.
2. **Analysis:** run `analysis/01` → `10` in order (`04` writes `data/processed/model_data_palepu.csv`, used by 05–07). From `analysis/`:
   ```bash
   jupyter nbconvert --to notebook --execute --inplace 04_preprocessing.ipynb
   ```
   `10_thesis_exports.ipynb` runs 05–09 unchanged and writes `thesis_figures/` and `thesis_tables/`. Notebooks 09 and 10 query SEC EDGAR (read-only) for the watchlist check, so the check date in the watchlist tables is the date of the run.

## Licence

The code is released under the [MIT License](./LICENSE). SEC data are in the public domain; the buyer labels in the data archive were generated with Claude Haiku 4.5 as described above.
