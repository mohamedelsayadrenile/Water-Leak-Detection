from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.models.schemas.common import ProfileSummary


class ProfileStatusResponse(BaseModel):
    profile_id: str
    status: Literal["pending", "running", "ready", "failed"]
    started_at: str | None = None
    finished_at: str | None = None
    summary: ProfileSummary | None = None
    error: str | None = None
    # absent on metas written before failures were classified; treated as "internal"
    error_type: Literal["validation", "internal"] | None = None
