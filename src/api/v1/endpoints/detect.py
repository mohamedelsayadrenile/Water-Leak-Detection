from __future__ import annotations

import asyncio

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from src.core.config import settings
from src.core.errors import ProfileNotFound, ValidationError
from src.core.helper import _build_detect_sync
from src.core.logging import get_logger
from src.models.schemas.common import AlertOut
from src.models.schemas.detect import DetectResponse
from src.repositories.profile_store import get_profile_store
from src.services.detection import severity_for

router = APIRouter()
logger = get_logger(__name__)


@router.post("/detect", response_model=DetectResponse)
async def detect(
    file: UploadFile = File(...),
    profile_id: str = Form(...),
) -> DetectResponse:
    store = get_profile_store()
    try:
        meta = store.load_meta(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid profile_id: {profile_id}") from exc
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
        alerts, n_events, confidence = await asyncio.to_thread(_build_detect_sync, data, profile)
    except ProfileNotFound as exc:
        raise HTTPException(status_code=404, detail=f"profile not found: {profile_id}") from exc
    except ValidationError as exc:
        # bad CSV schema/cadence/length, or a signal type the profile wasn't learned on
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        msg = str(exc)
        logger.exception("detect.failed", extra={"profile_id": profile_id})
        raise HTTPException(status_code=500, detail=f"detection failed: {msg}") from exc

    severity = severity_for(confidence, settings)
    leak_detected = confidence >= settings.leak_confidence_threshold
    logger.info(
        "detect.complete",
        extra={
            "profile_id": profile_id,
            "n_events": n_events,
            "n_alerts": len(alerts),
            "leak": leak_detected,
            "confidence": confidence,
            "severity": severity,
        },
    )

    return DetectResponse(
        profile_id=profile_id,
        leak_detected=leak_detected,
        leak_confidence=confidence,
        severity=severity,
        alerts=[AlertOut(**a.to_dict()) for a in alerts],
        n_events=n_events,
    )
