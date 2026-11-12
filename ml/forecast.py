"""Forecast helpers for Rental360.

ALL DATA IS SYNTHETIC. Forecasts read from ``latest_forecast.json`` are
illustrative only and must never be presented as real business predictions.

Importing this module never retrains the model; it only reads artifacts
written by ``ml/train.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import joblib

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"


def load_model():
    """Load the fitted demand-model pipeline saved by ``ml/train.py``."""
    path = ARTIFACTS_DIR / "demand_model.pkl"
    if not path.exists():
        raise FileNotFoundError(
            f"Model artifact not found: {path}. Run `python ml/train.py` first."
        )
    return joblib.load(path)


def predict_next_month(category: str | None = None, branch_id: str | None = None) -> list[dict]:
    """Return forecast dicts from ``latest_forecast.json``, optionally filtered.

    Each dict: {"year_month", "category", "branch_id", "predicted_rentals", "model"}.
    """
    path = ARTIFACTS_DIR / "latest_forecast.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Forecast artifact not found: {path}. Run `python ml/train.py` first."
        )
    rows = json.loads(path.read_text())
    if category is not None:
        rows = [r for r in rows if r["category"] == category]
    if branch_id is not None:
        rows = [r for r in rows if r["branch_id"] == branch_id]
    return rows


def get_metrics() -> dict:
    """Return the contents of ``metrics.json`` written by ``ml/train.py``."""
    path = ARTIFACTS_DIR / "metrics.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Metrics artifact not found: {path}. Run `python ml/train.py` first."
        )
    return json.loads(path.read_text())
