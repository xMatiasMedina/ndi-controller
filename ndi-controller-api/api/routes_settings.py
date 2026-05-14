"""HTTP adapter for the settings service."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from core.models import Settings
from services.settings_service import SettingsService

router = APIRouter(prefix="/api/settings", tags=["settings"])


def get_settings_service() -> SettingsService:
    raise NotImplementedError


@router.get("", response_model=Settings)
def get_settings(svc: SettingsService = Depends(get_settings_service)) -> Settings:
    return svc.get()


@router.put("", response_model=Settings)
def update_settings(
    settings: Settings,
    svc: SettingsService = Depends(get_settings_service),
) -> Settings:
    return svc.update(settings)
