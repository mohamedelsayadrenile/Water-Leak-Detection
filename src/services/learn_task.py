from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from src.core.config import settings
from src.core.logging import get_logger
from src.repositories.profile_store import get_profile_store
from src.services.cleaning import clean
from src.services.events import extract_events
from src.services.io import parse_csv
from src.services.profile import build_profile, make_profile_summary

logger = get_logger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _build_profile_sync(profile_id: str, upload_path) -> tuple[dict, dict]:
    cfg = settings.to_config()
    df, _ = parse_csv(
        upload_path.read_bytes(),
        cfg,
        expected_min_days=settings.min_learn_days,
    )
    level_series = df["water_level"].astype(float)
    df_clean = clean(level_series, cfg)
    events = extract_events(df_clean, cfg)
    profile = build_profile(events, df_clean, cfg)
    summary = make_profile_summary(profile, len(events))
    return profile, summary


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
