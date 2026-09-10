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

# Pre-fetch ChromaDB's default embedding model (all-MiniLM-L6-v2, ONNX) into
# the image. Participant namespaces on the Spark cluster have no internet
# egress, so chromadb's first-call download is refused and the app dies during
# startup indexing. Path.home() honours $HOME, so HOME must stay pointed here
# at runtime -- do not override it to the PVC.
ENV HOME=/opt/appcache
RUN mkdir -p /opt/appcache && \
    python -c "from chromadb.utils.embedding_functions.onnx_mini_lm_l6_v2 import ONNXMiniLM_L6_V2 as E; e = E(); e._download_model_if_not_exists(); print('cached at', e.DOWNLOAD_PATH)" && \
    find /opt/appcache -name 'onnx.tar.gz' -delete && \
    chown -R 1000:1000 /opt/appcache

COPY app/ .

RUN mkdir -p /shared/.sqlite

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
