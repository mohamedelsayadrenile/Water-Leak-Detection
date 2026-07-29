from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, UploadFile

from src.core.config import settings
from src.core.helper import _check_header, _now_iso
from src.core.logging import get_logger
from src.models.schemas.learn import LearnResponse
from src.repositories.profile_store import get_profile_store
from src.services.learn_task import learn_profile_task

router = APIRouter()
logger = get_logger(__name__)


@router.post("/learn", response_model=LearnResponse, status_code=202)
async def learn(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
) -> LearnResponse:
    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=422, detail="empty file")
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="upload too large")

    _check_header(data[:2048])

    profile_id = uuid4().hex
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    upload_path = settings.uploads_dir / f"{profile_id}.csv"
    upload_path.write_bytes(data)

    store = get_profile_store()
    store.write_meta(
        profile_id,
        {
            "profile_id": profile_id,
            "status": "pending",
            "started_at": _now_iso(),
            "finished_at": None,
            "summary": None,
            "error": None,
        },
    )

    background_tasks.add_task(learn_profile_task, profile_id)

    logger.info(
        "learn.queued",
        extra={"profile_id": profile_id, "size": len(data)},
    )
    return LearnResponse(profile_id=profile_id, status="pending")
