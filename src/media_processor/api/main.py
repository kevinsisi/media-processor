"""FastAPI application entry point."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

from media_processor.api.config import settings
from media_processor.api.routers import (
    assets,
    drafts,
    health,
    music,
    projects,
    queue,
    reviews,
    uploads,
    watermark_presets,
)
from media_processor.api.routers import settings as settings_router
from media_processor.api.watchdog import watchdog_loop

logger = logging.getLogger(__name__)

# Cache thumbnail files for 1 day; paths are stable per (asset_id, frame_index)
# so the browser can hold onto them aggressively.
THUMBNAIL_CACHE_CONTROL = "public, max-age=86400, immutable"
# Drafts can re-render at the same path (force re-roll), so cache for 5 min
# only — long enough to survive a quick reload, short enough to refresh.
DRAFT_CACHE_CONTROL = "public, max-age=300"


class StaticCacheMiddleware(BaseHTTPMiddleware):
    """Apply a fixed Cache-Control to responses under one URL prefix."""

    def __init__(self, app: ASGIApp, *, prefix: str, cache_control: str) -> None:
        super().__init__(app)
        self._prefix = prefix
        self._cache_control = cache_control

    async def dispatch(self, request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        if request.url.path.startswith(self._prefix) and response.status_code == 200:
            response.headers["Cache-Control"] = self._cache_control
        return response


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """v0.25.1 — runs the orphan-watchdog loop alongside the API.

    Spawns a background asyncio task at startup; the task sweeps
    in-flight Drafts every ``WATCHDOG_INTERVAL_S`` seconds and
    auto-resubmits any whose RQ job has disappeared. Cancelled
    cleanly on shutdown; the in-flight sweep gets a moment to
    finish (CancelledError bubbles out of ``watchdog_loop`` after
    the current ``asyncio.sleep`` returns).
    """
    task = asyncio.create_task(watchdog_loop(), name="orphan-watchdog")
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


app = FastAPI(
    title="media-processor API",
    version="0.42.4",
    lifespan=lifespan,
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(drafts.router)
app.include_router(assets.router)
app.include_router(reviews.router)
app.include_router(settings_router.router)
app.include_router(uploads.router)
app.include_router(music.router)
app.include_router(watermark_presets.router)
app.include_router(queue.router)

# Static-serve generated thumbnail JPEGs. The directory is the in-container
# path; on the dispatch host this resolves under MEDIA_STORAGE_DIR. mkdir on
# startup so the mount doesn't fail on a fresh deploy. Swallow OSError so
# import still works in environments without write access (CI test runner).
for _media_dir in (
    settings.thumbnails_dir,
    settings.drafts_dir,
    settings.bgm_dir,
    settings.watermark_dir,
    settings.assets_dir,
):
    with contextlib.suppress(OSError):
        Path(_media_dir).mkdir(parents=True, exist_ok=True)
# v0.15 — also create the curated library subdirectory so a fresh deploy
# can immediately list (an empty) /music-library response without the
# operator pre-creating the folder.
with contextlib.suppress(OSError):
    Path(settings.bgm_dir, "_library").mkdir(parents=True, exist_ok=True)
app.add_middleware(
    StaticCacheMiddleware,
    prefix="/media/thumbnails",
    cache_control=THUMBNAIL_CACHE_CONTROL,
)
app.add_middleware(
    StaticCacheMiddleware,
    prefix="/media/drafts",
    cache_control=DRAFT_CACHE_CONTROL,
)
app.mount(
    "/media/thumbnails",
    StaticFiles(directory=settings.thumbnails_dir, check_dir=False),
    name="thumbnails",
)
app.mount(
    "/media/drafts",
    StaticFiles(directory=settings.drafts_dir, check_dir=False),
    name="drafts",
)
# v0.15 — serve BGM audio (uploaded, AI-generated, and library tracks).
# Mounted with no cache header so a re-uploaded BGM at the same path is
# picked up by browsers immediately.
app.mount(
    "/media/bgm",
    StaticFiles(directory=settings.bgm_dir, check_dir=False),
    name="bgm",
)
# v0.18 — serve uploaded brand watermark PNGs so the picker can preview
# the current logo without a separate signed-URL endpoint. Same no-cache
# semantics as the BGM mount: a re-upload at the same path is picked up
# immediately.
app.mount(
    "/media/watermarks",
    StaticFiles(directory=settings.watermark_dir, check_dir=False),
    name="watermarks",
)
# v0.20 — serve uploaded source assets so the timeline-editor preview
# pane can scrub the original MP4 via a native <video> element.
# StaticFiles already speaks Range: so seek-to-position works without a
# custom streaming endpoint. No cache header — same trade-off as BGM:
# a re-uploaded asset at the same path appears immediately.
app.mount(
    "/media/assets",
    StaticFiles(directory=settings.assets_dir, check_dir=False),
    name="assets",
)
