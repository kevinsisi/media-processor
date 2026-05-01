FROM nvidia/cuda:12.1.1-cudnn8-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps — Python 3.11 (ubuntu 22.04 ships 3.10 by default; pull from
# deadsnakes for matching api image), ffmpeg (faster-whisper + frame sampling
# + motion downscale), libpq for psycopg2, build tools for source wheels.
# fonts-noto-cjk supplies "Noto Sans CJK TC" so the M5 subtitle burn-in
# (services/video_renderer.SUBTITLE_FORCE_STYLE) renders zh-Hant glyphs
# instead of tofu boxes; fontconfig refreshes the font cache so libass
# can resolve the family name at filter time.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        software-properties-common ca-certificates curl \
 && add-apt-repository -y ppa:deadsnakes/ppa \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev \
        build-essential libpq-dev \
        ffmpeg \
        libgl1 libglib2.0-0 \
        fonts-noto-cjk fontconfig \
 && fc-cache -f \
 && curl -sS https://bootstrap.pypa.io/get-pip.py | python3.11 \
 && ln -sf /usr/bin/python3.11 /usr/local/bin/python \
 && rm -rf /var/lib/apt/lists/*

# Base runtime deps — same set as api so the worker can read/write the same
# DB and Redis. Keep this list in sync with [project.dependencies].
RUN pip install --upgrade pip \
 && pip install \
        "fastapi>=0.115.0" \
        "pydantic>=2.9.0" \
        "pydantic-settings>=2.6.0" \
        "sqlalchemy>=2.0.36" \
        "alembic>=1.14.0" \
        "asyncpg>=0.30.0" \
        "psycopg2-binary>=2.9.10" \
        "redis>=5.2.0" \
        "rq>=2.0.0" \
        "pyyaml>=6.0.2" \
        "httpx>=0.28.0"

# Heavy analysis deps — the [analysis] extras group from pyproject.
RUN pip install \
        "faster-whisper>=1.0.3,<2.0" \
        "opencv-python-headless>=4.10.0,<5.0" \
        "opencc>=1.1.7" \
        "numpy>=1.26.0,<3.0" \
        "Pillow>=10.4.0"

COPY src/ ./src/
COPY profiles/ ./profiles/

ENV PYTHONPATH=/app/src

# Pre-download the default Whisper model so the first job doesn't pay the
# download cost. Skipped when WHISPER_FAKE=1 at build time (CI builds).
ARG WHISPER_PREFETCH_MODEL=medium
ARG WHISPER_FAKE=0
RUN if [ "$WHISPER_FAKE" = "0" ]; then \
        python -c "from faster_whisper import WhisperModel; WhisperModel('${WHISPER_PREFETCH_MODEL}', device='cpu', compute_type='int8')" ; \
    fi

# Run the RQ worker against the 'analysis' queue.
CMD ["python", "-m", "media_processor.workers"]
