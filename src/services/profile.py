from __future__ import annotations

from dataclasses import asdict

import numpy as np
import pandas as pd
from scipy.stats import linregress

from src.core.config import Config


def build_profile(events: pd.DataFrame, df_clean: pd.DataFrame, cfg: Config) -> dict:
    """Stage 4 of the POC — learned user behaviour.

    Returns a json-serializable dict. Per the global-config decision, the cfg
    snapshot is NOT embedded in the profile; thresholds come from live settings.
    """
    if len(events) == 0:
        n_days = int((df_clean.index[-1] - df_clean.index[0]).days + 1)
        return {
            "n_days": n_days,
            "hourly_active_prob": {h: 0.0 for h in range(24)},
            "per_hour_duration": {h: {"median": None, "p95": None, "count": 0} for h in range(24)},
            "quiet_hours": list(range(24)),
            "quiet_activity_threshold": 0.0,
            "global_duration": {"mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0},
            "quiet_baseline_slope": 0.0,
        }

    n_days = int((df_clean.index[-1] - df_clean.index[0]).days + 1)

    hourly_active = np.zeros(24)
    for _, e in events.iterrows():
        if pd.isna(e["start_time"]):
            continue
        covers = pd.date_range(e["start_time"], e["end_time"], freq="5min")
        days = covers.normalize().unique()
        hours = set(covers.hour)
        for h in hours:
            hourly_active[h] += len(days)
    hourly_active_prob = (hourly_active / n_days).clip(0, 1)

    per_hour_dur: dict[int, dict] = {}
    for h in range(24):
        eh = events[events["start_hour"] == h]["duration_minutes"]
        if len(eh):
            per_hour_dur[h] = {
                "median": float(eh.median()),
                "p95": float(eh.quantile(0.95)),
                "count": int(len(eh)),
            }
        else:
            per_hour_dur[h] = {"median": None, "p95": None, "count": 0}

    thresh = float(np.percentile(hourly_active_prob, cfg.low_activity_percentile))
    quiet_hours = [h for h in range(24) if hourly_active_prob[h] <= thresh + 1e-9]

    dur = events["duration_minutes"]
    global_dur = {
        "mean": float(dur.mean()),
        "median": float(dur.median()),
        "p95": float(dur.quantile(0.95)),
        "max": float(dur.max()),
    }

    quiet_mask = df_clean.index.hour.isin(quiet_hours)
    qs = df_clean.loc[quiet_mask & ~df_clean["is_refill"], "level_smooth"].dropna()
    slopes: list[float] = []
    if len(qs) > 12:
        for w_start in range(0, len(qs) - 12, 12):
            w = qs.iloc[w_start : w_start + 13]
            if len(w) >= 10:
                x = (w.index - w.index[0]).total_seconds().to_numpy() / 60.0
                res = linregress(x, w.to_numpy())
                slopes.append(res.slope)
    quiet_baseline_slope = float(np.median(slopes)) if slopes else 0.0

    return {
        "n_days": n_days,
        "hourly_active_prob": {int(h): float(p) for h, p in enumerate(hourly_active_prob)},
        "per_hour_duration": {int(h): per_hour_dur[h] for h in range(24)},
        "quiet_hours": sorted(quiet_hours),
        "quiet_activity_threshold": float(thresh),
        "global_duration": global_dur,
        "quiet_baseline_slope": quiet_baseline_slope,
    }


def make_profile_summary(profile: dict, n_events: int) -> dict:
    return {
        "n_days": profile["n_days"],
        "quiet_hours": profile["quiet_hours"],
        "global_duration": profile["global_duration"],
        "quiet_baseline_slope": profile["quiet_baseline_slope"],
        "n_events": int(n_events),
    }


def profile_config_snapshot(cfg: Config) -> dict:
    """Convenience kept for parity with the POC; not stored with the profile."""
    return asdict(cfg) if hasattr(cfg, "__dataclass_fields__") else dict(cfg)
