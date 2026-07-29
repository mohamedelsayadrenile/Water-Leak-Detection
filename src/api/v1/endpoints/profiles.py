from __future__ import annotations

from fastapi import APIRouter, HTTPException

from src.models.schemas.status import ProfileStatusResponse
from src.repositories.profile_store import get_profile_store

router = APIRouter()


@router.get("/profiles/{profile_id}", response_model=ProfileStatusResponse)
async def get_status(profile_id: str) -> ProfileStatusResponse:
    store = get_profile_store()
    meta = store.load_meta(profile_id)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"profile not found: {profile_id}")

    status = meta.get("status", "pending")
    if status in ("ready", "failed"):
        return ProfileStatusResponse(**meta)
    return ProfileStatusResponse(**meta)
