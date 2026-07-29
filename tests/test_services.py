from __future__ import annotations

from src.core.config import settings
from src.services.cleaning import clean
from src.services.events import extract_events
from src.services.profile import build_profile
from tests.fixtures import DEFAULT_SIGNAL_COLUMN as SIGNAL_COL
from tests.fixtures import make_series


def test_pipeline_extracts_events_and_profile():
    df = make_series(n_days=30)
    cfg = settings
    level = df.set_index("datetime")[SIGNAL_COL].astype(float)
    df_clean = clean(level, cfg)
    events = extract_events(df_clean, cfg)
    assert len(events) > 0
    assert "duration_minutes" in events.columns

    profile = build_profile(events, df_clean, cfg)
    assert profile["n_days"] == 30
    assert isinstance(profile["quiet_hours"], list)
    assert 0 < len(profile["quiet_hours"]) <= 24
    assert profile["global_duration"]["max"] > 0
    for h in range(24):
        assert str(h) in {str(k) for k in profile["hourly_active_prob"]}


def test_quiet_hours_exclude_busy_hours():
    df = make_series(n_days=30)
    cfg = settings
    level = df.set_index("datetime")[SIGNAL_COL].astype(float)
    df_clean = clean(level, cfg)
    events = extract_events(df_clean, cfg)
    profile = build_profile(events, df_clean, cfg)
    busy = [7, 13, 15, 19]
    for h in busy:
        # busy hours should not be quiet (prob high)
        assert profile["hourly_active_prob"][h] > profile["quiet_activity_threshold"]
