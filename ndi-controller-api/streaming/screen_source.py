"""
ScreenSource — captures a monitor using ``mss`` at a target framerate.

Implements ``IStreamSource`` so the NDI streamer can use it interchangeably
with FileSource. This is a live source — duration is 0 (infinite), no audio,
and seek is a no-op.

Threading: ``iter_video()`` is called from the NDI streamer's video thread.
It grabs frames from the selected monitor at the target fps using a simple
sleep-based pacer. Good enough for 30 fps screen share; not designed for
60 fps gaming capture.
"""
from __future__ import annotations

import time
from typing import Iterator, Tuple

import cv2
import numpy as np

try:
    import mss
    import mss.tools

    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

import config
from core.interfaces import IStreamSource


class ScreenSource(IStreamSource):
    def __init__(self, monitor_index: int = 0, target_fps: float = 30.0) -> None:
        self._monitor_index = monitor_index
        self._target_fps = target_fps
        self._sct: mss.mss | None = None
        self._monitor: dict | None = None
        self._width = 0
        self._height = 0
        self._opened = False

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def fps(self) -> float:
        return self._target_fps

    @property
    def duration_seconds(self) -> float:
        return 0.0  # live — no fixed duration

    @property
    def has_audio(self) -> bool:
        return False  # mss doesn't capture audio

    def open(self) -> None:
        if not MSS_AVAILABLE:
            raise RuntimeError("mss not installed. pip install mss")

        self._sct = mss.mss()
        monitors = self._sct.monitors  # index 0 = all monitors combined

        # User-facing index: 0 = primary, 1 = second monitor, etc.
        # mss index: 0 = virtual (all), 1 = primary, 2 = second, ...
        mss_index = self._monitor_index + 1
        if mss_index >= len(monitors):
            mss_index = 1  # fallback to primary

        self._monitor = monitors[mss_index]
        self._width = self._monitor["width"]
        self._height = self._monitor["height"]
        self._opened = True
        print(
            f"[screen_source] capturing monitor {self._monitor_index} "
            f"({self._width}x{self._height}) @ {self._target_fps} fps"
        )

    def close(self) -> None:
        self._opened = False
        if self._sct is not None:
            self._sct.close()
            self._sct = None
        self._monitor = None

    def seek(self, position_seconds: float) -> None:
        pass  # not meaningful for a live source

    def iter_video(self) -> Iterator[Tuple[np.ndarray, float]]:
        """Yield (BGRA frame, monotonic timestamp) at target fps."""
        if not self._opened or self._sct is None or self._monitor is None:
            raise RuntimeError("ScreenSource not opened")

        period = 1.0 / self._target_fps
        t0 = time.perf_counter()
        frame_index = 0

        while self._opened:
            # Grab the screen region
            raw = self._sct.grab(self._monitor)

            # mss returns BGRA as a ctypes byte array; convert to numpy
            frame = np.frombuffer(raw.rgb, dtype=np.uint8).reshape(
                raw.height, raw.width, 3
            )
            # mss .rgb is actually RGB despite the name — convert to BGRA
            bgra = cv2.cvtColor(frame, cv2.COLOR_RGB2BGRA)

            presentation_time = frame_index * period
            yield bgra, presentation_time
            frame_index += 1

            # Pace to target fps
            target = t0 + (frame_index * period)
            dt = target - time.perf_counter()
            if dt > 0:
                time.sleep(dt)

    def get_audio(self) -> np.ndarray:
        return np.zeros((0, config.AUDIO_CHANNELS), dtype=np.float32)