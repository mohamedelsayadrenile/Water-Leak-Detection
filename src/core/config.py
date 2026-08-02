from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # signal (the pipeline is signal-agnostic; the CSV loader is the only stage that
    # knows which of these source columns the upload actually carried)
    signal_source_columns: tuple[str, ...] = ("water_level", "pressure_level")
    signal_column: str = "signal"

    # physical / sensor
    sampling_interval_seconds: int = 300
    # magnitude-dependent: tune per signal type (metres of level vs bar of pressure)
    sensor_deadband: float = 0.005

    # cleaning
    rolling_window: int = 5
    refill_consecutive_pos: int = 3
    # consecutive NaN samples that may be interpolated away; longer runs are rejected
    max_interpolate_samples: int = 6

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
    rule_b_slope_mult: float = 2.0
    rule_b_step_minutes: int = 60
    # rule C
    rule_c_max_duration_hours: float = 4.0

    # scoring / verdict
    rule_min_score: float = 0.3
    leak_confidence_threshold: float = 0.5
    severity_medium_threshold: float = 0.6
    severity_high_threshold: float = 0.85

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
        env_file="src/.env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
