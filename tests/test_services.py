from __future__ import annotations

import pytest

from src.core.config import settings
from src.core.errors import ValidationError
from src.services.cleaning import clean
from src.services.events import extract_events
from src.services.io import parse_csv
from src.services.profile import build_profile
from tests.fixtures import DEFAULT_SIGNAL_COLUMN as SIGNAL_COL
from tests.fixtures import df_to_csv_bytes, make_series


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


def test_parse_csv_interpolates_short_null_run():
    df = make_series(n_days=2)
    n_nulls = settings.max_interpolate_samples
    df.loc[100 : 100 + n_nulls - 1, SIGNAL_COL] = None

    out, summary = parse_csv(df_to_csv_bytes(df), settings)
    assert not out[settings.signal_column].isna().any()
    assert summary["null_values"] == n_nulls
    assert summary["missing_values"] == n_nulls
    assert summary["filled_gaps"] == n_nulls


def test_parse_csv_interpolates_nulls_at_series_edges():
    """Leading nulls need limit_direction='both' — forward-only never fills them."""
    df = make_series(n_days=2)
    df.loc[0:3, SIGNAL_COL] = None  # 4 nulls at the very start
    df.loc[len(df) - 4 :, SIGNAL_COL] = None  # and 4 at the very end

    out, summary = parse_csv(df_to_csv_bytes(df), settings)
    assert not out[settings.signal_column].isna().any()
    assert summary["filled_gaps"] == 8


def test_parse_csv_rejects_long_null_run():
    df = make_series(n_days=2)
    run = settings.max_interpolate_samples + 1
    df.loc[100 : 100 + run - 1, SIGNAL_COL] = None

    with pytest.raises(ValidationError, match=f"gap of {run} consecutive null values"):
        parse_csv(df_to_csv_bytes(df), settings)


def test_parse_csv_rejects_long_null_run_at_series_start():
    """A leading run must be judged by its length, not by pandas' fill direction."""
    df = make_series(n_days=2)
    run = settings.max_interpolate_samples + 1
    df.loc[0 : run - 1, SIGNAL_COL] = None

    with pytest.raises(ValidationError, match=f"gap of {run} consecutive null values"):
        parse_csv(df_to_csv_bytes(df), settings)


def test_parse_csv_reports_no_nulls_on_clean_series():
    df = make_series(n_days=2)
    _, summary = parse_csv(df_to_csv_bytes(df), settings)
    assert summary["null_values"] == 0
    assert summary["missing_values"] == 0
    assert summary["filled_gaps"] == 0
