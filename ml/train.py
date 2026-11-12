"""Demand forecasting for Rental360.

ALL DATA IS SYNTHETIC. Forecasts produced here are illustrative only and
must never be presented as real business predictions.

Approach
--------
Monthly rental counts from the ``mart_monthly_demand`` warehouse view are
modelled per (category, branch) series. The target is next month's
``rental_count``. Features are known at prediction time:

* month-of-year cyclical encoding (sin / cos of month number)
* linear time trend (months since 2024-01)
* one-hot category + branch
* lag-1 ``rental_count`` (previous month, same category + branch)
* trailing 3-month mean ``rental_count`` (current month + previous two)

Missing (month, category, branch) rows in the mart mean no rentals were
recorded that month, so the series are reindexed onto the full monthly grid
with missing counts filled with 0 before lags / rolling means are computed.

Time-based split: the last 3 observed target months are held out as test;
everything earlier is training. Two candidate regressors
(GradientBoostingRegressor, RandomForestRegressor) are tried and the one
with the lower hold-out MAE wins. Predictions are clipped at 0 (counts
cannot be negative).

Run from the repo root::

    python ml/train.py
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

REPO_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = REPO_ROOT / "data" / "warehouse" / "rental360.db"
ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

NUMERIC_FEATURES = ["month_sin", "month_cos", "time_idx", "lag_1", "rolling_3m"]
CATEGORICAL_FEATURES = ["category", "branch_id"]
FEATURES = NUMERIC_FEATURES + CATEGORICAL_FEATURES

EXPECTED_COLUMNS = [
    "year_month",
    "category",
    "branch_id",
    "rental_count",
    "total_rental_days",
    "total_revenue",
]


def load_monthly_demand(db_path: Path = DB_PATH) -> pd.DataFrame:
    """Load ``mart_monthly_demand`` from the SQLite warehouse.

    ALL DATA IS SYNTHETIC.
    """
    if not Path(db_path).exists():
        raise FileNotFoundError(f"Warehouse not found: {db_path}")
    with sqlite3.connect(db_path) as con:
        df = pd.read_sql_query("SELECT * FROM mart_monthly_demand", con)
    missing = [c for c in EXPECTED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"mart_monthly_demand missing columns: {missing}")
    if df.empty:
        raise ValueError("mart_monthly_demand is empty")
    df = df.sort_values(["category", "branch_id", "year_month"]).reset_index(drop=True)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """Build the modelling frame with features and next-month target.

    Reindexes each (category, branch) series onto the full monthly grid,
    filling missing months with rental_count 0 (no rentals recorded).
    Drops rows where lag / rolling / target cannot be computed.
    Returns a frame with the feature columns, ``target`` (next month's
    rental_count) and ``target_month``.
    """
    df = df.copy()
    df["period"] = pd.PeriodIndex(df["year_month"], freq="M")
    months = pd.period_range(df["period"].min(), df["period"].max(), freq="M")
    pairs = df[["category", "branch_id"]].drop_duplicates()
    pair_list = list(zip(pairs["category"], pairs["branch_id"]))
    index = pd.MultiIndex.from_tuples(
        [(c, b, m) for c, b in pair_list for m in months],
        names=["category", "branch_id", "period"],
    )
    full = (
        df.set_index(["category", "branch_id", "period"])[["rental_count"]]
        .reindex(index)
        .reset_index()
    )
    full["rental_count"] = full["rental_count"].fillna(0).astype(float)
    full = full.sort_values(["category", "branch_id", "period"]).reset_index(drop=True)

    full["month_num"] = full["period"].dt.month
    full["month_sin"] = np.sin(2 * np.pi * full["month_num"] / 12)
    full["month_cos"] = np.cos(2 * np.pi * full["month_num"] / 12)
    full["time_idx"] = (full["period"] - months[0]).map(lambda p: p.n).astype(int)
    grouped = full.groupby(["category", "branch_id"])["rental_count"]
    full["lag_1"] = grouped.shift(1)
    full["rolling_3m"] = grouped.transform(
        lambda s: s.rolling(window=3, min_periods=3).mean()
    )
    full["target"] = grouped.shift(-1)
    full["target_month"] = full.groupby(["category", "branch_id"])["period"].shift(-1)
    full["year_month"] = full["period"].dt.strftime("%Y-%m")
    full["target_month"] = full["target_month"].dt.strftime("%Y-%m")

    out = full.dropna(subset=["lag_1", "rolling_3m", "target", "target_month"]).copy()
    out["target"] = out["target"].astype(float)
    return out.reset_index(drop=True)


def time_split(frame: pd.DataFrame, n_test_months: int = 3):
    """Time-based split with no leakage: test = last ``n_test_months`` target months."""
    target_months = sorted(frame["target_month"].unique())
    if len(target_months) <= n_test_months:
        raise ValueError("Not enough months for a time-based split")
    test_months = target_months[-n_test_months:]
    test_mask = frame["target_month"].isin(test_months)
    return frame[~test_mask].copy(), frame[test_mask].copy(), test_months


def _make_pipeline(model) -> Pipeline:
    preprocessor = ColumnTransformer(
        transformers=[
            ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
        ],
        remainder="passthrough",
    )
    return Pipeline([("preprocess", preprocessor), ("model", model)])


def train_and_select(X_train, y_train, X_test, y_test):
    """Fit both candidates, score on the hold-out, return the winner.

    Returns (best_name, best_pipeline, comparison dict, predictions).
    Predictions are clipped at 0 because rental counts cannot be negative.
    """
    candidates = {
        "GradientBoostingRegressor": GradientBoostingRegressor(random_state=42),
        "RandomForestRegressor": RandomForestRegressor(
            n_estimators=300, random_state=42, n_jobs=-1
        ),
    }
    comparison = {}
    best_name, best_pipe, best_pred, best_mae = None, None, None, math.inf
    for name, model in candidates.items():
        pipe = _make_pipeline(model)
        pipe.fit(X_train, y_train)
        pred = np.clip(pipe.predict(X_test), 0, None)
        mae = mean_absolute_error(y_test, pred)
        rmse = math.sqrt(mean_squared_error(y_test, pred))
        comparison[name] = {"mae": round(float(mae), 4), "rmse": round(float(rmse), 4)}
        if mae < best_mae:
            best_name, best_pipe, best_pred, best_mae = name, pipe, pred, mae
    return best_name, best_pipe, comparison, best_pred


def build_forecast_rows(raw: pd.DataFrame, pipeline, forecast_month: str) -> list[dict]:
    """Predict the month after the last observed month for every pair.

    Features are taken from the last observed month (2026-10): lag-1 is the
    2026-09 count and the rolling 3-month mean covers 2026-08..2026-10.
    """
    raw = raw.copy()
    raw["period"] = pd.PeriodIndex(raw["year_month"], freq="M")
    last_period = raw["period"].max()
    months = pd.period_range(raw["period"].min(), last_period, freq="M")
    pairs = raw[["category", "branch_id"]].drop_duplicates()
    pair_list = list(zip(pairs["category"], pairs["branch_id"]))
    index = pd.MultiIndex.from_tuples(
        [(c, b, m) for c, b in pair_list for m in months],
        names=["category", "branch_id", "period"],
    )
    full = (
        raw.set_index(["category", "branch_id", "period"])[["rental_count"]]
        .reindex(index)
        .reset_index()
    )
    full["rental_count"] = full["rental_count"].fillna(0).astype(float)
    model_name = type(pipeline.named_steps["model"]).__name__

    rows = []
    for (category, branch_id), series in full.groupby(["category", "branch_id"]):
        series = series.sort_values("period")
        last_three = series["rental_count"].tail(3).tolist()
        if len(last_three) < 3:
            continue
        forecast_period = last_period + 1
        month_num = forecast_period.month
        time_idx = (forecast_period - months[0]).n
        feat = pd.DataFrame(
            [
                {
                    "month_sin": math.sin(2 * math.pi * month_num / 12),
                    "month_cos": math.cos(2 * math.pi * month_num / 12),
                    "time_idx": time_idx,
                    "lag_1": series["rental_count"].iloc[-2],
                    "rolling_3m": float(np.mean(last_three)),
                    "category": category,
                    "branch_id": branch_id,
                }
            ]
        )
        pred = float(np.clip(pipeline.predict(feat[FEATURES]), 0, None)[0])
        rows.append(
            {
                "year_month": forecast_month,
                "category": category,
                "branch_id": branch_id,
                "predicted_rentals": round(pred, 1),
                "model": model_name,
            }
        )
    return sorted(rows, key=lambda r: (r["category"], r["branch_id"]))


def get_feature_importances(pipeline) -> dict | None:
    """Map feature importances to names, if the model supports them."""
    model = pipeline.named_steps["model"]
    if not hasattr(model, "feature_importances_"):
        return None
    try:
        names = pipeline.named_steps["preprocess"].get_feature_names_out()
    except Exception:
        return None
    importances = model.feature_importances_
    paired = sorted(
        zip(names, importances), key=lambda kv: kv[1], reverse=True
    )
    return {str(name): round(float(value), 4) for name, value in paired}


def main() -> int:
    try:
        raw = load_monthly_demand()
        frame = build_features(raw)
        train, test, test_months = time_split(frame, n_test_months=3)
        X_train, y_train = train[FEATURES], train["target"]
        X_test, y_test = test[FEATURES], test["target"]

        best_name, best_pipe, comparison, test_pred = train_and_select(
            X_train, y_train, X_test, y_test
        )
        mae = mean_absolute_error(y_test, test_pred)
        rmse = math.sqrt(mean_squared_error(y_test, test_pred))
        per_category_mae = (
            pd.DataFrame({"category": test["category"].values, "err": np.abs(y_test.values - test_pred)})
            .groupby("category")["err"]
            .mean()
            .round(4)
            .to_dict()
        )
        per_category_mae = {str(k): float(v) for k, v in per_category_mae.items()}

        last_observed = raw["year_month"].max()
        forecast_month = (pd.Period(last_observed, freq="M") + 1).strftime("%Y-%m")
        forecast_rows = build_forecast_rows(raw, best_pipe, forecast_month)

        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        joblib.dump(best_pipe, ARTIFACTS_DIR / "demand_model.pkl")
        metrics = {
            "model": best_name,
            "mae": round(float(mae), 4),
            "rmse": round(float(rmse), 4),
            "per_category_mae": per_category_mae,
            "train_date_range": [
                str(train["year_month"].min()),
                str(train["year_month"].max()),
            ],
            "test_date_range": [
                str(test["target_month"].min()),
                str(test["target_month"].max()),
            ],
            "test_target_months": test_months,
            "feature_list": FEATURES,
            "n_train": int(len(train)),
            "n_test": int(len(test)),
            "model_comparison": comparison,
            "forecast_month": forecast_month,
            "data": "synthetic",
        }
        (ARTIFACTS_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))
        (ARTIFACTS_DIR / "latest_forecast.json").write_text(
            json.dumps(forecast_rows, indent=2)
        )
        importances = get_feature_importances(best_pipe)
        if importances is not None:
            (ARTIFACTS_DIR / "feature_importances.json").write_text(
                json.dumps(importances, indent=2)
            )

        print(f"Model comparison (hold-out MAE): {comparison}")
        print(f"Selected model: {best_name}")
        print(f"Hold-out MAE: {mae:.4f}  RMSE: {rmse:.4f}")
        print(f"Train months (feature): {train['year_month'].min()}..{train['year_month'].max()} "
              f"(target {train['target_month'].min()}..{train['target_month'].max()})")
        print(f"Test target months: {test_months} (n={len(test)})")
        print(f"Per-category MAE: {per_category_mae}")
        print(f"Forecast {forecast_month}: {len(forecast_rows)} (category, branch) pairs")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI must exit non-zero on failure
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
