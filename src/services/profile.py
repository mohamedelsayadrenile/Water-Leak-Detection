from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import linregress

from src.core.config import Settings


def _refill_stats(df_clean: pd.DataFrame) -> tuple[dict[int, float], list[int], float | None]:
    """Per-hour refill probability, typical refill hours, and the lowest raw signal.

    - ``refill_hourly_prob[h]`` = fraction of samples in hour *h* flagged ``is_refill``.
    - ``refill_hours`` = hours whose refill probability exceeds the cross-hour mean
      (the hours that account for most refill activity).
    - ``min_signal`` = lowest observed ``signal_raw`` value; unit follows ``signal_type``
      (metres for ``water_level``, bar for ``pressure_level``).
    """
    refill_hourly_prob: dict[int, float] = {h: 0.0 for h in range(24)}
    if len(df_clean):
        for h, g in df_clean.groupby(df_clean.index.hour)["is_refill"]:
            refill_hourly_prob[int(h)] = float(g.mean()) if len(g) else 0.0
    if any(refill_hourly_prob.values()):
        mean_prob = float(np.mean(list(refill_hourly_prob.values())))
        refill_hours = sorted(h for h, p in refill_hourly_prob.items() if p > mean_prob)
    else:
        refill_hours = []
    min_signal = float(df_clean["signal_raw"].min()) if len(df_clean) else None
    return refill_hourly_prob, refill_hours, min_signal


def _usage_amount_by_hour(events: pd.DataFrame, n_days: int) -> dict[int, float]:
    """Per-hour average daily water consumed.

    ``hourly_usage_amount[h]`` = sum of every event's ``total_drop`` that started in
    hour *h*, divided by ``n_days`` — i.e. the average water volume consumed during
    that hour of the day across the learning period. Zero where no event starts.
    """
    hourly_usage_amount: dict[int, float] = {h: 0.0 for h in range(24)}
    if len(events) and n_days:
        for h, total in events.groupby("start_hour")["total_drop"].sum().items():
            hourly_usage_amount[int(h)] = float(total) / n_days
    return hourly_usage_amount


def _lowest_usage_hours(hourly_usage_amount: dict[int, float]) -> list[int]:
    """The six hours of the day with the lowest water usage.

    Methodology: rank the 24 hours by ``hourly_usage_amount`` (sum of event
    ``total_drop`` per start hour / ``n_days``), ascending; ties break by hour index.
    The six smallest are returned. Quiet hours naturally surface here because their
    summed drop volume is near zero.
    """
    ranked = sorted(hourly_usage_amount.items(), key=lambda kv: (kv[1], kv[0]))
    return [h for h, _ in ranked[:6]]


def build_profile(events: pd.DataFrame, df_clean: pd.DataFrame, cfg: Settings) -> dict:
    """Stage 4 of the POC — learned user behaviour.

    Returns a json-serializable dict. Per the global-config decision, the cfg
    snapshot is NOT embedded in the profile; thresholds come from live settings.
    """
    refill_hourly_prob, refill_hours, min_signal = _refill_stats(df_clean)

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
            "total_drop_mean": None,
            "hourly_usage_amount": {h: 0.0 for h in range(24)},
            "lowest_usage_hours": [],
            "refill_hourly_prob": refill_hourly_prob,
            "refill_hours": refill_hours,
            "min_signal": min_signal,
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
    qs = df_clean.loc[quiet_mask & ~df_clean["is_refill"], "signal_smooth"].dropna()
    slopes: list[float] = []
    if len(qs) > 12:
        for w_start in range(0, len(qs) - 12, 12):
            w = qs.iloc[w_start : w_start + 13]
            if len(w) >= 10:
                x = (w.index - w.index[0]).total_seconds().to_numpy() / 60.0
                res = linregress(x, w.to_numpy())
                slopes.append(res.slope)
    quiet_baseline_slope = float(np.median(slopes)) if slopes else 0.0

    hourly_usage_amount = _usage_amount_by_hour(events, n_days)
    total_drop_mean = float(events["total_drop"].mean())
    lowest_usage_hours = _lowest_usage_hours(hourly_usage_amount)

    return {
        "n_days": n_days,
        "hourly_active_prob": {int(h): float(p) for h, p in enumerate(hourly_active_prob)},
        "per_hour_duration": {int(h): per_hour_dur[h] for h in range(24)},
        "quiet_hours": sorted(quiet_hours),
        "quiet_activity_threshold": float(thresh),
        "global_duration": global_dur,
        "quiet_baseline_slope": quiet_baseline_slope,
        "total_drop_mean": total_drop_mean,
        "hourly_usage_amount": {int(h): float(v) for h, v in hourly_usage_amount.items()},
        "lowest_usage_hours": lowest_usage_hours,
        "refill_hourly_prob": {int(h): float(p) for h, p in refill_hourly_prob.items()},
        "refill_hours": refill_hours,
        "min_signal": min_signal,
    }


def make_profile_summary(profile: dict, n_events: int) -> dict:
    # scheduled_usage_hours = complement of quiet_hours: hours whose activity
    # probability exceeds the quiet threshold.
    hourly = profile.get("hourly_active_prob", {})
    thresh = profile.get("quiet_activity_threshold", 0.0)
    scheduled_usage_hours = sorted(int(h) for h, p in hourly.items() if float(p) > thresh + 1e-9)
    return {
        "signal_type": profile.get("signal_type"),
        "n_days": profile["n_days"],
        "quiet_hours": profile["quiet_hours"],
        "global_duration": profile["global_duration"],
        "quiet_baseline_slope": profile["quiet_baseline_slope"],
        "n_events": int(n_events),
        "scheduled_usage_hours": scheduled_usage_hours,
        "average_usage_amount": profile.get("total_drop_mean"),
        "refill_periods": profile.get("refill_hours", []),
        "min_tank_level": profile.get("min_signal"),
        "average_usage_duration": profile["global_duration"]["mean"],
        "lowest_usage_hours": profile.get("lowest_usage_hours", []),
    }
