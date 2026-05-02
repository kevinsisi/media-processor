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
#
# We register the deadsnakes PPA via a direct sources.list.d entry rather
# than `add-apt-repository`. The latter invokes python-launchpadlib which
# hits launchpad.net via httplib2 and trips IncompleteRead errors when the
# API is flaky — that's been costing us repeated build failures. Adding
# the apt source + GPG key directly uses libcurl + apt's built-in fetch
# logic, which is more robust under transient network conditions.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ca-certificates curl gnupg \
 && install -d -m 0755 /etc/apt/keyrings \
 && for i in 1 2 3 4 5; do \
        curl -fsSL "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0xF23C5A6CF475977595C89F51BA6932366A755776" \
            | gpg --dearmor -o /etc/apt/keyrings/deadsnakes.gpg \
        && break || (echo "keyserver attempt $i failed; retrying"; sleep $((i*5))); \
    done \
 && echo "deb [signed-by=/etc/apt/keyrings/deadsnakes.gpg] https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu jammy main" \
        > /etc/apt/sources.list.d/deadsnakes.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv python3.11-dev \
        build-essential libpq-dev \
        ffmpeg \
        libgl1 libglib2.0-0 \
        libgles2-mesa libegl1 \
        # ^^^ MediaPipe Tasks API (services/emotion.py) needs the OpenGL ES
        # client lib (libGLESv2.so.2) at FaceLandmarker.create_from_options
        # time even when the calculator graph is CPU-only; without these
        # the loader raises OSError("libGLESv2.so.2: cannot open shared
        # object file") and the emotion analysis step fails on every asset.
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
# mediapipe (Phase 8.1) drives services/emotion.py via the Face
# Landmarker Tasks API; the .task model file itself is downloaded
# lazily on first analyze run to /app/media/emotion_models, so the
# image build doesn't need network access to a Google CDN.
RUN pip install \
        "faster-whisper>=1.0.3,<2.0" \
        "opencv-python-headless>=4.10.0,<5.0" \
        "opencc>=1.1.7" \
        "numpy>=1.26.0,<3.0" \
        "Pillow>=10.4.0" \
        "mediapipe>=0.10.14,<0.11"

# v0.15 — MusicGen-small (services/music_gen.py): text → 30 s WAV.
# transformers ships ``MusicgenForConditionalGeneration``; we pull torch +
# torchaudio from the CUDA 12.1 wheel index so the runtime uses the GPU
# when available and falls back to CPU when not. The model itself
# (~600 MB) is downloaded lazily on the first generate call rather than
# baked into the image, so the build stays fast.
RUN pip install \
        --extra-index-url https://download.pytorch.org/whl/cu121 \
        "torch>=2.1.0,<2.5" \
        "torchaudio>=2.1.0,<2.5" \
 && pip install \
        "transformers>=4.45.0,<5.0" \
        "sentencepiece>=0.2.0" \
        "accelerate>=0.34.0,<2.0"

COPY src/ ./src/
COPY profiles/ ./profiles/
# v0.15 — operators run scripts/seed_music_library.py inside the worker
# container to pre-render the curated BGM library.
COPY scripts/ ./scripts/

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
