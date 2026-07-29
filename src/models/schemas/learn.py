from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class LearnResponse(BaseModel):
    profile_id: str
    status: Literal["pending"] = "pending"
