"""
FileSource — reads frames from a video file, returns decoded audio buffer.

Implements ``IStreamSource`` so the NDI streamer can use it interchangeably
with the screen capture source (Phase 2). The streamer doesn't know the
difference.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterator, Tuple

import cv2
import ffmpeg
import numpy as np

import config
from core.interfaces import IStreamSource


class FileSource(IStreamSource):
    def __init__(self, path: str) -> None:
        self._path = str(Path(path).resolve())
        self._cap: cv2.VideoCapture | None = None
        self._width = 0
        self._height = 0
        self._fps = 0.0
        self._duration = 0.0
        self._audio_cache: np.ndarray | None = None
        self._has_audio = True
        self._seek_to: float | None = None  # honoured at next iter step

    # ----- Metadata -----
    @property
    def width(self) -> int: return self._width

    @property
    def height(self) -> int: return self._height

    @property
    def fps(self) -> float: return self._fps

    @property
    def duration_seconds(self) -> float: return self._duration

    @property
    def has_audio(self) -> bool: return self._has_audio

    # ----- Lifecycle -----
    def open(self) -> None:
        cap = cv2.VideoCapture(self._path)
        if not cap.isOpened():
            raise RuntimeError(f"Cannot open video: {self._path}")
        self._cap = cap
        self._width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self._height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        self._fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        self._duration = (frames / self._fps) if self._fps else 0.0

        # Decode the entire audio track up front. For very long files this could
        # be replaced with a streaming decode (ffmpeg pipe) — flagged as a future
        # optimization.
        self._audio_cache = self._decode_audio()
        self._has_audio = self._audio_cache.size > 0

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._audio_cache = None

    def seek(self, position_seconds: float) -> None:
        # Defer the actual cv2 seek to the iterator thread to avoid threading
        # issues with cv2.VideoCapture.
        self._seek_to = max(0.0, min(position_seconds, self._duration))

    # ----- Iteration -----
    def iter_video(self) -> Iterator[Tuple[np.ndarray, float]]:
        if self._cap is None:
            raise RuntimeError("FileSource not opened")

        period = 1.0 / self._fps if self._fps else 1 / 30.0
        frame_index = 0

        while True:
            if self._seek_to is not None:
                target = self._seek_to
                self._seek_to = None
                self._cap.set(cv2.CAP_PROP_POS_MSEC, target * 1000.0)
                frame_index = int(target / period) if period else 0

            ok, frame = self._cap.read()
            if not ok:
                return  # end of file

            # Convert BGR -> BGRA (NDI BGRA fourcc expects 4 channels)
            bgra = cv2.cvtColor(frame, cv2.COLOR_BGR2BGRA)
            yield bgra, frame_index * period
            frame_index += 1

    def get_audio(self) -> np.ndarray:
        return self._audio_cache if self._audio_cache is not None else np.zeros(
            (0, config.AUDIO_CHANNELS), dtype=np.float32
        )

    # ----- Helpers -----
    def _decode_audio(self) -> np.ndarray:
        try:
            out, _ = (
                ffmpeg.input(self._path)
                .output(
                    "pipe:",
                    format="f32le",
                    acodec="pcm_f32le",
                    ac=config.AUDIO_CHANNELS,
                    ar=config.AUDIO_SAMPLE_RATE,
                )
                .run(capture_stdout=True, capture_stderr=True, quiet=True)
            )
            samples = np.frombuffer(out, dtype=np.float32)
            return samples.reshape(-1, config.AUDIO_CHANNELS)
        except ffmpeg.Error as e:
            print(
                f"[file_source] no audio decoded from {self._path}: "
                f"{e.stderr.decode(errors='ignore') if e.stderr else e}"
            )
            return np.zeros((0, config.AUDIO_CHANNELS), dtype=np.float32)
