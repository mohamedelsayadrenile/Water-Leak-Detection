from __future__ import annotations

from pydantic import BaseModel

from src.models.schemas.common import AlertOut


class DetectResponse(BaseModel):
    profile_id: str
    leak_detected: bool
    alerts: list[AlertOut]
    n_events: int
