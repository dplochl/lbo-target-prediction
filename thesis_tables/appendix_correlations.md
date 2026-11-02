# Spearman rank correlations of the Model B variables, training sample FY2012–2017 (raw values)

| variable | Return on equity | Sales growth (1y) | Liquidity | Leverage | Growth–resource imbalance | Industry PE activity | Log total assets | Float / book | Float / earnings | Free cash flow / assets | Cash flow volatility | Interest coverage | PP&E / assets | R&D / assets | Cash ratio | Altman Z''-score |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Return on equity | 1.00 | 0.04 | -0.25 | 0.16 | -0.03 | -0.03 | 0.43 | 0.15 | 0.48 | 0.63 | -0.30 | 0.73 | 0.16 | -0.35 | -0.20 | 0.30 |
| Sales growth (1y) | 0.04 | 1.00 | 0.05 | -0.02 | -0.05 | 0.09 | -0.06 | 0.13 | 0.06 | 0.02 | 0.03 | 0.06 | -0.08 | 0.09 | 0.01 | 0.08 |
| Liquidity | -0.25 | 0.05 | 1.00 | -0.29 | 0.08 | 0.02 | -0.19 | 0.07 | -0.09 | -0.06 | 0.16 | -0.07 | -0.16 | 0.36 | 0.53 | 0.29 |
| Leverage | 0.16 | -0.02 | -0.29 | 1.00 | -0.04 | -0.06 | 0.39 | -0.11 | 0.07 | 0.03 | -0.21 | -0.19 | 0.30 | -0.32 | -0.43 | -0.47 |
| Growth–resource imbalance | -0.03 | -0.05 | 0.08 | -0.04 | 1.00 | 0.00 | -0.07 | -0.05 | -0.01 | -0.03 | 0.05 | -0.01 | -0.02 | 0.02 | 0.01 | 0.04 |
| Industry PE activity | -0.03 | 0.09 | 0.02 | -0.06 | 0.00 | 1.00 | -0.03 | 0.06 | 0.02 | 0.08 | -0.03 | -0.02 | -0.12 | 0.11 | 0.08 | 0.04 |
| Log total assets | 0.43 | -0.06 | -0.19 | 0.39 | -0.07 | -0.03 | 1.00 | -0.02 | 0.31 | 0.36 | -0.52 | 0.28 | 0.32 | -0.35 | -0.44 | -0.06 |
| Float / book | 0.15 | 0.13 | 0.07 | -0.11 | -0.05 | 0.06 | -0.02 | 1.00 | 0.15 | 0.15 | 0.02 | 0.11 | -0.12 | 0.22 | 0.19 | 0.28 |
| Float / earnings | 0.48 | 0.06 | -0.09 | 0.07 | -0.01 | 0.02 | 0.31 | 0.15 | 1.00 | 0.42 | -0.30 | 0.57 | 0.14 | -0.22 | -0.18 | 0.26 |
| Free cash flow / assets | 0.63 | 0.02 | -0.06 | 0.03 | -0.03 | 0.08 | 0.36 | 0.15 | 0.42 | 1.00 | -0.29 | 0.61 | 0.10 | -0.22 | -0.09 | 0.36 |
| Cash flow volatility | -0.30 | 0.03 | 0.16 | -0.21 | 0.05 | -0.03 | -0.52 | 0.02 | -0.30 | -0.29 | 1.00 | -0.24 | -0.15 | 0.32 | 0.35 | -0.04 |
| Interest coverage | 0.73 | 0.06 | -0.07 | -0.19 | -0.01 | -0.02 | 0.28 | 0.11 | 0.57 | 0.61 | -0.24 | 1.00 | 0.08 | -0.25 | -0.08 | 0.55 |
| PP&E / assets | 0.16 | -0.08 | -0.16 | 0.30 | -0.02 | -0.12 | 0.32 | -0.12 | 0.14 | 0.10 | -0.15 | 0.08 | 1.00 | -0.38 | -0.42 | -0.14 |
| R&D / assets | -0.35 | 0.09 | 0.36 | -0.32 | 0.02 | 0.11 | -0.35 | 0.22 | -0.22 | -0.22 | 0.32 | -0.25 | -0.38 | 1.00 | 0.52 | 0.08 |
| Cash ratio | -0.20 | 0.01 | 0.53 | -0.43 | 0.01 | 0.08 | -0.44 | 0.19 | -0.18 | -0.09 | 0.35 | -0.08 | -0.42 | 0.52 | 1.00 | 0.24 |
| Altman Z''-score | 0.30 | 0.08 | 0.29 | -0.47 | 0.04 | 0.04 | -0.06 | 0.28 | 0.26 | 0.36 | -0.04 | 0.55 | -0.14 | 0.08 | 0.24 | 1.00 |

- Pairwise complete observations; the smallest pair sample has 8,174 firm-years.
- Spearman correlations are computed on ranks and are therefore robust to the extreme raw values.
- Variable selection in Notebook 06 used |rho| > 0.7 on the prepared (winsorised and imputed) training data; return on assets and log public float were dropped there and are not shown.
- Return on equity / interest coverage: rho = 0.73 on the 11,045 firm-years with both values, but 0.58 on the prepared data used for the selection in Notebook 06 (median-filled missing coverage weakens the association).
