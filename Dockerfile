# Production image for hosted deployment (Render, Fly, any container host). Local use does not need Docker.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 codemind
WORKDIR /app
COPY requirements-render.txt .
RUN pip install -r requirements-render.txt
COPY --chown=codemind:codemind . .
USER codemind

ENV CODEMIND_HOSTED=true \
    CODEMIND_STORAGE_DIR=/tmp/codemind \
    CODEMIND_EMBEDDING_BACKEND=hashing \
    CODEMIND_VECTOR_BACKEND=sqlite
# CODEMIND_ADMIN_EMAIL and CODEMIND_ADMIN_PASSWORD must be provided at runtime; in hosted mode the app refuses to start without an administrator.
EXPOSE 10000
CMD ["sh", "-c", "exec uvicorn backend.asgi:app --host 0.0.0.0 --port ${PORT:-10000} --proxy-headers --forwarded-allow-ips='*'"]
