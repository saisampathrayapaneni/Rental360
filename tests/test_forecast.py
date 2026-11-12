"""Tests for the Rental360 ML modules (ml/train.py, ml/forecast.py, ml/anomaly.py).

ALL DATA IS SYNTHETIC. These tests run against the synthetic warehouse and
the artifacts produced by `python ml/train.py` (run training first).
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ml import anomaly as anomaly_mod  # noqa: E402
from ml import forecast as forecast_mod  # noqa: E402
from ml import train as train_mod  # noqa: E402

ARTIFACTS = REPO_ROOT / "ml" / "artifacts"


@pytest.fixture(scope="module")
def raw():
    return train_mod.load_monthly_demand()


@pytest.fixture(scope="module")
def frame(raw):
    return train_mod.build_features(raw)


def test_loader_returns_expected_columns(raw):
    assert list(raw.columns) == train_mod.EXPECTED_COLUMNS
    assert len(raw) == 1895
    assert raw["year_month"].min() == "2024-01"
    assert raw["year_month"].max() == "2026-10"


def test_features_have_no_nans(frame):
    cols = train_mod.FEATURES + ["target", "target_month"]
    assert all(c in frame.columns for c in cols)
    assert not frame[cols].isna().any().any()
    # One row per (category, branch, feature month) after dropping warm-up/target rows.
    assert not frame.duplicated(subset=["category", "branch_id", "year_month"]).any()


def test_time_split_has_no_leakage(frame):
    train, test, test_months = train_mod.time_split(frame, n_test_months=3)
    assert max(train["target_month"]) < min(test["target_month"])
    assert sorted(test["target_month"].unique()) == test_months
    assert len(test_months) == 3
    assert len(train) + len(test) == len(frame)


def test_model_predicts_non_negative(frame):
    train, test, _ = train_mod.time_split(frame, n_test_months=3)
    name, pipe, comparison, pred = train_mod.train_and_select(
        train[train_mod.FEATURES], train["target"],
        test[train_mod.FEATURES], test["target"],
    )
    assert set(comparison) == {"GradientBoostingRegressor", "RandomForestRegressor"}
    assert name in comparison
    assert (pred >= 0).all()
    assert np.isfinite(pred).all()


def test_forecast_json_covers_every_pair(raw):
    path = ARTIFACTS / "latest_forecast.json"
    assert path.exists(), "Run `python ml/train.py` first"
    rows = json.loads(path.read_text())
    pairs = set(zip(raw["category"], raw["branch_id"]))
    assert len(rows) == len(pairs) == 62
    assert {(r["category"], r["branch_id"]) for r in rows} == pairs
    assert all(r["year_month"] == "2026-11" for r in rows)
    assert all(r["predicted_rentals"] >= 0 for r in rows)
    assert all(isinstance(r["predicted_rentals"], float) for r in rows)


def test_forecast_helpers_filter():
    metrics = forecast_mod.get_metrics()
    assert "mae" in metrics and "rmse" in metrics and metrics["mae"] >= 0
    all_rows = forecast_mod.predict_next_month()
    assert len(all_rows) == 62
    subset = forecast_mod.predict_next_month(category="Excavator", branch_id="BR-05")
    assert len(subset) == 1
    assert subset[0]["category"] == "Excavator"
    assert forecast_mod.predict_next_month(category="No Such Category") == []


def test_anomaly_flags_only_non_retired_and_z_scores_correct():
    # Category A: nine items at 10, one at 50 -> mean 14, sample std ~12.65.
    # z(50) = (50-14)/std > 2 -> hot outlier. A retired item at 200 must not flag.
    df = pd.DataFrame(
        {
            "equipment_id": [f"EQ-A{i}" for i in range(10)] + ["EQ-A-RET", "EQ-B1", "EQ-B2"],
            "equipment_name": ["A"] * 11 + ["B", "B"],
            "category": ["CatA"] * 11 + ["CatB", "CatB"],
            "branch_id": ["BR-01"] * 13,
            "daily_rate": [100.0] * 13,
            "status": ["available"] * 10 + ["retired", "available", "available"],
            "rented_days": [10] * 13,
            "rental_count": [1] * 13,
            "days_in_service": [100] * 13,
            "utilization_pct": [10.0] * 9 + [50.0, 200.0, 10.0, 10.0],
        }
    )
    found = anomaly_mod.detect_anomalies(df)
    flagged_ids = {a["equipment_id"] for a in found}
    assert "EQ-A-RET" not in flagged_ids  # retired equipment is never flagged
    assert "EQ-A9" in flagged_ids  # the 50% item
    assert all(a["flag"] == "hot_outlier" for a in found if a["equipment_id"] == "EQ-A9")
    # z-score computed correctly: mean/std over non-retired CatA items only.
    vals = np.array([10.0] * 9 + [50.0])
    expected_z = (50.0 - vals.mean()) / vals.std(ddof=1)
    got = next(a for a in found if a["equipment_id"] == "EQ-A9")
    assert got["z_score"] == pytest.approx(round(float(expected_z), 2))
    # CatB has zero std -> no flags from it.
    assert not any(a["category"] == "CatB" for a in found)


def test_anomaly_real_data_flags_only_non_retired():
    df = anomaly_mod.load_utilization()
    found = anomaly_mod.detect_anomalies(df)
    retired_ids = set(df[df["status"] == "retired"]["equipment_id"])
    assert not any(a["equipment_id"] in retired_ids for a in found)
    assert all(a["flag"] in ("idle_outlier", "hot_outlier") for a in found)
    assert all(abs(a["z_score"]) > 2 for a in found)
