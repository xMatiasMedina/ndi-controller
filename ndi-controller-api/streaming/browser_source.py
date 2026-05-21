"""
BrowserSource — receives video frames and audio samples from a browser's
getDisplayMedia stream via WebSocket and presents them as an IStreamSource
for the NDI streamer.

Flow:
  1. Frontend calls getDisplayMedia({ video: true, audio: true })
  2. Frontend draws each video frame to a canvas, exports as JPEG bytes
  3. Frontend captures audio via AudioWorklet / ScriptProcessorNode,
     encodes as raw float32 PCM
  4. Frontend sends JPEG video frames as binary WebSocket messages, and
     audio chunks as binary messages prefixed with a 4-byte magic header
  5. This source decodes each JPEG, resizes to fixed output resolution,
     and feeds audio into a ring buffer for the NDI audio loop

AUDIO PROTOCOL:
  Binary WebSocket messages are distinguished by a 4-byte magic prefix:
    b'AUD\\x00' + raw float32 PCM (interleaved stereo, 48 kHz)
  All other binary messages are treated as JPEG video frames.

LATEST-FRAME SEMANTICS:
  A live source should never show stale frames. Instead of a FIFO queue
  (which introduces N-frame latency), we keep only the most recent frame
  behind a lock.
"""
from __future__ import annotations

import struct
import threading
from typing import Iterator, Optional, Tuple

import cv2
import numpy as np

import config
from core.interfaces import IStreamSource

AUDIO_MAGIC = b"AUD\x00"


class BrowserSource(IStreamSource):
    """Receives JPEG frames and audio from a browser WebSocket."""

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

        # Latest-frame holder
        self._frame_lock = threading.Lock()
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_ready = threading.Event()

        # Audio ring buffer: 1 second capacity
        self._ring_size = config.AUDIO_SAMPLE_RATE
        self._ring_buf = np.zeros(
            (self._ring_size, config.AUDIO_CHANNELS), dtype=np.float32
        )
        self._ring_write = 0
        self._ring_read = 0
        self._ring_lock = threading.Lock()
        self._has_audio = False

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
        return self._has_audio

    def open(self) -> None:
        self._opened = True
        self._stop_event.clear()
        self._frame_ready.clear()
        with self._frame_lock:
            self._latest_frame = None
        with self._ring_lock:
            self._ring_write = 0
            self._ring_read = 0
        self._has_audio = False
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

    def push_frame(self, data: bytes) -> None:
        """Called by the WebSocket handler with raw binary data.

        If the data starts with AUDIO_MAGIC, it's treated as audio PCM.
        Otherwise it's treated as a JPEG video frame.
        """
        if not self._opened:
            return

        if data[:4] == AUDIO_MAGIC:
            self._push_audio(data[4:])
            return

        self._push_video(data)

    def _push_video(self, jpeg_bytes: bytes) -> None:
        """Decode JPEG and store as latest frame."""
        try:
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
        except Exception as e:
            print(f"[browser_source] push_video error: {e}")

    def _push_audio(self, pcm_bytes: bytes) -> None:
        """Write interleaved float32 PCM into the ring buffer."""
        try:
            samples = np.frombuffer(pcm_bytes, dtype=np.float32)
            # Interleaved stereo → (N, 2)
            n_channels = config.AUDIO_CHANNELS
            if samples.size % n_channels != 0:
                return  # malformed
            samples = samples.reshape(-1, n_channels)

            if not self._has_audio:
                self._has_audio = True
                print("[browser_source] audio stream detected")

            n = samples.shape[0]
            with self._ring_lock:
                end = self._ring_write + n
                if end <= self._ring_size:
                    self._ring_buf[self._ring_write:end] = samples
                else:
                    first = self._ring_size - self._ring_write
                    self._ring_buf[self._ring_write:] = samples[:first]
                    self._ring_buf[:n - first] = samples[first:]
                self._ring_write = end % self._ring_size
        except Exception as e:
            print(f"[browser_source] push_audio error: {e}")

    def get_audio_chunk(self, num_samples: int) -> np.ndarray:
        """Pull num_samples from the ring buffer. Called by the NDI audio loop."""
        with self._ring_lock:
            available = (self._ring_write - self._ring_read) % self._ring_size
            if available < num_samples:
                out = np.zeros(
                    (num_samples, config.AUDIO_CHANNELS), dtype=np.float32
                )
                if available > 0:
                    end = self._ring_read + available
                    if end <= self._ring_size:
                        out[:available] = self._ring_buf[self._ring_read:end]
                    else:
                        first = self._ring_size - self._ring_read
                        out[:first] = self._ring_buf[self._ring_read:]
                        out[first:available] = self._ring_buf[:available - first]
                    self._ring_read = (self._ring_read + available) % self._ring_size
                return out
            else:
                end = self._ring_read + num_samples
                if end <= self._ring_size:
                    out = self._ring_buf[self._ring_read:end].copy()
                else:
                    first = self._ring_size - self._ring_read
                    out = np.empty(
                        (num_samples, config.AUDIO_CHANNELS), dtype=np.float32
                    )
                    out[:first] = self._ring_buf[self._ring_read:]
                    out[first:] = self._ring_buf[:num_samples - first]
                self._ring_read = end % self._ring_size
                return out

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
                self._latest_frame = None

            if bgra is None:
                continue

            presentation_time = frame_index * period
            yield bgra, presentation_time
            frame_index += 1

    def get_audio(self) -> np.ndarray:
        # For live sources, the NDI streamer uses get_audio_chunk() instead.
        return np.zeros((0, config.AUDIO_CHANNELS), dtype=np.float32)
