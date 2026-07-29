from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.main import app
from tests.fixtures import df_to_csv_bytes, make_series


def _wait_until_ready(client: TestClient, profile_id: str, timeout: float = 30.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/v1/profiles/{profile_id}")
        assert r.status_code in (200, 202), r.text
        meta = r.json()
        if meta["status"] in ("ready", "failed"):
            return meta
        time.sleep(0.1)
    raise AssertionError(f"profile {profile_id} did not finish in time")


def test_learn_happy_path(clean_env):
    client = TestClient(app)
    df = make_series(n_days=30)
    csv_bytes = df_to_csv_bytes(df)
    r = client.post("/v1/learn", files={"file": ("data.csv", csv_bytes, "text/csv")})
    assert r.status_code == 202, r.text
    body = r.json()
    pid = body["profile_id"]
    assert body["status"] == "pending"

    meta = _wait_until_ready(client, pid)
    assert meta["status"] == "ready", meta
    assert meta["summary"]["n_days"] == 30
    assert isinstance(meta["summary"]["quiet_hours"], list)

    # profile file should exist now, csv uploaded removed
    from src.core.config import settings

    assert (settings.profile_dir / f"{pid}.json").exists()
    assert not (settings.uploads_dir / f"{pid}.csv").exists()


def test_learn_rejects_bad_header(clean_env):
    client = TestClient(app)
    bad = b"timestamp,foo\n2025-01-01 00:00:00,1"
    r = client.post("/v1/learn", files={"file": ("d.csv", bad, "text/csv")})
    assert r.status_code == 422


def test_learn_rejects_too_short(clean_env):
    client = TestClient(app)
    df = make_series(n_days=10)
    r = client.post("/v1/learn", files={"file": ("d.csv", df_to_csv_bytes(df), "text/csv")})
    # 422 because background task fails — but the request itself returns 202 with pending,
    # the failure surfaces via polling. Check the eventual status.
    assert r.status_code == 202
    meta = _wait_until_ready(client, r.json()["profile_id"])
    assert meta["status"] == "failed"


def test_detect_no_leak(clean_env):
    client = TestClient(app)
    df = make_series(n_days=30, seed=11)
    r = client.post("/v1/learn", files={"file": ("d.csv", df_to_csv_bytes(df), "text/csv")})
    pid = r.json()["profile_id"]
    meta = _wait_until_ready(client, pid)
    assert meta["status"] == "ready"

    from tests.fixtures import slice_day

    day_df = slice_day(df, day_index=0)
    d = client.post(
        "/v1/detect",
        files={"file": ("day.csv", df_to_csv_bytes(day_df), "text/csv")},
        data={"profile_id": pid},
    )
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["profile_id"] == pid
    assert body["leak_detected"] is False
    assert body["n_events"] >= 0
    assert isinstance(body["alerts"], list)
    assert 0.0 <= body["leak_confidence"] <= 1.0
    assert body["severity"] == "none"


def test_detect_unknown_profile(clean_env):
    client = TestClient(app)
    df = make_series(n_days=1, seed=5)
    bad = "0" * 32
    d = client.post(
        "/v1/detect",
        files={"file": ("d.csv", df_to_csv_bytes(df), "text/csv")},
        data={"profile_id": bad},
    )
    assert d.status_code == 404


def test_detect_pending_profile_returns_409(clean_env):
    client = TestClient(app)
    # learn then immediately try detect without waiting
    df = make_series(n_days=30, seed=7)
    r = client.post("/v1/learn", files={"file": ("d.csv", df_to_csv_bytes(df), "text/csv")})
    pid = r.json()["profile_id"]

    from tests.fixtures import slice_day

    day_df = slice_day(df, day_index=0)
    d = client.post(
        "/v1/detect",
        files={"file": ("day.csv", df_to_csv_bytes(day_df), "text/csv")},
        data={"profile_id": pid},
    )
    # Task may run synchronously in TestClient; accept 409 OR a real 200 once done
    assert d.status_code in (200, 409)
    # drain pending task for cleanliness
    _wait_until_ready(client, pid)


def test_detect_with_injected_leak(clean_env):
    client = TestClient(app)
    df = make_series(n_days=30, seed=9)
    r = client.post("/v1/learn", files={"file": ("d.csv", df_to_csv_bytes(df), "text/csv")})
    pid = r.json()["profile_id"]
    meta = _wait_until_ready(client, pid)
    assert meta["status"] == "ready"

    from tests.fixtures import inject_slow_leak, slice_day

    day_df = inject_slow_leak(
        slice_day(df, day_index=2), start_hour=1, duration_min=300, rate=0.008
    )
    d = client.post(
        "/v1/detect",
        files={"file": ("day.csv", df_to_csv_bytes(day_df), "text/csv")},
        data={"profile_id": pid},
    )
    assert d.status_code == 200, d.text
    body = d.json()
    assert body["leak_detected"] is True
    assert body["alerts"]
    assert 0.0 <= body["leak_confidence"] <= 1.0
    assert body["severity"] in ("low", "medium", "high")
    for a in body["alerts"]:
        assert 0.0 <= a["score"] <= 1.0
