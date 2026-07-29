from __future__ import annotations

import numpy as np
import pandas as pd

from src.core.config import Settings


def extract_events(df_in: pd.DataFrame, cfg: Settings) -> pd.DataFrame:
    """Stage 3 of the POC: continuous drain segments -> event rows."""
    mov = df_in["movement"].to_numpy()
    refill = df_in["is_refill"].to_numpy()
    lvl = df_in["level_smooth"].to_numpy()
    idx = df_in.index
    night_set = set(cfg.night_hours)

    events: list[dict] = []
    i = 0
    n = len(df_in)
    while i < n:
        if mov[i] == "drop" and not refill[i]:
            j = i
            last_drop = i
            while j < n:
                if mov[j] == "drop" and not refill[j]:
                    last_drop = j
                    j += 1
                elif mov[j] == "flat" and not refill[j]:
                    k = j
                    while k < n and mov[k] == "flat" and not refill[k]:
                        k += 1
                    flat_minutes = (idx[k - 1] - idx[j - 1]).total_seconds() / 60 if k > j else 0
                    if flat_minutes >= cfg.idle_gap_minutes or k >= n:
                        break
                    j = k
                else:
                    break
            end = last_drop
            seg_lvl = lvl[i : end + 1]
            seg_t = idx[i : end + 1]
            duration_min = (seg_t[-1] - seg_t[0]).total_seconds() / 60.0
            n_samples = end - i + 1
            if n_samples >= cfg.min_event_samples and duration_min > 0:
                drops = np.diff(seg_lvl)
                total_drop = float(seg_lvl[0] - seg_lvl[-1])
                mean_drop_rate = float(total_drop / duration_min) if duration_min else 0.0
                rate_variance = float(np.var(drops)) if len(drops) > 1 else 0.0
                start = seg_t[0]
                volume = total_drop * cfg.tank_area if cfg.tank_area is not None else None
                events.append(
                    {
                        "start_time": start,
                        "end_time": seg_t[-1],
                        "duration_minutes": duration_min,
                        "num_samples": n_samples,
                        "total_drop": total_drop,
                        "mean_drop_rate": mean_drop_rate,
                        "rate_variance": rate_variance,
                        "start_hour": start.hour,
                        "weekday": int(start.dayofweek),
                        "is_night": any(seg_t[h].hour in night_set for h in range(len(seg_t))),
                        "total_volume": volume,
                    }
                )
            i = max(end + 1, i + 1)
        else:
            i += 1

    return pd.DataFrame(events)
