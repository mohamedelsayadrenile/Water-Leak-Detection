from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd
from scipy.stats import linregress

from src.core.config import Config


@dataclass
class Alert:
    start: str
    end: str
    rule: str
    reason: str
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


def _iso(ts: pd.Timestamp) -> str:
    return pd.Timestamp(ts).isoformat()


def rule_a(ev_row: pd.Series, profile: dict, cfg: Config) -> Alert | None:
    h = int(ev_row["start_hour"])
    prob = (
        profile["hourly_active_prob"][str(h)]
        if isinstance(profile["hourly_active_prob"], dict)
        else profile["hourly_active_prob"][h]
    )
    dur_p95 = (
        profile["per_hour_duration"][str(h)]["p95"]
        if isinstance(profile["per_hour_duration"], dict)
        else profile["per_hour_duration"][h]["p95"]
    )
    if dur_p95 is None:
        dur_p95 = profile["global_duration"]["p95"]
    if (
        prob < cfg.rule_a_min_unusual_prob
        and ev_row["duration_minutes"] > cfg.rule_a_dur_mult * dur_p95
        and ev_row["duration_minutes"] >= 30
    ):
        reason = (
            f"Rule A: event at {ev_row['start_time']} lasted "
            f"{ev_row['duration_minutes']:.0f} min vs p95 {dur_p95:.0f} min "
            f"at hour {h:02d} (activity prob {prob:.2f})"
        )
        return Alert(
            _iso(ev_row["start_time"]),
            _iso(ev_row["end_time"]),
            "A",
            reason,
            float(ev_row["duration_minutes"]),
        )
    return None


def rule_a_safe(ev_row: pd.Series, profile: dict, cfg: Config) -> Alert | None:
    try:
        return rule_a(ev_row, profile, cfg)
    except Exception:
        return None


def rule_b(df_in: pd.DataFrame, profile: dict, cfg: Config) -> list[Alert]:
    alerts: list[Alert] = []
    quiet = set(profile["quiet_hours"])
    isq = df_in.index.hour.isin(quiet) & ~df_in["is_refill"]
    runs = (~isq.eq(isq.shift(1).fillna(False))).cumsum()
    for _, g in df_in.groupby(runs):
        if not isq[g.index[0]]:
            continue
        lvl = g["level_smooth"].dropna()
        if len(lvl) < 12:
            continue
        mins = (lvl.index - lvl.index[0]).total_seconds().to_numpy() / 60.0
        win_len = int(cfg.rule_b_min_quiet_hours * 60)
        step = cfg.rule_b_step_minutes
        baseline = profile["quiet_baseline_slope"]
        step_samples = int(step // 5)
        win_samples = int(win_len // 5)
        for w0 in range(0, len(lvl) - win_samples + 1, step_samples):
            w1 = w0 + win_samples
            if w1 > len(lvl):
                w1 = len(lvl)
            x = mins[w0:w1]
            y = lvl.to_numpy()[w0:w1]
            if len(x) < 10:
                continue
            res = linregress(x, y)
            if (
                res.slope < 0
                and res.pvalue < cfg.rule_b_alpha
                and abs(res.slope) > abs(baseline) * cfg.rule_b_slope_mult
                and abs(res.slope) > cfg.sensor_deadband / 5
            ):
                ts_start = lvl.index[w0]
                ts_end = lvl.index[w1 - 1]
                reason = (
                    f"Rule B: slow drain {res.slope:.5f} lvl/min over "
                    f"{(w1 - w0 - 1) * 5} min from {ts_start.time()} "
                    f"(p={res.pvalue:.2e}, baseline {baseline:.5f})"
                )
                if not any(a.start == ts_start.isoformat() for a in alerts):
                    alerts.append(
                        Alert(
                            ts_start.isoformat(),
                            ts_end.isoformat(),
                            "B",
                            reason,
                            float(abs(res.slope)),
                        )
                    )
    alerts.sort(key=lambda a: a.start)
    merged: list[Alert] = []
    for a in alerts:
        if (
            merged
            and (pd.Timestamp(a.start) - pd.Timestamp(merged[-1].end)).total_seconds() < 90 * 60
        ):
            merged[-1].end = max(merged[-1].end, a.end)
        else:
            merged.append(a)
    return merged


def rule_c(ev_row: pd.Series, cfg: Config) -> Alert | None:
    if ev_row["duration_minutes"] > cfg.rule_c_max_duration_hours * 60:
        reason = (
            f"Rule C: event from {ev_row['start_time']} lasted "
            f"{ev_row['duration_minutes']:.0f} min > "
            f"{cfg.rule_c_max_duration_hours:.0f}h cap"
        )
        return Alert(
            _iso(ev_row["start_time"]),
            _iso(ev_row["end_time"]),
            "C",
            reason,
            float(ev_row["duration_minutes"]),
        )
    return None


def detect(events_in: pd.DataFrame, df_in: pd.DataFrame, profile: dict, cfg: Config) -> list[Alert]:
    alerts: list[Alert] = []
    if len(events_in) > 0:
        qh = set(profile["quiet_hours"])
        for _, e in events_in.iterrows():
            aA = rule_a_safe(e, profile, cfg)
            if aA:
                alerts.append(aA)
            aC = rule_c(e, cfg)
            if aC:
                alerts.append(aC)
        # is_quiet_hour is informational; not serialized in alerts
        _ = qh
    alerts.extend(rule_b(df_in, profile, cfg))
    return alerts
