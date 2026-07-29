from __future__ import annotations

import pytest


@pytest.fixture
def clean_env(monkeypatch, tmp_path):
    """Isolate profile_dir / uploads_dir per test."""
    from src.core.config import settings
    from src.repositories import profile_store

    pdir = tmp_path / "profiles"
    udir = tmp_path / "uploads"
    pdir.mkdir()
    udir.mkdir()
    monkeypatch.setattr(settings, "profile_dir", pdir)
    monkeypatch.setattr(settings, "uploads_dir", udir)
    # reset any cached store
    profile_store._store = None
    yield pdir, udir
