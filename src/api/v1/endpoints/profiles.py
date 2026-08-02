from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status

from src.core.errors import error_payload
from src.core.logging import get_logger
from src.models.schemas.status import ProfileStatusResponse
from src.repositories.profile_store import get_profile_store

router = APIRouter()
logger = get_logger(__name__)


@router.get(
    "/profiles/{profile_id}",
    response_model=ProfileStatusResponse,
    responses={
        202: {"description": "learning still in progress"},
        404: {"description": "unknown profile"},
        422: {"description": "malformed profile_id, or learning failed on a bad CSV"},
        500: {"description": "learning failed on an internal error"},
    },
)
async def get_status(profile_id: str, response: Response) -> ProfileStatusResponse:
    """Poll a learn job.

    The HTTP status carries the outcome so a client checking only the status code
    cannot mistake a failed job for a healthy one: 202 while running, 200 once ready,
    422 when the upload was rejected, 500 when learning hit an internal error.
    """
    store = get_profile_store()
    try:
        meta = store.load_meta(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid profile_id: {profile_id}") from exc

    if meta is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {profile_id}")

    job_status = meta.get("status", "pending")

    if job_status == "failed":
        # a meta written before failures were classified is treated as internal
        error_type = meta.get("error_type") or "internal"
        if error_type == "validation":
            raise HTTPException(
                status_code=422,
                detail=error_payload(
                    meta.get("error") or "the uploaded CSV was rejected",
                    type_="validation",
                    profile_id=profile_id,
                    status=job_status,
                ),
            )
        logger.error(
            "profiles.failed",
            extra={"profile_id": profile_id, "error": meta.get("error")},
        )
        raise HTTPException(
            status_code=500,
            detail=error_payload(
                "profile learning failed",
                type_="internal",
                profile_id=profile_id,
                status=job_status,
            ),
        )

    if job_status in ("pending", "running"):
        response.status_code = status.HTTP_202_ACCEPTED

    return ProfileStatusResponse(**meta)
