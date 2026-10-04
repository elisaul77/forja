FROM python:3.12-slim-bookworm

# Shared libraries required by cadquery-ocp (OpenCascade) at import time,
# even in headless/off-screen mode (ADR-0001). `git` is the history
# backend (ADR-0014), called only through app/git_store.py.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1 \
    libglu1-mesa \
    libgomp1 \
    libxrender1 \
    libxext6 \
    libsm6 \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
ENV PYTHONPATH=/app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# F0.2 (ADR-0013): mount points seeded with their ownership, so fresh named
# volumes start right. `/staging` is shared with forja-sandbox (layout in
# app/sandbox/protocolo.py); `/run/secrets` holds the token, `forja` only.
# Placed after the pip layer so it does not invalidate it.
RUN mkdir -p /staging/trabajos /staging/ejecutor /run/secrets \
    && chown 1000:1000 /staging /staging/trabajos /run/secrets \
    && chmod 0711 /staging /staging/trabajos \
    && chmod 0700 /run/secrets \
    && chown 65534:65534 /staging/ejecutor \
    && chmod 0755 /staging/ejecutor

COPY app/ /app/
COPY mcp_server/ /app/mcp_server/
RUN chmod 0755 /app/entrypoint.sh

EXPOSE 8000

# Root only for the one-time migration + token move, then setpriv to uid
# 1000 (app/entrypoint.sh). forja-sandbox overrides the entrypoint.
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "3"]
