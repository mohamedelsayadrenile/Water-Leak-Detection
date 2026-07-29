from __future__ import annotations

import asyncio

from src.core.config import settings
from src.core.helper import _build_profile_sync, _now_iso
from src.core.logging import get_logger
from src.repositories.profile_store import get_profile_store

logger = get_logger(__name__)


async def learn_profile_task(profile_id: str) -> None:
    """Background task: parse -> clean -> events -> profile -> persist.

    All pandas/scipy work is wrapped in asyncio.to_thread so the event loop
    keeps serving other requests while the CPU-bound pipeline runs.
    """
    store = get_profile_store()
    store.update_meta(profile_id, status="running")
    upload_path = settings.uploads_dir / f"{profile_id}.csv"
    logger.info("learn.start", extra={"profile_id": profile_id})
    try:
        profile, summary = await asyncio.to_thread(_build_profile_sync, profile_id, upload_path)
        store.save_profile(profile_id, profile)
        store.update_meta(
            profile_id,
            status="ready",
            finished_at=_now_iso(),
            summary=summary,
        )
        upload_path.unlink(missing_ok=True)
        logger.info(
            "learn.complete",
            extra={"profile_id": profile_id, "n_days": summary["n_days"]},
        )
    except Exception as exc:
        store.update_meta(
            profile_id,
            status="failed",
            finished_at=_now_iso(),
            error=str(exc)[:500],
        )
        if not settings.keep_failed_uploads:
            upload_path.unlink(missing_ok=True)
        logger.exception("learn.failed", extra={"profile_id": profile_id})
