"""
Dual NDI streamer.

Two NDI senders, each following cyndilib's canonical pattern:

  * "OBS Video" — a video-only sender carrying the real video at the
    source's native fps. Written from the video thread using write_video().

  * "Reaper Audio" — a video+audio sender carrying a 16x16 black video AND
    the real audio (or silence), both written atomically every 20 ms using
    write_video_and_audio(). Reaper's NDI Input VST refuses to lock onto
    sources where video and audio have mismatched cadences; by pairing a
    tiny black frame to every audio chunk we get a clean 50 fps video +
    48 kHz audio stream that the VST is happy with.

Reference level is set to dBFS_smpte (SMPTE -20 dBFS) on the audio frame,
matching Reaper's default "Audio Level: -20dB (SMPTE level)".

Threading model:
  - one thread writes to the video sender at source fps
  - one thread writes video+audio atomically to the audio sender at 50 Hz
  - each Sender is touched by exactly one thread — no races on cyndilib
    internal state

Clocking per NDI SDK guidance:
  - "OBS Video" sender uses the default clock_video=True (video-only, single
    thread writing at declared fps — NDI paces it).
  - "Reaper Audio" sender uses clock_video=True, clock_audio=False. NDI docs:
    "if you are submitting video and audio of a single thread, you should
    only clock one of them (video is probably the better choice to clock
    off)."

Data format notes (cyndilib 0.0.8+):
  - write_video expects a flat 1D uint8 buffer of size W*H*4 for BGRA
  - write_video_and_audio(video_data, audio_data) takes the flat video
    buffer + (num_channels, num_samples) float32 audio
  - Audio sample count must match AudioSendFrame.max_num_samples exactly
  - AudioReference.dBFS_smpte makes cyndilib scale normalized audio up
    by 20 dB (10x) to put it at NDI's wire level
"""
from __future__ import annotations

import threading
import time
from fractions import Fraction
from typing import Callable, Optional

import numpy as np
import traceback

try:
    from cyndilib.sender import Sender
    from cyndilib.video_frame import VideoSendFrame
    from cyndilib.audio_frame import AudioSendFrame
    from cyndilib.wrapper.ndi_structs import FourCC
    NDI_AVAILABLE = True
except ImportError:
    NDI_AVAILABLE = False

try:
    from cyndilib import AudioReference
    AUDIO_REFERENCE_AVAILABLE = True
except ImportError:
    AUDIO_REFERENCE_AVAILABLE = False

import config
from core.interfaces import IStreamSource

# 20 ms audio chunk @ 48 kHz = 960 samples.
AUDIO_CHUNK_SAMPLES = config.AUDIO_SAMPLE_RATE // 50

# The audio sender emits a tiny 16x16 black video frame paired to every
# audio chunk. At 50 Hz audio (20 ms chunks) that means 50 fps video —
# a consistent cadence the NDI Input VST can lock onto.
AUDIO_SENDER_VIDEO_FPS = 50
AUDIO_SENDER_VIDEO_W = 16
AUDIO_SENDER_VIDEO_H = 16


def _fps_to_fraction(fps: float) -> Fraction:
    """Convert an fps float to the Fraction type cyndilib requires."""
    if fps <= 0:
        return Fraction(30, 1)
    return Fraction(fps).limit_denominator(1000)


