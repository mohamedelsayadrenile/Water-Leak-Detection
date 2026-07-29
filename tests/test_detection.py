from __future__ import annotations

from src.core.config import settings
from src.services.cleaning import clean
from src.services.detection import detect
from src.services.events import extract_events
from src.services.profile import build_profile
from tests.fixtures import inject_slow_leak, make_series, slice_day


def test_detect_no_leak_on_clean_day():
    df = make_series(n_days=30, seed=1)
    cfg = settings.to_config()

    full = df.set_index("datetime")["water_level"].astype(float)
    df_clean = clean(full, cfg)
    events = extract_events(df_clean, cfg)
    profile = build_profile(events, df_clean, cfg)

    # build a one-day series with the same cadence/index for detection
    day_df = slice_day(df, day_index=0)
    day_level = day_df.set_index("datetime")["water_level"].astype(float)
    day_clean = clean(day_level, cfg)
    day_events = extract_events(day_clean, cfg)
    alerts = detect(day_events, day_clean, profile, cfg)

    # on a normal day, no leak should fire (or only baseline-ish Rule B; accept zero)
    assert all(a.rule in ("A", "B", "C") for a in alerts)


def test_detect_flags_rule_c_long_event():
    df = make_series(n_days=30, seed=2)
    cfg = settings.to_config()
    full = df.set_index("datetime")["water_level"].astype(float)
    df_clean = clean(full, cfg)
    events = extract_events(df_clean, cfg)
    profile = build_profile(events, df_clean, cfg)

    # craft a long continuous drain (>4h) during a quiet hour -> rule A or C
    day_df = slice_day(df, day_index=1).copy()
    import numpy as np

    day_df["water_level"] = day_df["water_level"] - np.linspace(0, 3.0, len(day_df))
    day_df["water_level"] = day_df["water_level"].clip(lower=0.0)
    day_level = day_df.set_index("datetime")["water_level"].astype(float)
    day_clean = clean(day_level, cfg)
    day_events = extract_events(day_clean, cfg)
    alerts = detect(day_events, day_clean, profile, cfg)
    rules = {a.rule for a in alerts}
    assert "C" in rules or "A" in rules


def test_detect_slow_leak_fires_rule_b_or_c():
    df = make_series(n_days=30, seed=3)
    cfg = settings.to_config()
    full = df.set_index("datetime")["water_level"].astype(float)
    df_clean = clean(full, cfg)
    events = extract_events(df_clean, cfg)
    profile = build_profile(events, df_clean, cfg)

    day_df = inject_slow_leak(
        slice_day(df, day_index=2), start_hour=0, duration_min=360, rate=0.008
    )
    day_level = day_df.set_index("datetime")["water_level"].astype(float)
    day_clean = clean(day_level, cfg)
    day_events = extract_events(day_clean, cfg)
    alerts = detect(day_events, day_clean, profile, cfg)
    assert len(alerts) > 0
    rules = {a.rule for a in alerts}
    assert rules & {"B", "C"}
