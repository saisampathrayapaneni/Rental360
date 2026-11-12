# Rental360 — pipeline helpers
VENV ?= .venv/bin
PY = $(VENV)/python

.PHONY: setup data etl models quality train anomalies pipeline run test clean

setup:
	python3 -m venv .venv
	$(VENV)/pip install -r requirements.txt

data:
	$(PY) data/generate_data.py

etl:
	$(PY) etl/load_warehouse.py

models:
	$(PY) models/run_models.py

quality:
	$(PY) etl/data_quality.py

train:
	$(PY) ml/train.py

anomalies:
	$(PY) ml/anomaly.py

pipeline: data etl models quality train anomalies

run:
	$(VENV)/uvicorn app.main:app --port 8000

test:
	$(VENV)/pytest tests/ -q

clean:
	rm -f data/warehouse/*.db data/raw/*.csv
	rm -rf ml/artifacts/*.pkl ml/artifacts/*.json
