"""
Development / deployment configuration.

These are parameters that control how the application behaves in the
deployment environment — distinct from the user-facing settings that
are edited in the web UI (Settings panel → settings.json).

Persisted to data/dev_config.json. Missing keys fall back to defaults.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

import config as paths


_DEV_CONFIG_FILE = paths.DATA_DIR / "dev_config.json"


class ScreenShareObs(BaseModel):
    """OBS scene/source that the app auto-provisions for screen sharing."""
    scene_name: str = "StreamScreen"
    source_name: str = "StreamScreen"
    ndi_source_name: str = "OBS Video"  # NDI source name to receive from
    ndi_input_kind: str = "ndi_source"  # OBS input kind for NDI plugin
    switch_delay_sec: float = 2.0       # delay before reverting scene after share ends


class DevConfig(BaseModel):
    """Top-level dev/deployment config."""
    screen_share_obs: ScreenShareObs = Field(default_factory=ScreenShareObs)


# ---- Singleton load/save ----

_instance: Optional[DevConfig] = None


def get() -> DevConfig:
    """Return the cached config, loading from disk on first call."""
    global _instance
    if _instance is None:
        _instance = _load()
    return _instance


def save(cfg: DevConfig) -> None:
    """Persist to disk and update the cache."""
    global _instance
    _instance = cfg
    _DEV_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _DEV_CONFIG_FILE.write_text(
        cfg.model_dump_json(indent=2), encoding="utf-8"
    )
    print(f"[dev_config] saved to {_DEV_CONFIG_FILE}")


def _load() -> DevConfig:
    if _DEV_CONFIG_FILE.exists():
        try:
            data = json.loads(_DEV_CONFIG_FILE.read_text(encoding="utf-8"))
            cfg = DevConfig(**data)
            print(f"[dev_config] loaded from {_DEV_CONFIG_FILE}")
            return cfg
        except Exception as e:
            print(f"[dev_config] failed to load {_DEV_CONFIG_FILE}: {e}")
    # First run — create with defaults and persist
    cfg = DevConfig()
    save(cfg)
    return cfg
