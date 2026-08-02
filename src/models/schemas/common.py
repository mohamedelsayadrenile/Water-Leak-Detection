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
    # None for profiles learned before signal types were recorded
    signal_type: str | None = None
    n_days: int
    quiet_hours: list[int]
    global_duration: dict
    quiet_baseline_slope: float
    n_events: int
    # Extended usage summary. Absent on profiles learned before these fields
    # existed — left null/empty rather than backfilled.
    scheduled_usage_hours: list[int] = []
    average_usage_amount: float | None = None
    refill_periods: list[int] = []
    # unit follows signal_type: metres for water_level, bar for pressure_level
    min_tank_level: float | None = None
    average_usage_duration: float = 0.0
    lowest_usage_hours: list[int] = []
