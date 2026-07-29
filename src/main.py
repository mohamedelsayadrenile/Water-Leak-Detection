from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.v1.router import v1_router
from src.core.config import settings
from src.core.helper import _sweep_stale_tasks
from src.core.logging import get_logger, setup_logging

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.profile_dir.mkdir(parents=True, exist_ok=True)
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(settings.log_level)
    if settings.sweep_stale_tasks_on_startup:
        n = _sweep_stale_tasks()
        if n:
            logger.info("startup.swept", extra={"count": n})
    yield


app = FastAPI(
    title="Water Leak Detection",
    version="0.1.0",
    description="Behavior-based water tank leak detection from water_level signal.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(v1_router, prefix="/v1")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
