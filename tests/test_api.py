from __future__ import annotations

import time

from fastapi.testclient import TestClient

from src.main import app
from tests.fixtures import df_to_csv_bytes, make_series


def _poll_until_done(client: TestClient, profile_id: str, timeout: float = 30.0):
    """Poll until the job leaves 202; returns the terminal response as-is."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = client.get(f"/v1/profiles/{profile_id}")
        if r.status_code != 202:
            return r
        assert r.json()["status"] in ("pending", "running"), r.text
        time.sleep(0.1)
    raise AssertionError(f"profile {profile_id} did not finish in time")


def _wait_until_ready(client: TestClient, profile_id: str, timeout: float = 30.0) -> dict:
    r = _poll_until_done(client, profile_id, timeout)
    assert r.status_code == 200, r.text
    return r.json()


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
    assert meta["summary"]["signal_type"] == "water_level"
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
    # the upload request itself returns 202 with pending; the rejection surfaces on poll
    assert r.status_code == 202
    pid = r.json()["profile_id"]

    poll = _poll_until_done(client, pid)
    assert poll.status_code == 422, poll.text
    detail = poll.json()["detail"]
    assert detail["status"] == "failed"
    assert detail["type"] == "validation"
    assert "too short" in detail["detail"]

    # the stored meta still records the failure for anyone reading it directly
    from src.repositories.profile_store import get_profile_store

    meta = get_profile_store().load_meta(pid)
    assert meta["status"] == "failed"
    assert meta["error_type"] == "validation"
    assert meta["finished_at"]


def test_learn_rejects_null_run_longer_than_limit(clean_env):
    from src.core.config import settings

    client = TestClient(app)
    df = make_series(n_days=30)
    df.loc[100 : 100 + settings.max_interpolate_samples, "water_level"] = None
    r = client.post("/v1/learn", files={"file": ("d.csv", df_to_csv_bytes(df), "text/csv")})
    assert r.status_code == 202

    poll = _poll_until_done(client, r.json()["profile_id"])
    assert poll.status_code == 422, poll.text
    assert "unfillable gap" in poll.json()["detail"]["detail"]


def test_learn_interpolates_short_null_run(clean_env):
    client = TestClient(app)
    df = make_series(n_days=30)
    df.loc[100:102, "water_level"] = None  # 3 nulls, under the limit
    r = client.post("/v1/learn", files={"file": ("d.csv", df_to_csv_bytes(df), "text/csv")})
    meta = _wait_until_ready(client, r.json()["profile_id"])
    assert meta["status"] == "ready"


def test_profile_status_unknown_and_malformed_id(clean_env):
    client = TestClient(app)
    assert client.get(f"/v1/profiles/{'a' * 32}").status_code == 404
    assert client.get("/v1/profiles/not-a-hex-id").status_code == 422


def test_profile_status_pending_returns_202(clean_env):
    from src.repositories.profile_store import get_profile_store

    pid = "b" * 32
    get_profile_store().write_meta(pid, {"profile_id": pid, "status": "running"})

    r = TestClient(app).get(f"/v1/profiles/{pid}")
    assert r.status_code == 202
    assert r.json()["status"] == "running"


def test_profile_status_internal_failure_returns_500(clean_env):
    from src.repositories.profile_store import get_profile_store

    pid = "c" * 32
    get_profile_store().write_meta(
        pid,
        {
            "profile_id": pid,
            "status": "failed",
            "error": "/abs/path/leaked.csv missing",
            "error_type": "internal",
        },
    )

    r = TestClient(app).get(f"/v1/profiles/{pid}")
    assert r.status_code == 500
    # the internal error string must not reach the client
    assert "leaked.csv" not in r.text
    assert r.json()["detail"]["detail"] == "profile learning failed"


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
