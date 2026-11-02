# Descriptive statistics, training sample FY2012–2017 (raw values before winsorising and imputation)

| variable | group | N | N total | mean | SD | P25 | median | P75 |
|---|---|---|---|---|---|---|---|---|
| Return on equity | Targets | 138 | 167 | -0.082 | 1.132 | -0.057 | 0.050 | 0.130 |
| Return on equity | Non-targets | 15,716 | 17,891 | -0.458 | 18.157 | -0.079 | 0.069 | 0.150 |
| Sales growth (1y) | Targets | 130 | 167 | 0.147 | 0.389 | -0.003 | 0.076 | 0.208 |
| Sales growth (1y) | Non-targets | 12,239 | 17,891 | 1.035 | 30.981 | -0.025 | 0.054 | 0.167 |
| Liquidity | Targets | 149 | 167 | -0.087 | 0.190 | -0.166 | -0.085 | -0.013 |
| Liquidity | Non-targets | 15,708 | 17,891 | -23.444 | 1,938.5 | -0.172 | -0.076 | 0.027 |
| Leverage | Targets | 163 | 167 | 0.265 | 0.238 | 0.057 | 0.233 | 0.416 |
| Leverage | Non-targets | 17,467 | 17,891 | 30.774 | 3,131.3 | 0.006 | 0.208 | 0.386 |
| Growth–resource imbalance | Targets | 117 | 167 | 0.137 | 0.345 | 0.000 | 0.000 | 0.000 |
| Growth–resource imbalance | Non-targets | 10,883 | 17,891 | 0.266 | 0.442 | 0.000 | 0.000 | 1.000 |
| Industry PE activity | Targets | 167 | 167 | 0.329 | 0.471 | 0.000 | 0.000 | 1.000 |
| Industry PE activity | Non-targets | 17,890 | 17,891 | 0.263 | 0.440 | 0.000 | 0.000 | 1.000 |
| Log total assets | Targets | 164 | 167 | 20.784 | 1.485 | 19.755 | 20.493 | 21.718 |
| Log total assets | Non-targets | 17,727 | 17,891 | 20.382 | 2.714 | 19.222 | 20.691 | 22.024 |
| Float / book | Targets | 147 | 167 | 3.894 | 16.605 | 0.850 | 1.611 | 3.098 |
| Float / book | Non-targets | 16,342 | 17,891 | 109,757.6 | 13,307,697.7 | 1.076 | 1.924 | 3.530 |
| Float / earnings | Targets | 160 | 167 | -40.439 | 575.9 | -5.063 | 11.665 | 24.909 |
| Float / earnings | Non-targets | 16,793 | 17,891 | 3,436.7 | 336,497.3 | -4.104 | 11.452 | 21.358 |
| Free cash flow / assets | Targets | 143 | 167 | 0.037 | 0.112 | 0.002 | 0.050 | 0.089 |
| Free cash flow / assets | Non-targets | 15,776 | 17,891 | -7.248 | 449.9 | -0.032 | 0.035 | 0.083 |
| Cash flow volatility | Targets | 113 | 167 | 0.040 | 0.079 | 0.011 | 0.027 | 0.041 |
| Cash flow volatility | Non-targets | 11,828 | 17,891 | 2.275 | 135.3 | 0.014 | 0.029 | 0.062 |
| Interest coverage | Targets | 141 | 167 | -23.468 | 234.9 | -1.553 | 2.580 | 6.741 |
| Interest coverage | Non-targets | 12,200 | 17,891 | 326.3 | 54,413.1 | -1.352 | 3.145 | 11.928 |
| PP&E / assets | Targets | 164 | 167 | 0.253 | 0.240 | 0.067 | 0.160 | 0.370 |
| PP&E / assets | Non-targets | 17,727 | 17,891 | 0.262 | 0.280 | 0.046 | 0.141 | 0.408 |
| R&D / assets | Targets | 164 | 167 | 0.036 | 0.070 | 0.000 | 0.000 | 0.044 |
| R&D / assets | Non-targets | 17,727 | 17,891 | 0.090 | 1.358 | 0.000 | 0.000 | 0.052 |
| Cash ratio | Targets | 162 | 167 | 0.127 | 0.147 | 0.018 | 0.078 | 0.167 |
| Cash ratio | Non-targets | 17,541 | 17,891 | 0.167 | 0.214 | 0.026 | 0.088 | 0.211 |
| Altman Z''-score | Targets | 130 | 167 | 4.598 | 6.558 | 1.133 | 2.711 | 5.433 |
| Altman Z''-score | Non-targets | 13,893 | 17,891 | 2,258.6 | 161,910.0 | 1.342 | 4.122 | 7.821 |

- Training sample after the sample filters: 18,058 firm-years, 167 targets (PE take-private within 3 years, pe_main) and 17,891 non-targets.
- N = firm-years with a non-missing value; N total = all firm-years in the group.
- Growth–resource imbalance and industry PE activity are 0/1 variables: the mean is the share of ones.
- Raw values include extreme ratios of firms with very small total assets, so means and SDs are driven by outliers; medians and quartiles are robust. The models use values winsorised at the training P1/P99 (Notebooks 04 and 06).
