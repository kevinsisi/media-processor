FROM python:3.11-slim

WORKDIR /app

# System deps
# ffmpeg is required by services/thumbnails.py (keyframe gallery extraction at
# upload-complete) and by the existing services/uploads.probe_media() call,
# which silently degrades without it.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Python deps — keep this list in sync with [project.dependencies] in
# pyproject.toml. Drift here causes runtime ImportError / missing-table
# crashes (see commit history for httpx and alembic incidents).
COPY pyproject.toml ./
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir hatchling \
 && pip install --no-cache-dir \
        "fastapi>=0.115.0" \
        "uvicorn[standard]>=0.32.0" \
        "pydantic>=2.9.0" \
        "pydantic-settings>=2.6.0" \
        "sqlalchemy>=2.0.36" \
        "alembic>=1.14.0" \
        "asyncpg>=0.30.0" \
        "psycopg2-binary>=2.9.10" \
        "redis>=5.2.0" \
        "rq>=2.0.0" \
        "python-multipart>=0.0.17" \
        "pyyaml>=6.0.2" \
        "httpx>=0.28.0" \
        "edge-tts>=6.1.9"

# v0.23.1 / v0.28.0 — OpenCV remains needed by the api container for
# synchronous custom ROI tracking (mode=custom, services/object_tracking.
# track_custom_roi). mode=point now runs asynchronously on worker-analysis,
# so LK point tracking no longer executes inside the API request path. The
# ``-headless`` variant skips Qt/GUI bindings (the api container has
# no display) but keeps the contrib trackers (CSRT, MIL, etc.). numpy
# pinned to the same range the worker uses to avoid wheel mismatches
# when both containers share a mounted package cache.
RUN pip install --no-cache-dir \
        "opencv-python-headless>=4.10.0,<5.0" \
        "numpy>=1.26.0,<3.0"

COPY src/ ./src/
COPY profiles/ ./profiles/
COPY alembic.ini ./
COPY alembic/ ./alembic/
COPY scripts/ ./scripts/

ENV PYTHONPATH=/app/src

EXPOSE 8000

# M7 — apply pending alembic migrations on every api boot. Removes the
# need to remember `docker exec api alembic upgrade head` after a deploy
# that ships a new migration. The api container is the canonical
# migration runner; worker stays stateless.
CMD ["sh", "-c", "alembic upgrade head && uvicorn media_processor.api.main:app --host 0.0.0.0 --port 8000"]
