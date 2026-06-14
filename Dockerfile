# syntax=docker/dockerfile:1
#
# Galadriel — ready-to-run container image.
#
# Two-stage build: the builder compiles wheels (incl. the heavier ChromaDB /
# onnxruntime stack that mempalace pulls in), the runtime stage stays slim.
#
#   docker build -t galadriel .
#   docker run --env-file .env -p 127.0.0.1:8080:8080 -v galadriel-data:/data galadriel
#
# Or just use docker-compose.yml (recommended): docker compose up -d --build
#
# Multi-arch: python:3.12-slim is published for amd64 and arm64, so a plain
# `docker build` works on both. For a registry push covering both:
#   docker buildx build --platform linux/amd64,linux/arm64 -t <repo> --push .

# ---------- builder: compile wheels ----------
FROM python:3.12-slim AS builder
WORKDIR /build
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential git \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ---------- runtime: slim final image ----------
FROM python:3.12-slim
LABEL org.opencontainers.image.title="Galadriel" \
      org.opencontainers.image.source="https://github.com/avasol/galadriel-public" \
      org.opencontainers.image.description="A persistent, self-hosted Claude agent with a verbatim memory palace."

# onnxruntime (transitive dep of mempalace) needs libgomp at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Non-root user. Its home is /data so the palace defaults (~/.mempalace) land
# on the persistent volume with zero extra config.
RUN useradd --create-home --home-dir /data --uid 1000 galadriel
WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    && rm -rf /wheels

# Application code. .dockerignore keeps keys/, .env, memory logs and bloat out.
COPY . .
RUN chown -R galadriel:galadriel /app /data

# Persistent state — mount these as volumes so they survive `docker compose down`:
#   /data            → the memory palace + archive (~/.mempalace), owned by the user
#   /app/memory      → daily memory logs (markdown)
#   /app/config      → scheduler_state.json, ambient_state.json, active_vision.txt
VOLUME ["/data", "/app/memory", "/app/config"]

# Palace lives under the user's home on the volume. These are the public
# defaults already (~/.mempalace), set explicitly here for clarity.
ENV MEMPALACE_PATH=/data/.mempalace/palace \
    PALACE_ARCHIVE_ROOT=/data/.mempalace/archive \
    PALACE_WAKE_UP_FILE=/data/.mempalace/wake_up.md \
    TOWER_HOST=0.0.0.0 \
    TOWER_PORT=8080 \
    PYTHONUNBUFFERED=1

# The Tower UI has NO built-in auth. Only ever publish this port to localhost
# or behind an authenticated reverse proxy. See docker-compose.yml.
EXPOSE 8080

USER galadriel
CMD ["python", "main.py"]
