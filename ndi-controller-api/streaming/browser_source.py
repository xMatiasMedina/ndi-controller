"""
BrowserSource — receives video frames from a browser's getDisplayMedia stream
via WebSocket and presents them as an IStreamSource for the NDI streamer.

Flow:
  1. Frontend calls getDisplayMedia() to capture a screen/window/tab
  2. Frontend draws each frame to a canvas, exports as JPEG bytes
  3. Frontend sends the JPEG bytes over WebSocket to /ws/screen-share
  4. This source decodes each JPEG, resizes it to the fixed output resolution,
     and yields it to the NDI streamer

FIXED OUTPUT RESOLUTION:
  NDI pre-allocates its video buffer at sender init time using this source's
  declared width/height. The browser, however, sends frames at the user's
  actual capture resolution (whatever monitor/window they picked). To avoid
  "differing extents" errors when the buffer sizes don't match, every
  incoming frame is resized to a fixed output resolution (default 1920x1080)
  using cv2.resize.

LATEST-FRAME SEMANTICS:
  A live source should never show stale frames. Instead of a FIFO queue
  (which introduces N-frame latency), we keep only the most recent frame
  behind a lock. The producer (WebSocket handler) overwrites it on every
  push; the consumer (NDI video loop) always reads the latest and clears
  the holder so it cannot be re-read. This gives near-zero buffering delay
  and prevents duplicate-frame stuttering.

No audio — browser screen capture audio requires a separate MediaStreamTrack
pipeline that isn't worth the complexity in Phase 1.
"""
from __future__ import annotations

import threading
from typing import Iterator, Optional, Tuple

import cv2
import numpy as np

import config
from core.interfaces import IStreamSource


class BrowserSource(IStreamSource):
    """Receives JPEG frames pushed from a browser WebSocket."""

    def __init__(
        self,
        output_width: int = 1920,
        output_height: int = 1080,
        target_fps: float = 30.0,
    ) -> None:
        self._target_fps = target_fps
        self._width = output_width
        self._height = output_height
        self._opened = False
        self._stop_event = threading.Event()

        # Latest-frame holder: the producer overwrites, consumer reads+clears.
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_ready = threading.Event()

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
        return 0.0  # live

    @property
    def has_audio(self) -> bool:
        return False

    def open(self) -> None:
        self._opened = True
        self._stop_event.clear()
        self._frame_ready.clear()
        with self._frame_lock:
            self._latest_frame = None
        print(
            f"[browser_source] waiting for browser frames "
            f"(output {self._width}x{self._height} @ {self._target_fps} fps)"
        )

    def close(self) -> None:
        self._opened = False
        self._stop_event.set()
        self._frame_ready.set()  # unblock any waiting iter_video
        with self._frame_lock:
            self._latest_frame = None

    def seek(self, position_seconds: float) -> None:
        pass  # not meaningful for live

    def push_frame(self, jpeg_bytes: bytes) -> None:
        """Called by the WebSocket handler with raw JPEG data from the browser."""
        if not self._opened:
            return

        # Decode JPEG -> BGR numpy array
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return

        if frame.shape[0] != self._height or frame.shape[1] != self._width:
            frame = cv2.resize(
                frame,
                (self._width, self._height),
                interpolation=cv2.INTER_AREA,
            )

        bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)

        with self._frame_lock:
            self._latest_frame = bgra
        self._frame_ready.set()

    def iter_video(self) -> Iterator[Tuple[np.ndarray, float]]:
        """Yield (BGRA frame, presentation_time) as frames arrive from the browser."""
        if not self._opened:
            raise RuntimeError("BrowserSource not opened")

        period = 1.0 / self._target_fps
        frame_index = 0

        while self._opened and not self._stop_event.is_set():
            if not self._frame_ready.wait(timeout=0.5):
                continue

            self._frame_ready.clear()

            with self._frame_lock:
                bgra = self._latest_frame
                # Clear after read so this exact frame can never be re-yielded.
                # If push_frame() races between clear() and here, the new frame
                # is the one we read — frame_ready was re-set and will fire a
                # harmless no-op next iteration (bgra will be None -> continue).
                self._latest_frame = None

            if bgra is None:
                continue

            presentation_time = frame_index * period
            yield bgra, presentation_time
            frame_index += 1

    def get_audio(self) -> np.ndarray:
        return np.zeros((0, config.AUDIO_CHANNELS), dtype=np.float32)