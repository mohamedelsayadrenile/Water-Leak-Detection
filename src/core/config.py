from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class Config:
    sampling_interval_seconds: int
    tank_height: float
    tank_area: float | None
    sensor_deadband: float
    rolling_window: int
    refill_consecutive_pos: int
    min_event_samples: int
    idle_gap_minutes: int
    low_activity_percentile: float
    night_hours: tuple[int, ...]
    rule_a_min_unusual_prob: float
    rule_a_dur_mult: float
    rule_b_min_quiet_hours: float
    rule_b_alpha: float
    rule_b_slope_mult: float
    rule_b_step_minutes: int
    rule_c_max_duration_hours: float


class Settings(BaseSettings):
    # physical / sensor
    sampling_interval_seconds: int = 300
    tank_height: float = 4.0
    tank_area: float | None = None
    sensor_deadband: float = 0.005

    # cleaning
    rolling_window: int = 5
    refill_consecutive_pos: int = 3

    # events
    min_event_samples: int = 2
    idle_gap_minutes: int = 15

    # profile
    low_activity_percentile: float = 33.0
    night_hours: tuple[int, ...] = (23, 0, 1, 2, 3, 4, 5)

    # rule A
    rule_a_min_unusual_prob: float = 0.25
    rule_a_dur_mult: float = 1.5
    # rule B
    rule_b_min_quiet_hours: float = 4.0
    rule_b_alpha: float = 0.01
    rule_b_slope_mult: float = 2.0
    rule_b_step_minutes: int = 60
    # rule C
    rule_c_max_duration_hours: float = 4.0

    # API / storage
    profile_dir: Path = Path("data/profiles")
    uploads_dir: Path = Path("data/uploads")
    keep_failed_uploads: bool = True
    min_learn_days: int = 25
    min_detect_minutes: int = 1440
    max_upload_mb: int = 50
    task_timeout_minutes: int = 60
    sweep_stale_tasks_on_startup: bool = True

    # logging
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file="src/.env/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def to_config(self) -> Config:
        return Config(
            sampling_interval_seconds=self.sampling_interval_seconds,
            tank_height=self.tank_height,
            tank_area=self.tank_area,
            sensor_deadband=self.sensor_deadband,
            rolling_window=self.rolling_window,
            refill_consecutive_pos=self.refill_consecutive_pos,
            min_event_samples=self.min_event_samples,
            idle_gap_minutes=self.idle_gap_minutes,
            low_activity_percentile=self.low_activity_percentile,
            night_hours=self.night_hours,
            rule_a_min_unusual_prob=self.rule_a_min_unusual_prob,
            rule_a_dur_mult=self.rule_a_dur_mult,
            rule_b_min_quiet_hours=self.rule_b_min_quiet_hours,
            rule_b_alpha=self.rule_b_alpha,
            rule_b_slope_mult=self.rule_b_slope_mult,
            rule_b_step_minutes=self.rule_b_step_minutes,
            rule_c_max_duration_hours=self.rule_c_max_duration_hours,
        )


settings = Settings()
