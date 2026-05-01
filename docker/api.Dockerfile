FROM python:3.11-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev curl \
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
        "httpx>=0.28.0"

COPY src/ ./src/
COPY profiles/ ./profiles/
COPY alembic.ini ./
COPY alembic/ ./alembic/

ENV PYTHONPATH=/app/src

EXPOSE 8000

CMD ["uvicorn", "media_processor.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
