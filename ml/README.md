# Rental360 ML — Demand Forecasting & Anomaly Detection

> **ALL DATA IS SYNTHETIC.** Everything in this folder is built on generated
> demo data. Forecasts and anomaly flags are illustrative only and must never
> be presented as real business predictions.

## What it does

- **Demand forecasting** (`ml/train.py`, `ml/forecast.py`): predicts next
  month's `rental_count` for every (category, branch) pair, from the
  `mart_monthly_demand` warehouse view (1,895 rows, 2024-01–2026-10,
  8 categories × 8 branches, 62 observed pairs).
- **Utilization anomaly detection** (`ml/anomaly.py`): flags equipment whose
  `utilization_pct` is an outlier within its own category in
  `mart_equipment_utilization` (420 items, 12 retired — retired equipment is
  never flagged).

## Approach

### Forecasting

- **Target:** `rental_count` next month within each (category, branch) series.
- **Features** (all known at prediction time):
  - month-of-year cyclical encoding: `sin` / `cos` of month number
  - linear time trend: months since 2024-01
  - one-hot `category` + `branch_id`
  - lag-1 `rental_count` (previous month, same category + branch)
  - trailing 3-month mean `rental_count` (current month + previous two)
- **Missing months:** a missing (month, category, branch) row in the mart
  means no rentals were recorded, so each series is reindexed onto the full
  monthly grid with missing counts filled with 0 before lags / rolling means
  are computed.
- **Split:** time-based, no leakage — train on target months up to 2026-07,
  hold out the last 3 target months (2026-08, 2026-09, 2026-10) as test.
- **Models tried:** `GradientBoostingRegressor` and `RandomForestRegressor`
  (300 trees), each in a pipeline with one-hot encoding; the winner is picked
  by hold-out MAE. Predictions are clipped at 0 (counts can't be negative).

### Anomaly detection

Per category, compute mean and standard deviation of `utilization_pct` over
non-retired equipment. Flag `z < -2` as `idle_outlier` and `z > +2` as
`hot_outlier`, where `z = (utilization − category_mean) / category_std`.
Categories with fewer than 2 active items or zero std produce no flags.

## Metrics (hold-out, target months 2026-08–2026-10, n = 186)

| Model | MAE | RMSE |
|---|---|---|
| GradientBoostingRegressor | 2.0054 | 2.5587 |
| **RandomForestRegressor (selected)** | **2.0038** | **2.5899** |

The two models are effectively tied on MAE; RandomForest wins by 0.0016.
Per-category MAE (selected model): Compactor 1.02, Aerial Lift 1.75,
Bulldozer 1.78, Truck & Trailer 1.92, Crane 1.93, Excavator 2.29,
Forklift 2.59, Generator 2.73. Mean monthly rentals per pair are ~3.8, so an
MAE of ~2.0 is weak — the synthetic series are noisy and the history is
short (see Limitations). Top feature: trailing 3-month mean (importance
0.36), then time trend (0.10) and lag-1 (0.09).

Latest forecast (2026-11, 62 pairs, predicted rentals 1.4–7.8). Top 3:

| Category | Branch | Predicted rentals |
|---|---|---|
| Forklift | BR-02 | 7.8 |
| Generator | BR-02 | 6.2 |
| Excavator | BR-05 | 6.0 |

Anomalies on current data: **19 total — 19 hot outliers, 0 idle outliers**
(e.g. EQ-1258 Kohler 60REOZ, Generator BR-01, 87.0%, z = 5.06;
EQ-1007 John Deere 130G, Excavator BR-05, 76.0%, z = 3.87). No idle
outliers: utilization is right-skewed, so `mean − 2·std` is below 0 for
most categories and no active item falls under it.

## How to retrain / run

From the repo root (uses the project venv):

```bash
.venv/bin/python ml/train.py      # trains, writes ml/artifacts/*
.venv/bin/python ml/anomaly.py    # writes ml/artifacts/anomalies.json
.venv/bin/pytest tests/test_forecast.py
```

Artifacts in `ml/artifacts/`:

- `demand_model.pkl` — fitted pipeline (preprocessing + model)
- `metrics.json` — model name, MAE/RMSE, per-category MAE, date ranges,
  feature list, n_train/n_test, model comparison
- `latest_forecast.json` — 2026-11 predictions, one per (category, branch)
- `feature_importances.json` — feature importances of the selected model
- `anomalies.json` — utilization outliers

`ml/forecast.py` helpers (import only — no retraining on import):

```python
from ml.forecast import load_model, predict_next_month, get_metrics
predict_next_month(category="Excavator", branch_id="BR-05")
get_metrics()["mae"]
```

`ml/train.py` exits non-zero on failure.

## Limitations

- **Synthetic data:** all inputs are generated demo data; forecasts are not
  real business predictions and must not be used for purchasing, staffing,
  or pricing decisions.
- **Short history:** only 34 months (2024-01–2026-10), so the model sees
  fewer than three full seasonal cycles; the time-based hold-out is just
  3 months × 62 pairs.
- **No external drivers:** no weather, seasonality beyond month-of-year,
  economic indicators, pricing, promotions, or maintenance schedules are
  modelled — the features are purely the series' own history plus
  category/branch identity.
- **Noisy target:** monthly counts per pair average ~3.8 with a long tail
  (max 18), so hold-out MAE ≈ 2.0 is large relative to the signal; the two
  candidate models are statistically indistinguishable here.
- **Zero-fill assumption:** months absent from the mart are treated as zero
  rentals; if a missing row instead meant missing data, lags and rolling
  means would be biased downward for sparse pairs.
- **Anomaly detection is univariate:** a single z-score per category, so
  genuinely unusual items can hide in high-variance categories and
  high-utilization items in low-variance categories can be over-flagged.
