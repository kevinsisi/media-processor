"""FastAPI application entry point."""

from fastapi import FastAPI

from media_processor.api.routers import (
    assets,
    drafts,
    health,
    projects,
    reviews,
    uploads,
)

app = FastAPI(
    title="media-processor API",
    version="0.8.0",
)

app.include_router(health.router)
app.include_router(projects.router)
app.include_router(drafts.router)
app.include_router(assets.router)
app.include_router(reviews.router)
app.include_router(uploads.router)
