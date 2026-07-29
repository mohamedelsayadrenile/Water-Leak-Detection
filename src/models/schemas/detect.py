from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from src.models.schemas.common import AlertOut


class DetectResponse(BaseModel):
    profile_id: str
    leak_detected: bool
    leak_confidence: float
    severity: Literal["none", "low", "medium", "high"]
    alerts: list[AlertOut]
    n_events: int
