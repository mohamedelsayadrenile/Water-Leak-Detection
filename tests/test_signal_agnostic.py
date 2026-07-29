from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.core.config import settings
from src.core.errors import ValidationError
from src.core.helper import _build_detect_sync
from src.main import app
from src.services.cleaning import clean
from src.services.detection import detect
from src.services.events import extract_events
from src.services.io import parse_csv
from src.services.profile import build_profile
from tests.fixtures import df_to_csv_bytes, inject_slow_leak, make_series, slice_day
from tests.test_api import _wait_until_ready

WATER = "water_level"
PRESSURE = "pressure_level"


def _run_pipeline(csv_bytes: bytes, *, min_days=None, min_minutes=None):
    df, summary = parse_csv(
        csv_bytes, settings, expected_min_days=min_days, expected_min_minutes=min_minutes
    )
    df_clean = clean(df[settings.signal_column].astype(float), settings)
    return df_clean, extract_events(df_clean, settings), summary


# --- the acceptance check: both signals must produce identical detection output ---


@pytest.mark.parametrize("column", [WATER, PRESSURE])
def test_parse_csv_maps_source_column_to_signal(column):
    df = make_series(n_days=2, seed=21, column=column)
    parsed, summary = parse_csv(df_to_csv_bytes(df), settings)
    assert summary["signal_type"] == column
    assert settings.signal_column in parsed.columns
    assert column not in parsed.columns
    assert {"signal_min", "signal_max", "signal_mean"} <= set(summary)


def test_water_level_and_pressure_produce_identical_detection():
    """Same numbers, two different headers -> byte-identical alerts and confidence."""
    results = []
    for column in (WATER, PRESSURE):
        full = make_series(n_days=30, seed=13, column=column)
        day = inject_slow_leak(
            slice_day(full, day_index=2), start_hour=1, duration_min=300, rate=0.008, column=column
        )

        train_clean, train_events, _ = _run_pipeline(df_to_csv_bytes(full))
        # round-trip through JSON exactly as the profile store does: a freshly built
        # profile has int hour keys, a stored one has str keys, and only the stored
        # shape lets rule_a resolve per_hour_duration. Parity must cover rule A too.
        profile = json.loads(json.dumps(build_profile(train_events, train_clean, settings)))

        day_clean, day_events, _ = _run_pipeline(df_to_csv_bytes(day))
        alerts, confidence = detect(day_events, day_clean, profile, settings)
        results.append((profile, [a.to_dict() for a in alerts], confidence))

    water, pressure = results
    assert water[0] == pressure[0], "learned profiles diverged"
    assert water[1] == pressure[1], "alerts diverged"
    assert water[2] == pressure[2], "confidence diverged"
    assert water[1], "fixture produced no alerts — the parity check would be vacuous"
    assert {a["rule"] for a in water[1]} & {"A", "C"}, "event-driven rules never ran"


# --- source column validation ---


def test_rejects_csv_carrying_both_signals():
    df = make_series(n_days=2, seed=3)
    df[PRESSURE] = df[WATER]
    with pytest.raises(ValidationError, match="exactly one of"):
        parse_csv(df_to_csv_bytes(df), settings)


def test_rejects_csv_carrying_no_signal():
    df = make_series(n_days=2, seed=3).rename(columns={WATER: "humidity"})
    with pytest.raises(ValidationError, match="exactly one of"):
        parse_csv(df_to_csv_bytes(df), settings)


def test_learn_accepts_pressure_header(clean_env):
    client = TestClient(app)
    df = make_series(n_days=30, seed=17, column=PRESSURE)
    r = client.post("/v1/learn", files={"file": ("d.csv", df_to_csv_bytes(df), "text/csv")})
    assert r.status_code == 202, r.text
    meta = _wait_until_ready(client, r.json()["profile_id"])
    assert meta["status"] == "ready", meta
    assert meta["summary"]["signal_type"] == PRESSURE


# --- profile/signal mismatch guard ---


def test_detect_rejects_signal_type_mismatch(clean_env):
    client = TestClient(app)
    df = make_series(n_days=30, seed=19, column=WATER)
    r = client.post("/v1/learn", files={"file": ("d.csv", df_to_csv_bytes(df), "text/csv")})
    pid = r.json()["profile_id"]
    assert _wait_until_ready(client, pid)["status"] == "ready"

    day = slice_day(make_series(n_days=30, seed=19, column=PRESSURE), day_index=0)
    d = client.post(
        "/v1/detect",
        files={"file": ("day.csv", df_to_csv_bytes(day), "text/csv")},
        data={"profile_id": pid},
    )
    assert d.status_code == 422, d.text
    assert "signal type mismatch" in d.json()["detail"]


def test_detect_allows_legacy_profile_without_signal_type():
    """Profiles stored before signal types existed must keep working."""
    full = make_series(n_days=30, seed=23)
    train_clean, train_events, _ = _run_pipeline(df_to_csv_bytes(full))
    legacy_profile = build_profile(train_events, train_clean, settings)
    assert "signal_type" not in legacy_profile

    day = slice_day(full, day_index=0)
    alerts, n_events, confidence = _build_detect_sync(df_to_csv_bytes(day), legacy_profile)
    assert n_events >= 0
    assert 0.0 <= confidence <= 1.0
    assert isinstance(alerts, list)
