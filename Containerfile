FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt .
# BuildKit cache mount: keeps the pip download cache across builds so editing
# requirements.txt re-resolves without re-downloading chromadb/onnxruntime.
# --no-cache-dir is deliberately absent — it would defeat the mount. The cache
# lives in the builder, not in a layer, so the image stays the same size.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt

COPY app/ .

RUN mkdir -p /shared/.sqlite

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
