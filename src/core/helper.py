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
    """Cheap header sniff on upload; parse_csv does the strict exactly-one check."""
    from src.core.config import settings

    first_line = peek.split(b"\n", 1)[0].decode("utf-8", errors="ignore").lower()
    if "datetime" not in first_line or not any(
        c in first_line for c in settings.signal_source_columns
    ):
        raise HTTPException(
            status_code=422,
            detail=(
                "CSV header must contain 'datetime' and exactly one of: "
                + ", ".join(settings.signal_source_columns)
            ),
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
    from src.core.errors import ValidationError
    from src.services.cleaning import clean
    from src.services.events import extract_events
    from src.services.io import parse_csv
    from src.services.profile import build_profile, make_profile_summary

    try:
        file_bytes = upload_path.read_bytes()
    except OSError as exc:
        # never surface the staged path — the error string reaches the client via meta
        raise ValidationError("staged upload is no longer available") from exc

    df, parse_summary = parse_csv(
        file_bytes,
        settings,
        expected_min_days=settings.min_learn_days,
    )
    signal_series = df[settings.signal_column].astype(float)
    df_clean = clean(signal_series, settings)
    events = extract_events(df_clean, settings)
    profile = build_profile(events, df_clean, settings)
    # stamped here, not in build_profile, so the profiling stage stays signal-agnostic
    profile["signal_type"] = parse_summary["signal_type"]
    summary = make_profile_summary(profile, len(events))
    return profile, summary


def _build_detect_sync(file_bytes: bytes, profile: dict) -> tuple[list[Any], int, float]:
    from src.core.config import settings
    from src.core.errors import ValidationError
    from src.core.logging import get_logger
    from src.services.cleaning import clean
    from src.services.detection import detect as detect_sync
    from src.services.events import extract_events
    from src.services.io import parse_csv

    df, parse_summary = parse_csv(
        file_bytes, settings, expected_min_minutes=settings.min_detect_minutes
    )

    expected = profile.get("signal_type")
    actual = parse_summary["signal_type"]
    if expected is None:
        # profile learned before signal types were recorded — cannot verify
        get_logger("src.core.helper").warning(
            "detect.signal_type_unknown", extra={"signal_type": actual}
        )
    elif expected != actual:
        raise ValidationError(
            f"signal type mismatch: profile was learned on '{expected}' but the "
            f"uploaded CSV contains '{actual}'"
        )

    df_clean = clean(df[settings.signal_column].astype(float), settings)
    events = extract_events(df_clean, settings)
    alerts, confidence = detect_sync(events, df_clean, profile, settings)
    return alerts, int(len(events)), confidence


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
