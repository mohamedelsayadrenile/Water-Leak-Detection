from __future__ import annotations

from typing import Any


class ValidationError(Exception):
    """Raised when uploaded CSV fails schema/cadence/length validation."""


class ProfileNotFound(Exception):
    def __init__(self, profile_id: str) -> None:
        super().__init__(f"profile not found: {profile_id}")
        self.profile_id = profile_id


class ProfileNotReady(Exception):
    def __init__(self, profile_id: str, status: str, error: str | None = None) -> None:
        super().__init__(f"profile {profile_id} not ready: status={status}")
        self.profile_id = profile_id
        self.status = status
        self.error = error


def error_payload(detail: str, type_: str = "error", **extra: Any) -> dict[str, Any]:
    payload = {"detail": detail, "type": type_}
    payload.update(extra)
    return payload
