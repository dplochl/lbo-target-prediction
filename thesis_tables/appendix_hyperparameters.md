# Hyperparameter grids and chosen values: Random forest and XGBoost

| model | max_depth | min_samples_leaf | learning_rate | n_estimators | validation log-loss | validation ROC-AUC (info only) | chosen |
|---|---|---|---|---|---|---|---|
| Random forest | None (unlimited) | 100 |  |  | 0.06304 | 0.6528 | yes |
| Random forest | 8 | 50 |  |  | 0.06308 | 0.6566 |  |
| Random forest | 8 | 100 |  |  | 0.06309 | 0.6536 |  |
| Random forest | 8 | 20 |  |  | 0.06316 | 0.6532 |  |
| Random forest | None (unlimited) | 50 |  |  | 0.06317 | 0.6506 |  |
| Random forest | 8 | 200 |  |  | 0.06327 | 0.6458 |  |
| Random forest | None (unlimited) | 20 |  |  | 0.06331 | 0.6497 |  |
| Random forest | None (unlimited) | 200 |  |  | 0.06331 | 0.6438 |  |
| Random forest | 4 | 100 |  |  | 0.06371 | 0.6380 |  |
| Random forest | 4 | 200 |  |  | 0.06373 | 0.6321 |  |
| Random forest | 4 | 20 |  |  | 0.06382 | 0.6402 |  |
| Random forest | 4 | 50 |  |  | 0.06386 | 0.6342 |  |
| XGBoost | 2 |  | 0.03 | 200 | 0.06446 | 0.6376 | yes |
| XGBoost | 3 |  | 0.03 | 200 | 0.06478 | 0.6502 |  |
| XGBoost | 4 |  | 0.03 | 200 | 0.06562 | 0.6578 |  |
| XGBoost | 2 |  | 0.03 | 500 | 0.06635 | 0.6442 |  |
| XGBoost | 3 |  | 0.03 | 500 | 0.06720 | 0.6655 |  |
| XGBoost | 2 |  | 0.1 | 200 | 0.06831 | 0.6307 |  |
| XGBoost | 4 |  | 0.03 | 500 | 0.07010 | 0.6704 |  |
| XGBoost | 3 |  | 0.1 | 200 | 0.07048 | 0.6544 |  |
| XGBoost | 4 |  | 0.1 | 200 | 0.07323 | 0.6887 |  |
| XGBoost | 2 |  | 0.1 | 500 | 0.07348 | 0.6527 |  |
| XGBoost | 3 |  | 0.1 | 500 | 0.08069 | 0.6817 |  |
| XGBoost | 4 |  | 0.1 | 500 | 0.08984 | 0.6950 |  |

- Chosen Random forest: {'max_depth': None, 'min_samples_leaf': 100}; fixed: n_estimators = 500, max_features = sqrt, no class weights, random_state = 20261030.
- Chosen XGBoost: {'max_depth': 2, 'learning_rate': 0.03, 'n_estimators': 200}; fixed: subsample = 0.8, colsample_bytree = 0.8, objective = binary:logistic, tree_method = hist, no scale_pos_weight, random_state = 20261030.
- Validation design: fit on FY2012–2015, validate on FY2016–2017 (training period only); criterion log-loss (lower is better).
- Reference log-loss (predicting the fit-period base rate for every row): 0.06493.
- Limitation: the 3-year label window makes fit and validation rows overlap (18 of 43 positive validation firms are also positive in the fit part).
