"""Settings service — single source of truth for user-configurable options."""
from __future__ import annotations

import config
from core.interfaces import IPersistence
from core.models import Settings


class SettingsService:
    def __init__(self, persistence: IPersistence) -> None:
        self._persistence = persistence
        self._settings = self._load()

    def _load(self) -> Settings:
        data = self._persistence.load_json(config.SETTINGS_FILE)
        if data:
            try:
                return Settings(**data)
            except Exception as e:
                print(f"[settings] invalid settings file, using defaults: {e}")
        return Settings()

    def _save(self) -> None:
        self._persistence.save_json(config.SETTINGS_FILE, self._settings.model_dump())

    def get(self) -> Settings:
        return self._settings.model_copy(deep=True)

    def update(self, new_settings: Settings) -> Settings:
        self._settings = new_settings
        self._save()
        return self.get()