class NDIStreamer:
    """Owns the two NDI senders. Plays a given IStreamSource through them."""

    def __init__(
        self,
        video_source_name: str = config.DEFAULT_NDI_VIDEO_NAME,
        audio_source_name: str = config.DEFAULT_NDI_AUDIO_NAME,
        on_position: Optional[Callable[[float], None]] = None,
        on_finished: Optional[Callable[[], None]] = None,
    ) -> None:
        if not NDI_AVAILABLE:
            raise RuntimeError(
                "cyndilib not installed. Install NDI runtime + cyndilib first."
            )

        self._video_name = video_source_name
        self._audio_name = audio_source_name
        self._on_position = on_position
        self._on_finished = on_finished

        self._stop_event = threading.Event()
        self._paused = False
        self._pause_started: float = 0.0
        self._total_paused: float = 0.0
        self._muted = False
        self._video_offset = 0.0
        self._audio_offset = 0.0

        self._source: Optional[IStreamSource] = None
        self._threads: list[threading.Thread] = []
        self._t0: float = 0.0
        self._is_live: bool = False

        # NDI senders — created on start, destroyed on stop
        self._v_sender: Optional[Sender] = None
        self._a_sender: Optional[Sender] = None
        self._v_frame: Optional[VideoSendFrame] = None
        self._a_frame: Optional[AudioSendFrame] = None
        self._a_keep_frame: Optional[VideoSendFrame] = None

        self._v_flat_size = 0

    # ----- Public control -----
    def start(self, source: IStreamSource) -> None:
        if not self._stop_event.is_set() and self._threads:
            self.stop()

        self._source = source
        self._source.open()

        self._is_live = source.duration_seconds == 0.0

        self._init_ndi()
        self._stop_event.clear()
        self._paused = False
        self._total_paused = 0.0
        self._t0 = time.perf_counter()

        tv = threading.Thread(target=self._video_loop, daemon=True, name="ndi-video")
        ta = threading.Thread(target=self._audio_loop, daemon=True, name="ndi-audio")
        tv.start(); ta.start()
        self._threads = [tv, ta]

    def stop(self) -> None:
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=3)
            if t.is_alive():
                print(f"[ndi] WARNING: thread {t.name} did not exit in time")
        self._threads = []

        if self._source is not None:
            try:
                self._source.close()
            except Exception as e:
                print(f"[ndi] source close error: {e}")
            self._source = None

        self._shutdown_ndi()

    def pause(self, paused: bool) -> None:
        if paused and not self._paused:
            self._pause_started = time.perf_counter()
        elif not paused and self._paused:
            self._total_paused += time.perf_counter() - self._pause_started
        self._paused = paused

    def set_muted(self, muted: bool) -> None:
        self._muted = muted

    def set_video_offset_ms(self, ms: int) -> None:
        self._video_offset = ms / 1000.0

    def set_audio_offset_ms(self, ms: int) -> None:
        self._audio_offset = ms / 1000.0

    def seek(self, position_seconds: float) -> None:
        if self._source is not None:
            self._source.seek(position_seconds)
            self._t0 = time.perf_counter() - position_seconds - self._total_paused

    @property
    def is_running(self) -> bool: return not self._stop_event.is_set()

    @property
    def is_paused(self) -> bool: return self._paused

    # ----- NDI lifecycle -----
    def _init_ndi(self) -> None:
        assert self._source is not None
        s = self._source

        # ---- "OBS Video" — video-only sender ------------------------------
        # Default clocking (clock_video=True, clock_audio=True). Only video
        # is attached, so clock_audio is a no-op.
        self._v_sender = Sender(ndi_name=self._video_name)
        self._v_frame = VideoSendFrame()
        self._v_frame.set_fourcc(FourCC.BGRA)
        self._v_frame.set_resolution(s.width, s.height)
        self._v_frame.set_frame_rate(_fps_to_fraction(s.fps))
        self._v_sender.set_video_frame(self._v_frame)
        self._v_flat_size = s.width * s.height * 4

        # ---- "Reaper Audio" — video+audio sender, written atomically ------
        # Single thread writes via write_video_and_audio() at 50 Hz. Per NDI
        # SDK guidance for single-thread combined writes: clock only the
        # video. clock_audio=False lets our explicit pacing control audio.
        self._a_sender = Sender(
            ndi_name=self._audio_name,
            clock_video=True,
            clock_audio=False,
        )

        # Audio frame
        self._a_frame = AudioSendFrame()
        self._a_frame.sample_rate = config.AUDIO_SAMPLE_RATE
        self._a_frame.num_channels = config.AUDIO_CHANNELS
        if AUDIO_REFERENCE_AVAILABLE:
            try:
                self._a_frame.reference_level = AudioReference.dBFS_smpte
                print("[ndi] audio reference level: dBFS_smpte (SMPTE)")
            except AttributeError:
                print(
                    "[ndi] WARNING: reference_level unsupported. "
                    "Upgrade cyndilib (pip install -U cyndilib)."
                )
        else:
            print(
                "[ndi] WARNING: AudioReference import failed. "
                "Upgrade cyndilib (pip install -U cyndilib)."
            )
        self._a_frame.set_max_num_samples(AUDIO_CHUNK_SAMPLES)
        self._a_sender.set_audio_frame(self._a_frame)

        # Tiny black video frame paired with every audio chunk -> 50 fps
        self._a_keep_frame = VideoSendFrame()
        self._a_keep_frame.set_fourcc(FourCC.BGRA)
        self._a_keep_frame.set_resolution(
            AUDIO_SENDER_VIDEO_W, AUDIO_SENDER_VIDEO_H
        )
        self._a_keep_frame.set_frame_rate(
            Fraction(AUDIO_SENDER_VIDEO_FPS, 1)
        )
        self._a_sender.set_video_frame(self._a_keep_frame)

        self._v_sender.open()
        self._a_sender.open()

    def _shutdown_ndi(self) -> None:
        for sender in (self._v_sender, self._a_sender):
            if sender is not None:
                try:
                    sender.close()
                except Exception:
                    pass
        self._v_sender = None
        self._a_sender = None

    # ----- Helpers -----
    @staticmethod
    def _flatten_bgra(frame: np.ndarray) -> np.ndarray:
        """cyndilib wants a contiguous 1D uint8 buffer for video."""
        if frame.dtype != np.uint8:
            frame = frame.astype(np.uint8)
        if not frame.flags["C_CONTIGUOUS"]:
            frame = np.ascontiguousarray(frame)
        return frame.reshape(-1)

    # ----- Worker loops -----
    def _video_loop(self) -> None:
        """Writes only to the 'OBS Video' sender."""
        assert self._source is not None
        last_position = 0.0
        finished_naturally = False

        try:
            for frame_bgra, presentation_time in self._source.iter_video():
                if self._stop_event.is_set():
                    return

                while self._paused and not self._stop_event.is_set():
                    time.sleep(0.05)

                if not self._is_live:
                    target = (
                        self._t0
                        + self._total_paused
                        + self._video_offset
                        + presentation_time
                    )
                    dt = target - time.perf_counter()
                    if dt > 0:
                        time.sleep(dt)

                self._v_sender.write_video(self._flatten_bgra(frame_bgra))

                last_position = presentation_time
                if self._on_position is not None:
                    self._on_position(last_position)

            finished_naturally = True
        except Exception as e:
            if not self._stop_event.is_set():
                print(f"[ndi] video loop error: {e}")
                traceback.print_exc()
        finally:
            if finished_naturally and self._on_finished is not None:
                self._on_finished()
            elif not finished_naturally and not self._stop_event.is_set():
                if self._on_finished is not None:
                    self._on_finished()

    def _audio_loop(self) -> None:
        """
        Writes to the 'Reaper Audio' sender. Every 20 ms calls
        write_video_and_audio() with a black 16x16 frame and the next audio
        chunk — one atomic operation.
        """
        assert self._source is not None
        chunk = AUDIO_CHUNK_SAMPLES

        black_keep = np.zeros(
            (AUDIO_SENDER_VIDEO_H, AUDIO_SENDER_VIDEO_W, 4), dtype=np.uint8
        ).reshape(-1)

        silence = np.ascontiguousarray(
            np.zeros((config.AUDIO_CHANNELS, chunk), dtype=np.float32)
        )

        try:
            if self._source.has_audio:
                samples = self._source.get_audio()
            else:
                samples = np.zeros((0, config.AUDIO_CHANNELS), dtype=np.float32)
        except Exception as e:
            print(f"[ndi] failed to get audio: {e}")
            samples = np.zeros((0, config.AUDIO_CHANNELS), dtype=np.float32)

        total = samples.shape[0]
        pos = 0
        i = 0
        pad_buffer = np.zeros(
            (chunk, config.AUDIO_CHANNELS), dtype=np.float32
        )

        try:
            while not self._stop_event.is_set():
                while self._paused and not self._stop_event.is_set():
                    time.sleep(0.05)
                if self._stop_event.is_set():
                    return

                if pos < total:
                    end = min(pos + chunk, total)
                    block = samples[pos:end]
                    if block.shape[0] < chunk:
                        pad_buffer[:] = 0
                        pad_buffer[: block.shape[0]] = block
                        block = pad_buffer
                    if self._muted:
                        block = np.zeros_like(block)
                    planar = np.ascontiguousarray(block.T.astype(np.float32))
                    pos = end
                else:
                    planar = silence

                target = (
                    self._t0
                    + self._total_paused
                    + self._audio_offset
                    + i * (chunk / config.AUDIO_SAMPLE_RATE)
                )
                dt = target - time.perf_counter()
                if dt > 0:
                    time.sleep(dt)

                self._a_sender.write_video_and_audio(
                    video_data=black_keep,
                    audio_data=planar,
                )

                i += 1
        except Exception as e:
            if not self._stop_event.is_set():
                print(f"[ndi] audio loop error: {e}")
                traceback.print_exc()