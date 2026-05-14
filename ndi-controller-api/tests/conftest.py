"""
Shared fixtures — mock implementations of all hardware-dependent interfaces
so tests run without NDI runtime, OBS, Reaper, or video files.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Iterator, List, Optional, Tuple
from unittest.mock import MagicMock

import numpy as np
import pytest

from core.interfaces import IObsClient, IPersistence, IReaperClient, IStreamSource
from core.models import PlaybackMode, PlaybackStatus, VideoAsset
from services.library_service import LibraryService
from services.playlist_service import PlaylistService
from services.settings_service import SettingsService


class FakeSource(IStreamSource):
    """Yields a fixed number of frames then stops."""

    def __init__(
        self, num_frames: int = 10, fps: float = 30.0, fail_on_frame: int = -1
    ) -> None:
        self._num_frames = num_frames
        self._fps = fps
        self._fail_on_frame = fail_on_frame
        self._opened = False
        self._closed = False

    @property
    def width(self) -> int:
        return 16

    @property
    def height(self) -> int:
        return 16

    @property
    def fps(self) -> float:
        return self._fps

    @property
    def duration_seconds(self) -> float:
        return self._num_frames / self._fps

    @property
    def has_audio(self) -> bool:
        return True

    def open(self) -> None:
        self._opened = True

    def close(self) -> None:
        self._closed = True

    def seek(self, position_seconds: float) -> None:
        pass

    def iter_video(self) -> Iterator[Tuple[np.ndarray, float]]:
        period = 1.0 / self._fps
        for i in range(self._num_frames):
            if i == self._fail_on_frame:
                raise RuntimeError("Simulated frame error")
            frame = np.zeros((16, 16, 4), dtype=np.uint8)
            yield frame, i * period

    def get_audio(self) -> np.ndarray:
        return np.zeros((960, 2), dtype=np.float32)


class InfiniteSource(FakeSource):
    """Yields frames forever until closed (for live-source tests)."""

    @property
    def duration_seconds(self) -> float:
        return 0.0

    def iter_video(self) -> Iterator[Tuple[np.ndarray, float]]:
        i = 0
        while self._opened and not self._closed:
            frame = np.zeros((16, 16, 4), dtype=np.uint8)
            yield frame, i / self._fps
            i += 1
            time.sleep(0.001)


class FakePersistence(IPersistence):
    def __init__(self) -> None:
        self._store: dict = {}

    def load_json(self, path) -> Optional[dict]:
        return self._store.get(str(path))

    def save_json(self, path, data: dict) -> None:
        self._store[str(path)] = data


class FakeObsClient(IObsClient):
    def __init__(self) -> None:
        self._connected = False

    async def connect(self) -> bool:
        self._connected = True
        return True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def list_scenes(self) -> List[str]:
        return ["Scene 1", "Scene 2"] if self._connected else []

    async def get_current_scene(self) -> Optional[str]:
        return "Scene 1" if self._connected else None

    async def set_current_scene(self, name: str) -> bool:
        return self._connected


class FakeReaperClient(IReaperClient):
    def __init__(self) -> None:
        self.calls: list[str] = []

    def play(self) -> None:
        self.calls.append("play")

    def stop(self) -> None:
        self.calls.append("stop")

    def pause(self) -> None:
        self.calls.append("pause")

    def record(self) -> None:
        self.calls.append("record")

    def seek(self, position_seconds: float) -> None:
        self.calls.append(f"seek:{position_seconds}")

    def is_configured(self) -> bool:
        return True


@pytest.fixture
def persistence():
    return FakePersistence()


@pytest.fixture
def library(persistence):
    return LibraryService(persistence)


@pytest.fixture
def playlists(persistence, library):
    return PlaylistService(persistence, library)


@pytest.fixture
def settings(persistence):
    return SettingsService(persistence)


@pytest.fixture
def obs():
    return FakeObsClient()


@pytest.fixture
def reaper():
    return FakeReaperClient()


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    loop.close()
