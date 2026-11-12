FROM python:3.12-slim

WORKDIR /app

# System deps: build tools for scikit-learn wheels are prebuilt, but keep
# gcc available for any source builds; sqlite3 CLI is handy for debugging.
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc sqlite3 curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Build the warehouse at image-build time so the container works out of the box.
# (Regenerate with: python data/generate_data.py && python etl/load_warehouse.py)
RUN python data/generate_data.py \
    && python etl/load_warehouse.py \
    && python models/run_models.py \
    && python ml/train.py || echo "ML training skipped/failed — forecast endpoint will report unavailable"

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
