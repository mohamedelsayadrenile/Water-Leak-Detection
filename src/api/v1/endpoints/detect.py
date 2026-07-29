from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.core.config import settings
from src.core.errors import ProfileNotFound
from src.core.logging import get_logger
from src.models.schemas.common import AlertOut
from src.models.schemas.detect import DetectResponse
from src.repositories.profile_store import get_profile_store
from src.services.cleaning import clean
from src.services.detection import detect as detect_sync
from src.services.events import extract_events
from src.services.io import parse_csv

router = APIRouter()
logger = get_logger(__name__)


def _build_detect_sync(file_bytes: bytes, profile: dict) -> tuple[list[Any], int]:
    cfg = settings.to_config()
    df, _ = parse_csv(file_bytes, cfg, expected_min_minutes=settings.min_detect_minutes)
    df_clean = clean(df["water_level"].astype(float), cfg)
    events = extract_events(df_clean, cfg)
    alerts = detect_sync(events, df_clean, profile, cfg)
    return alerts, int(len(events))


@router.post("/detect", response_model=DetectResponse)
async def detect(
    file: UploadFile = File(...),
    profile_id: str = Form(...),
) -> DetectResponse:
    store = get_profile_store()
    meta = store.load_meta(profile_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {profile_id}")

    status = meta.get("status", "pending")
    if status != "ready":
        raise HTTPException(
            status_code=409,
            detail={
                "profile_id": profile_id,
                "status": status,
                "error": meta.get("error"),
                "message": f"profile is {status}; cannot run detection",
            },
        )

    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=422, detail="empty file")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="upload too large")

    profile = store.load_profile(profile_id)

    try:
        alerts, n_events = await asyncio.to_thread(_build_detect_sync, data, profile)
    except ProfileNotFound as exc:
        raise HTTPException(status_code=404, detail=f"profile not found: {profile_id}") from exc
    except Exception as exc:
        # Validation errors from parse_csv -> 422
        msg = str(exc)
        if "missing required columns" in msg or "irregular cadence" in msg or "too short" in msg:
            raise HTTPException(status_code=422, detail=msg) from exc
        logger.exception("detect.failed", extra={"profile_id": profile_id})
        raise HTTPException(status_code=500, detail=f"detection failed: {msg}") from exc

    leak_detected = len(alerts) > 0
    logger.info(
        "detect.complete",
        extra={
            "profile_id": profile_id,
            "n_events": n_events,
            "n_alerts": len(alerts),
            "leak": leak_detected,
        },
    )

    return DetectResponse(
        profile_id=profile_id,
        leak_detected=leak_detected,
        alerts=[AlertOut(**a.to_dict()) for a in alerts],
        n_events=n_events,
    )
