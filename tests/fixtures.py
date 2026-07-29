from __future__ import annotations

import io
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

SRC_CADENCE_MIN = 5  # 300 s
DEFAULT_SIGNAL_COLUMN = "water_level"


def make_series(
    n_days: int = 30,
    seed: int = 42,
    cadence_min: int = SRC_CADENCE_MIN,
    column: str = DEFAULT_SIGNAL_COLUMN,
) -> pd.DataFrame:
    """Synthetic 5-min signal series: daily morning + afternoon draw + nightly idle.

    `column` names the measurement column, so the same series can be emitted as a
    water_level CSV or a pressure_level CSV.
    """
    rng = np.random.default_rng(seed)
    start = datetime(2025, 4, 1, 0, 0, 0)
    cadence = timedelta(minutes=cadence_min)
    samples_per_day = int(24 * 60 / cadence_min)
    total_samples = n_days * samples_per_day
    timestamps = [start + i * cadence for i in range(total_samples)]
    level: list[float] = []
    lvl = 4.0
    for i in range(total_samples):
        t = timestamps[i]
        hour = t.hour
        minute = t.minute
        drop = 0.0
        # morning draw ~7-8:30, lunch ~12-13:30, afternoon ~15-17, evening ~19-20:30
        if (hour == 7 and minute >= 0) or (hour == 8 and minute < 30):
            drop = 0.012 + rng.normal(0, 0.0008)
        elif (hour == 12 and minute >= 0) or (hour == 13 and minute < 30):
            drop = 0.010 + rng.normal(0, 0.0006)
        elif 15 <= hour <= 16:
            drop = 0.015 + rng.normal(0, 0.0010)
        elif (hour == 19 and minute >= 0) or (hour == 20 and minute < 30):
            drop = 0.008 + rng.normal(0, 0.0005)
        else:
            drop = rng.normal(0, 0.00005)  # idle noise
        drop = max(drop, -0.0008)
        lvl = max(0.05, lvl - drop)
        # refill at 4AM-ish if low
        if hour == 4 and minute < cadence_min and lvl < 2.5:
            lvl = 4.0
        level.append(round(lvl, 4))
    return pd.DataFrame({"datetime": timestamps, column: level})


def df_to_csv_bytes(df: pd.DataFrame) -> bytes:
    out = io.StringIO()
    df.to_csv(out, index=False, date_format="%Y-%m-%d %H:%M:%S")
    return out.getvalue().encode("utf-8")


def slice_day(df: pd.DataFrame, day_index: int = 0) -> pd.DataFrame:
    cadence_min = SRC_CADENCE_MIN
    samples_per_day = int(24 * 60 / cadence_min)
    lo = day_index * samples_per_day
    # include one extra sample so the inclusive span is exactly 1440 min
    hi = lo + samples_per_day + 1
    return df.iloc[lo:hi].reset_index(drop=True)


def inject_slow_leak(
    df: pd.DataFrame,
    start_hour: int = 22,
    duration_min: int = 300,
    rate: float = 0.006,
    column: str = DEFAULT_SIGNAL_COLUMN,
) -> pd.DataFrame:
    """Add a downward ramp onto the day's signal starting at start_hour."""
    out = df.copy()
    dt = pd.to_datetime(out["datetime"])
    start_ts = dt.iloc[0].replace(hour=start_hour, minute=0, second=0)
    end_ts = start_ts + timedelta(minutes=duration_min)
    mask = (dt >= start_ts) & (dt <= end_ts)
    elapsed = (dt[mask] - start_ts).dt.total_seconds().to_numpy() / 60.0
    out.loc[mask, column] = out.loc[mask, column].to_numpy() - rate * elapsed
    out[column] = out[column].clip(lower=0.0)
    return out
