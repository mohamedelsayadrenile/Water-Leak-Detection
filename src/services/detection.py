from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import pandas as pd
from scipy.stats import linregress

from src.core.config import Settings
from src.core.helper import _iso


@dataclass
class Alert:
    start: str
    end: str
    rule: str
    reason: str
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _hourly_active_prob(profile: dict, h: int) -> float:
    probs = profile["hourly_active_prob"]
    return float(probs[str(h)] if isinstance(probs, dict) else probs[h])


def _hour_dur_p95(profile: dict, h: int) -> float | None:
    per = profile["per_hour_duration"]
    return per[str(h)]["p95"] if isinstance(per, dict) else per[h]["p95"]


def rule_a(ev_row: pd.Series, profile: dict, cfg: Settings) -> Alert | None:
    h = int(ev_row["start_hour"])
    prob = _hourly_active_prob(profile, h)
    dur_p95 = _hour_dur_p95(profile, h)
    if dur_p95 is None:
        dur_p95 = profile["global_duration"]["p95"]
    if dur_p95 is None or dur_p95 <= 0:
        dur_p95 = profile["global_duration"]["p95"] or 0.0

    unusualness = clamp01(1.0 - (prob / cfg.rule_a_min_unusual_prob))
    duration_term = min(
        1.0, float(ev_row["duration_minutes"]) / (2.0 * cfg.rule_a_dur_mult * dur_p95)
    )
    score = unusualness * duration_term

    if score >= cfg.rule_min_score:
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
            float(score),
        )
    return None


def rule_b(df_in: pd.DataFrame, profile: dict, cfg: Settings) -> list[Alert]:
    alerts: list[Alert] = []
    quiet = set(profile["quiet_hours"])
    isq = df_in.index.hour.isin(quiet) & ~df_in["is_refill"]
    runs = (~isq.eq(isq.shift(1).fillna(False))).cumsum()
    baseline = profile["quiet_baseline_slope"]
    ref = max(abs(baseline) * cfg.rule_b_slope_mult, 1e-5)

    for _, g in df_in.groupby(runs):
        if not isq[g.index[0]]:
            continue
        sig = g["signal_smooth"].dropna()
        if len(sig) < 12:
            continue
        mins = (sig.index - sig.index[0]).total_seconds().to_numpy() / 60.0
        win_len = int(cfg.rule_b_min_quiet_hours * 60)
        step_samples = int(cfg.rule_b_step_minutes // 5)
        win_samples = int(win_len // 5)
        for w0 in range(0, len(sig) - win_samples + 1, step_samples):
            w1 = w0 + win_samples
            if w1 > len(sig):
                w1 = len(sig)
            x = mins[w0:w1]
            y = sig.to_numpy()[w0:w1]
            if len(x) < 10:
                continue
            res = linregress(x, y)
            if res.slope >= 0:
                continue
            significance = min(1.0, -math.log10(max(res.pvalue, 1e-300)) / 10.0)
            strength = min(1.0, (abs(res.slope) / ref) / 10.0)
            score = significance * strength
            if score < cfg.rule_min_score:
                continue
            ts_start = sig.index[w0]
            ts_end = sig.index[w1 - 1]
            reason = (
                f"Rule B: slow drain {res.slope:.5f} signal/min over "
                f"{(w1 - w0 - 1) * 5} min from {ts_start.time()} "
                f"(p={res.pvalue:.2e}, baseline {baseline:.5f})"
            )
            if not any(a.start == ts_start.isoformat() for a in alerts):
                alerts.append(
                    Alert(ts_start.isoformat(), ts_end.isoformat(), "B", reason, float(score))
                )

    alerts.sort(key=lambda a: a.start)
    merged: list[Alert] = []
    for a in alerts:
        if (
            merged
            and (pd.Timestamp(a.start) - pd.Timestamp(merged[-1].end)).total_seconds() < 90 * 60
        ):
            merged[-1].end = max(merged[-1].end, a.end)
            merged[-1].score = max(merged[-1].score, a.score)
        else:
            merged.append(a)
    return merged


def rule_c(ev_row: pd.Series, cfg: Settings) -> Alert | None:
    cap_min = cfg.rule_c_max_duration_hours * 60.0
    score = min(1.0, float(ev_row["duration_minutes"]) / (2.0 * cap_min))
    if score >= cfg.rule_min_score:
        reason = (
            f"Rule C: event from {ev_row['start_time']} lasted "
            f"{ev_row['duration_minutes']:.0f} min > "
            f"{cfg.rule_c_max_duration_hours:.0f}h threshold"
        )
        return Alert(
            _iso(ev_row["start_time"]), _iso(ev_row["end_time"]), "C", reason, float(score)
        )
    return None


def detect(
    events_in: pd.DataFrame, df_in: pd.DataFrame, profile: dict, cfg: Settings
) -> tuple[list[Alert], float]:
    """Run all three soft-gate rules and combine their evidence into a day confidence."""
    alerts: list[Alert] = []
    if len(events_in) > 0:
        for _, e in events_in.iterrows():
            try:
                aA = rule_a(e, profile, cfg)
                if aA:
                    alerts.append(aA)
            except Exception:
                pass
            aC = rule_c(e, cfg)
            if aC:
                alerts.append(aC)
    alerts.extend(rule_b(df_in, profile, cfg))
    confidence = aggregate_confidence(alerts)
    return alerts, confidence


def aggregate_confidence(alerts: list[Alert]) -> float:
    """Probabilistic union of alert scores: 1 - Π(1 - score_i)."""
    prod = 1.0
    for a in alerts:
        prod *= max(0.0, 1.0 - a.score)
    return 1.0 - prod


def severity_for(confidence: float, cfg: Settings) -> str:
    """Map day confidence to a severity label using the configured bands."""
    if confidence < cfg.leak_confidence_threshold:
        return "none"
    if confidence < cfg.severity_medium_threshold:
        return "low"
    if confidence < cfg.severity_high_threshold:
        return "medium"
    return "high"
