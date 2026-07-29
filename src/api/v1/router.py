from __future__ import annotations

from fastapi import APIRouter

from src.api.v1.endpoints import detect, learn, profiles

v1_router = APIRouter()
v1_router.include_router(learn.router, tags=["learn"])
v1_router.include_router(profiles.router, tags=["profiles"])
v1_router.include_router(detect.router, tags=["detect"])
