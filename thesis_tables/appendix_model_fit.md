# Model fit statistics (training FY2012–2017)

| model | N (firm-years) | positives | firms (clusters) | parameters incl. constant | McFadden pseudo-R2 | log-likelihood | log-likelihood (constant only) | LR chi2 | LR p-value |
|---|---|---|---|---|---|---|---|---|---|
| Model A | 18058 | 167 | 4763 | 16 | 2.03% | -929.1 | -948.3 | 38.5 | 0.0008 |
| Variant A2 | 18058 | 167 | 4763 | 17 | 2.10% | -928.4 | -948.3 | 39.8 | 0.0008 |
| Model B | 18058 | 167 | 4763 | 26 | 4.55% | -905.2 | -948.3 | 86.3 | <0.0001 |
| Variant B2 | 18058 | 167 | 4763 | 27 | 5.43% | -896.9 | -948.3 | 102.9 | <0.0001 |

- Estimation sample: training period FY2012–2017; outcome y_pe_main_h3 (PE take-private within 3 years).
- Unpenalised, unweighted logit (statsmodels, Newton); standard errors clustered by firm (CIK).
