from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class AlertOut(BaseModel):
    rule: Literal["A", "B", "C"]
    start: str
    end: str
    reason: str
    score: float


class ProfileSummary(BaseModel):
    n_days: int
    quiet_hours: list[int]
    global_duration: dict
    quiet_baseline_slope: float
    n_events: int
