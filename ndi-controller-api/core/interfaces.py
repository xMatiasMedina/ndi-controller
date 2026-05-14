"""
Abstract base classes for swappable components.

The point: ``PlayerService`` depends on ``IStreamSource`` (an interface), not on
``FileSource`` or ``ScreenSource`` (concrete classes). That means we can:
  - swap a video file for a screen capture without touching the player,
  - mock everything in tests,
  - add new source types (image, test pattern, RTSP, ...) without modifying
    existing code (Open/Closed principle).

Same logic applies to OBS, Reaper, persistence — they're all behind interfaces
so the rest of the app talks to capabilities, not implementations.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, List, Optional, Tuple

import numpy as np


class IStreamSource(ABC):
    """A source of video frames and (optionally) audio samples for the NDI streamer.

    The streamer doesn't care WHAT produces the data — file, screen, generator,
    network — only that it can call these methods.
    """

    # ----- Metadata -----
    @property
    @abstractmethod
    def width(self) -> int: ...

    @property
    @abstractmethod
    def height(self) -> int: ...

    @property
    @abstractmethod
    def fps(self) -> float: ...

    @property
    @abstractmethod
    def duration_seconds(self) -> float:
        """Total duration. Use 0 (or float('inf')) for live sources."""

    @property
    @abstractmethod
    def has_audio(self) -> bool: ...

    # ----- Lifecycle -----
    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def seek(self, position_seconds: float) -> None:
        """Seek to a position. No-op for live sources."""

    # ----- Data iteration -----
    @abstractmethod
    def iter_video(self) -> Iterator[Tuple[np.ndarray, float]]:
        """Yield (frame_bgra, presentation_time_seconds) until exhausted."""

    @abstractmethod
    def get_audio(self) -> np.ndarray:
        """Return the entire decoded audio buffer (N, channels) float32.

        For live sources without audio, return a zero-length array.
        """


class IObsClient(ABC):
    """OBS WebSocket v5 wrapper (Phase 2 implementation)."""

    @abstractmethod
    async def connect(self) -> bool: ...

    @abstractmethod
    async def disconnect(self) -> None: ...

    @abstractmethod
    def is_connected(self) -> bool: ...

    @abstractmethod
    async def list_scenes(self) -> List[str]: ...

    @abstractmethod
    async def get_current_scene(self) -> Optional[str]: ...

    @abstractmethod
    async def set_current_scene(self, name: str) -> bool: ...


class IReaperClient(ABC):
    """Reaper OSC wrapper (Phase 2 implementation)."""

    @abstractmethod
    def play(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def pause(self) -> None: ...

    @abstractmethod
    def record(self) -> None: ...

    @abstractmethod
    def seek(self, position_seconds: float) -> None: ...

    @abstractmethod
    def is_configured(self) -> bool: ...


class IPersistence(ABC):
    """Anything that can read/write our JSON files atomically."""

    @abstractmethod
    def load_json(self, path) -> Optional[dict]: ...

    @abstractmethod
    def save_json(self, path, data: dict) -> None: ...
