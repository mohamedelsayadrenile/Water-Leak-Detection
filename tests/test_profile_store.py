from __future__ import annotations

from datetime import timedelta

from src.repositories.profile_store import ProfileStore


def test_round_trip(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    pid = "a" * 32
    store.write_meta(pid, {"profile_id": pid, "status": "pending"})
    assert store.exists(pid)
    meta = store.load_meta(pid)
    assert meta["status"] == "pending"

    store.update_meta(pid, status="running")
    assert store.load_meta(pid)["status"] == "running"

    store.save_profile(pid, {"quiet_hours": [1, 2]})
    prof = store.load_profile(pid)
    assert prof["quiet_hours"] == [1, 2]

    store.update_meta(pid, status="ready", summary={"n_days": 30})
    assert store.load_meta(pid)["status"] == "ready"


def test_invalid_id_rejected(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    try:
        store.write_meta("../escape", {"x": 1})
    except ValueError:
        return
    raise AssertionError("expected ValueError for invalid profile_id")


def test_list_stale(tmp_path):
    store = ProfileStore(tmp_path / "profiles")
    pid = "b" * 32
    old = "2020-01-01T00:00:00+00:00"
    store.write_meta(pid, {"profile_id": pid, "status": "pending", "started_at": old})
    stale = store.list_stale(timedelta(minutes=10))
    assert any(m["profile_id"] == pid for m in stale)
