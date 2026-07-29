from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.v1.router import v1_router
from src.core.config import settings
from src.core.logging import get_logger, setup_logging
from src.repositories.profile_store import get_profile_store

logger = get_logger(__name__)


def _sweep_stale_tasks() -> int:
    store = get_profile_store()
    cutoff = timedelta(minutes=settings.task_timeout_minutes)
    stale = store.list_stale(cutoff)
    from datetime import datetime

    for meta in stale:
        store.update_meta(
            meta["profile_id"],
            status="failed",
            finished_at=datetime.now(UTC).isoformat(),
            error=f"task stale > {cutoff}",
        )
        logger.warning(
            "learn.swept",
            extra={"profile_id": meta["profile_id"]},
        )
    return len(stale)


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
