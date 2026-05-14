"""
Tests for NDIStreamer — thread lifecycle, crash recovery, pause timing.

Mocks the cyndilib Sender to avoid needing NDI runtime.
"""
from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests.conftest import FakeSource, InfiniteSource


@pytest.fixture
def mock_ndi():
    """Patch cyndilib imports so NDIStreamer can be instantiated."""
    with patch("streaming.ndi_streamer.NDI_AVAILABLE", True), \
         patch("streaming.ndi_streamer.AUDIO_REFERENCE_AVAILABLE", False), \
         patch("streaming.ndi_streamer.Sender") as MockSender, \
         patch("streaming.ndi_streamer.VideoSendFrame") as MockVideoFrame, \
         patch("streaming.ndi_streamer.AudioSendFrame") as MockAudioFrame, \
         patch("streaming.ndi_streamer.FourCC") as MockFourCC:

        mock_sender = MagicMock()
        MockSender.return_value = mock_sender
        mock_vframe = MagicMock()
        MockVideoFrame.return_value = mock_vframe
        mock_aframe = MagicMock()
        mock_aframe.sample_rate = 48000
        mock_aframe.num_channels = 2
        MockAudioFrame.return_value = mock_aframe
        MockFourCC.BGRA = "BGRA"

        yield {
            "Sender": MockSender,
            "sender": mock_sender,
            "VideoFrame": MockVideoFrame,
            "AudioFrame": MockAudioFrame,
        }


class TestStreamerLifecycle:
    def test_start_and_stop(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        source = FakeSource(num_frames=5)
        streamer = NDIStreamer()
        streamer.start(source)

        assert streamer.is_running
        time.sleep(0.5)
        streamer.stop()
        assert not streamer.is_running
        assert source._closed

    def test_stop_before_start_is_safe(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        streamer = NDIStreamer()
        streamer.stop()  # should not raise

    def test_double_stop_is_safe(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        source = FakeSource(num_frames=3)
        streamer = NDIStreamer()
        streamer.start(source)
        time.sleep(0.2)
        streamer.stop()
        streamer.stop()  # should not raise


class TestOnFinished:
    def test_on_finished_called_when_source_ends(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        finished = threading.Event()
        streamer = NDIStreamer(on_finished=lambda: finished.set())
        source = FakeSource(num_frames=3)
        streamer.start(source)

        assert finished.wait(timeout=3), "on_finished was never called"
        streamer.stop()

    def test_on_finished_called_on_error(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        finished = threading.Event()
        streamer = NDIStreamer(on_finished=lambda: finished.set())
        source = FakeSource(num_frames=10, fail_on_frame=3)
        streamer.start(source)

        assert finished.wait(timeout=3), "on_finished should be called after error"
        streamer.stop()

    def test_on_finished_not_called_on_manual_stop(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        finished = threading.Event()
        streamer = NDIStreamer(on_finished=lambda: finished.set())
        source = InfiniteSource()
        streamer.start(source)

        time.sleep(0.1)
        streamer.stop()
        time.sleep(0.3)

        assert not finished.is_set(), "on_finished should NOT be called on manual stop"


class TestPositionCallback:
    def test_position_updates(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        positions = []
        streamer = NDIStreamer(on_position=lambda p: positions.append(p))
        source = FakeSource(num_frames=5)
        streamer.start(source)

        time.sleep(1)
        streamer.stop()

        assert len(positions) > 0
        assert positions[-1] > 0


class TestPause:
    def test_pause_and_resume(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        source = InfiniteSource()
        streamer = NDIStreamer()
        streamer.start(source)

        time.sleep(0.05)
        streamer.pause(True)
        assert streamer.is_paused

        streamer.pause(False)
        assert not streamer.is_paused

        streamer.stop()


class TestMute:
    def test_mute_toggle(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        streamer = NDIStreamer()
        streamer.set_muted(True)
        streamer.set_muted(False)  # should not raise


class TestOffsets:
    def test_set_offsets(self, mock_ndi):
        from streaming.ndi_streamer import NDIStreamer

        streamer = NDIStreamer()
        streamer.set_video_offset_ms(100)
        streamer.set_audio_offset_ms(-50)
        # No crash is the test — these set internal float values
