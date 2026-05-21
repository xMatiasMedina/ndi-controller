"""
ScreenSource — captures a monitor using ``mss`` at a target framerate,
with optional system audio loopback via ``sounddevice``.

Implements ``IStreamSource`` so the NDI streamer can use it interchangeably
with FileSource. This is a live source — duration is 0 (infinite), and seek
is a no-op.

Audio capture:
  Uses sounddevice with a WASAPI loopback device (Windows) or PulseAudio/
  PipeWire monitor source (Linux). If no loopback device is found or
  sounddevice is unavailable, falls back to silence gracefully.

  Audio is captured into a thread-safe ring buffer. The NDI audio loop calls
  get_audio_chunk() every 20 ms to pull the latest samples — this replaces
  the old get_audio() bulk-decode model used by FileSource.

Threading: ``iter_video()`` is called from the NDI streamer's video thread.
Audio capture runs in its own callback-driven thread managed by sounddevice.
"""
from __future__ import annotations

import sys
import threading
import time
from typing import Iterator, Optional, Tuple

import cv2
import numpy as np

try:
    import mss
    import mss.tools

    MSS_AVAILABLE = True
except ImportError:
    MSS_AVAILABLE = False

try:
    import sounddevice as sd

    SD_AVAILABLE = True
except ImportError:
    SD_AVAILABLE = False

import config
from core.interfaces import IStreamSource


def _find_loopback_device() -> Optional[int]:
    """Find a system audio loopback device for capturing desktop audio."""
    if not SD_AVAILABLE:
        return None

    try:
        devices = sd.query_devices()
    except Exception:
        return None

    # Windows WASAPI loopback — sounddevice/PortAudio exposes these as input
    # devices with hostapi = "Windows WASAPI" and names containing "Loopback"
    if sys.platform == "win32":
        hostapis = sd.query_hostapis()
        wasapi_idx = None
        for i, api in enumerate(hostapis):
            if "WASAPI" in api["name"]:
                wasapi_idx = i
                break
        if wasapi_idx is not None:
            for i, dev in enumerate(devices):
                if (
                    dev["hostapi"] == wasapi_idx
                    and dev["max_input_channels"] >= 2
                    and "loopback" in dev["name"].lower()
                ):
                    return i

    # Linux — look for PulseAudio/PipeWire monitor sources
    else:
        for i, dev in enumerate(devices):
            if dev["max_input_channels"] >= 2 and (
                "monitor" in dev["name"].lower()
                or "loopback" in dev["name"].lower()
            ):
                return i

    return None


class ScreenSource(IStreamSource):
    def __init__(self, monitor_index: int = 0, target_fps: float = 30.0) -> None:
        self._monitor_index = monitor_index
        self._target_fps = target_fps
        self._sct: mss.mss | None = None
        self._monitor: dict | None = None
        self._width = 0
        self._height = 0
        self._opened = False

        # Audio
        self._audio_stream: Optional[sd.InputStream] = None
        self._audio_device: Optional[int] = None
        self._has_audio = False

        # Ring buffer: holds up to 1 second of audio (48000 samples × 2 channels)
        self._ring_size = config.AUDIO_SAMPLE_RATE  # 1 second
        self._ring_buf = np.zeros(
            (self._ring_size, config.AUDIO_CHANNELS), dtype=np.float32
        )
        self._ring_write = 0
        self._ring_read = 0
        self._ring_lock = threading.Lock()

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
        return self._has_audio

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

        # Try to open system audio loopback
        self._audio_device = _find_loopback_device()
        if self._audio_device is not None:
            try:
                self._audio_stream = sd.InputStream(
                    device=self._audio_device,
                    samplerate=config.AUDIO_SAMPLE_RATE,
                    channels=config.AUDIO_CHANNELS,
                    dtype="float32",
                    blocksize=960,  # 20 ms chunks
                    callback=self._audio_callback,
                )
                self._audio_stream.start()
                self._has_audio = True
                dev_name = sd.query_devices(self._audio_device)["name"]
                print(f"[screen_source] audio loopback: {dev_name}")
            except Exception as e:
                print(f"[screen_source] audio loopback failed: {e}")
                self._has_audio = False
                self._audio_stream = None
        else:
            print("[screen_source] no loopback device found — audio disabled")

    def close(self) -> None:
        self._opened = False
        if self._audio_stream is not None:
            try:
                self._audio_stream.stop()
                self._audio_stream.close()
            except Exception:
                pass
            self._audio_stream = None
        if self._sct is not None:
            self._sct.close()
            self._sct = None
        self._monitor = None

    def seek(self, position_seconds: float) -> None:
        pass  # not meaningful for a live source

    def _audio_callback(self, indata, frames, time_info, status) -> None:
        """Called by sounddevice from its audio thread with new samples."""
        if status:
            pass  # underflow/overflow — ignore for loopback
        with self._ring_lock:
            n = indata.shape[0]
            end = self._ring_write + n
            if end <= self._ring_size:
                self._ring_buf[self._ring_write:end] = indata
            else:
                # Wrap around
                first = self._ring_size - self._ring_write
                self._ring_buf[self._ring_write:] = indata[:first]
                self._ring_buf[:n - first] = indata[first:]
            self._ring_write = end % self._ring_size

    def get_audio_chunk(self, num_samples: int) -> np.ndarray:
        """Pull num_samples from the ring buffer. Called by the NDI audio loop."""
        with self._ring_lock:
            available = (self._ring_write - self._ring_read) % self._ring_size
            if available < num_samples:
                # Not enough data — return silence for missing portion
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
        """Yield (BGRA frame, monotonic timestamp) at target fps."""
        if not self._opened or self._sct is None or self._monitor is None:
            raise RuntimeError("ScreenSource not opened")

        period = 1.0 / self._target_fps
        t0 = time.perf_counter()
        frame_index = 0
        consecutive_errors = 0

        while self._opened:
            try:
                raw = self._sct.grab(self._monitor)
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                if consecutive_errors > 10:
                    print(f"[screen_source] too many grab errors, stopping: {e}")
                    return
                print(f"[screen_source] grab error (attempt {consecutive_errors}): {e}")
                time.sleep(0.1)
                continue

            frame = np.frombuffer(raw.rgb, dtype=np.uint8).reshape(
                raw.height, raw.width, 3
            )
            bgra = cv2.cvtColor(frame, cv2.COLOR_RGB2BGRA)

            presentation_time = frame_index * period
            yield bgra, presentation_time
            frame_index += 1

            target = t0 + (frame_index * period)
            dt = target - time.perf_counter()
            if dt > 0:
                time.sleep(dt)

    def get_audio(self) -> np.ndarray:
        # For live sources, the NDI streamer uses get_audio_chunk() instead.
        # This exists only to satisfy the IStreamSource interface.
        return np.zeros((0, config.AUDIO_CHANNELS), dtype=np.float32)
