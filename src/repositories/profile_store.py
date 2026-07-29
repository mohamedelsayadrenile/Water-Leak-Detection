from __future__ import annotations

import json
import re
from datetime import timedelta
from typing import Any

from src.core.config import settings
from src.core.errors import ProfileNotFound

PROFILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def _validate_profile_id(profile_id: str) -> None:
    if not PROFILE_ID_RE.match(profile_id):
        raise ValueError(f"invalid profile_id: {profile_id!r}")


def _meta_path(profile_id: str, root) -> Any:
    return root / f"{profile_id}.meta.json"


def _profile_path(profile_id: str, root) -> Any:
    return root / f"{profile_id}.json"


class ProfileStore:
    def __init__(self, profile_dir=None) -> None:
        self.dir = profile_dir if profile_dir is not None else settings.profile_dir
        self.dir.mkdir(parents=True, exist_ok=True)

    def exists(self, profile_id: str) -> bool:
        _validate_profile_id(profile_id)
        return _meta_path(profile_id, self.dir).exists()

    def write_meta(self, profile_id: str, meta: dict) -> None:
        _validate_profile_id(profile_id)
        _meta_path(profile_id, self.dir).write_text(json.dumps(meta, default=str))

    def load_meta(self, profile_id: str) -> dict | None:
        _validate_profile_id(profile_id)
        path = _meta_path(profile_id, self.dir)
        if not path.exists():
            return None
        return json.loads(path.read_text())

    def update_meta(self, profile_id: str, **fields) -> dict:
        meta = self.load_meta(profile_id) or {}
        meta.update(fields)
        self.write_meta(profile_id, meta)
        return meta

    def save_profile(self, profile_id: str, profile: dict) -> None:
        _validate_profile_id(profile_id)
        _profile_path(profile_id, self.dir).write_text(json.dumps(profile, indent=2, default=str))

    def load_profile(self, profile_id: str) -> dict:
        _validate_profile_id(profile_id)
        path = _profile_path(profile_id, self.dir)
        if not path.exists():
            # Maybe still pending / failed — give caller a precise error
            meta = self.load_meta(profile_id)
            if meta is None:
                raise ProfileNotFound(profile_id)
            raise ProfileNotFound(profile_id)
        return json.loads(path.read_text())

    def list_stale(self, older_than: timedelta) -> list[dict]:
        import time

        cutoff_seconds = time.time() - older_than.total_seconds()
        stale: list[dict] = []
        for p in self.dir.glob("*.meta.json"):
            try:
                meta = json.loads(p.read_text())
            except Exception:
                continue
            if meta.get("status") not in ("pending", "running"):
                continue
            started = meta.get("started_at")
            if not started:
                continue
            try:
                from datetime import datetime

                ts = datetime.fromisoformat(started).timestamp()
            except Exception:
                continue
            if ts < cutoff_seconds:
                stale.append(meta)
        return stale


_store: ProfileStore | None = None


def get_profile_store() -> ProfileStore:
    global _store
    if _store is None:
        _store = ProfileStore()
    return _store
