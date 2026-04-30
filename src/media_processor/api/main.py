"""FastAPI application entry point."""
from fastapi import FastAPI

from media_processor.api.routers import health

app = FastAPI(
    title="media-processor API",
    version="0.1.0",
)

app.include_router(health.router)
