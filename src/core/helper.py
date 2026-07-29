from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

# --- time ---

PROFILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _iso(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).isoformat()


# --- csv ---


def _check_header(peek: bytes) -> None:
    first_line = peek.split(b"\n", 1)[0].decode("utf-8", errors="ignore").lower()
    if "datetime" not in first_line or "water_level" not in first_line:
        raise HTTPException(
            status_code=422,
            detail="CSV header must contain columns: datetime, water_level",
        )


# --- profile ids / paths ---


def _validate_profile_id(profile_id: str) -> None:
    if not PROFILE_ID_RE.match(profile_id):
        raise ValueError(f"invalid profile_id: {profile_id!r}")


def _meta_path(profile_id: str, root: Path) -> Path:
    return root / f"{profile_id}.meta.json"


def _profile_path(profile_id: str, root: Path) -> Path:
    return root / f"{profile_id}.json"


# --- sync pipeline wrappers (run via asyncio.to_thread) ---


def _build_profile_sync(profile_id: str, upload_path: Path) -> tuple[dict, dict]:
    # local imports avoid a core <-> services layering cycle at import time
    from src.core.config import settings
    from src.services.cleaning import clean
    from src.services.events import extract_events
    from src.services.io import parse_csv
    from src.services.profile import build_profile, make_profile_summary

    df, _ = parse_csv(
        upload_path.read_bytes(),
        settings,
        expected_min_days=settings.min_learn_days,
    )
    level_series = df["water_level"].astype(float)
    df_clean = clean(level_series, settings)
    events = extract_events(df_clean, settings)
    profile = build_profile(events, df_clean, settings)
    summary = make_profile_summary(profile, len(events))
    return profile, summary


def _build_detect_sync(file_bytes: bytes, profile: dict) -> tuple[list[Any], int]:
    from src.core.config import settings
    from src.services.cleaning import clean
    from src.services.detection import detect as detect_sync
    from src.services.events import extract_events
    from src.services.io import parse_csv

    df, _ = parse_csv(file_bytes, settings, expected_min_minutes=settings.min_detect_minutes)
    df_clean = clean(df["water_level"].astype(float), settings)
    events = extract_events(df_clean, settings)
    alerts = detect_sync(events, df_clean, profile, settings)
    return alerts, int(len(events))


# --- startup sweeper ---


def _sweep_stale_tasks() -> int:
    from datetime import timedelta

    from src.core.config import settings
    from src.core.logging import get_logger
    from src.repositories.profile_store import get_profile_store

    logger = get_logger("src.core.helper")
    store = get_profile_store()
    cutoff = timedelta(minutes=settings.task_timeout_minutes)
    stale = store.list_stale(cutoff)
    for meta in stale:
        store.update_meta(
            meta["profile_id"],
            status="failed",
            finished_at=_now_iso(),
            error=f"task stale > {cutoff}",
        )
        logger.warning("learn.swept", extra={"profile_id": meta["profile_id"]})
    return len(stale)
