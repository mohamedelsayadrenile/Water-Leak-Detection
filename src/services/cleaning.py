from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.config import Config


def classify_movement(delta: pd.Series, sensor_deadband: float) -> pd.Series:
    def classify(d: float) -> str:
        if pd.isna(d):
            return "flat"
        if d > sensor_deadband:
            return "rise"
        if d < -sensor_deadband:
            return "drop"
        return "flat"

    return delta.apply(classify)


def detect_refill(movement: pd.Series, consecutive_pos: int) -> np.ndarray:
    is_rise = movement.to_numpy() == "rise"
    refill_mask = np.zeros(len(movement), dtype=bool)
    consec = 0
    for i in range(len(movement)):
        if is_rise[i]:
            consec += 1
            if consec >= consecutive_pos:
                refill_mask[i - consec + 1 : i + 1] = True
        else:
            consec = 0
    return refill_mask


def clean(level_series: pd.Series, cfg: Config) -> pd.DataFrame:
    """Stage 2 of the POC: smoothing + movement + refill mask.

    `level_series` must be a datetime-indexed Series of water_level (raw).
    Returns a DataFrame indexed the same, with columns:
    level_raw, level_smooth, movement, is_refill.
    """
    level = level_series.astype(float)
    smoothed = level.rolling(window=cfg.rolling_window, center=True, min_periods=1).median()

    delta = smoothed.diff()
    movement = classify_movement(delta, cfg.sensor_deadband)
    refill_mask = detect_refill(movement, cfg.refill_consecutive_pos)

    return pd.DataFrame(
        {
            "level_raw": level,
            "level_smooth": smoothed,
            "movement": movement,
            "is_refill": refill_mask,
        },
        index=level_series.index,
    )
