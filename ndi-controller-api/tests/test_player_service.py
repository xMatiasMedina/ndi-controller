"""
Tests for PlayerService — state machine, race conditions, edge cases.

These tests mock the NDIStreamer to avoid needing NDI runtime.
"""
from __future__ import annotations

import asyncio
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from core.models import PlaybackMode, PlaybackStatus, PlayerState, VideoAsset
from services.player_service import PlayerService
from tests.conftest import FakeSource


def _make_player(library, playlists, settings, obs, reaper):
    """Build a PlayerService with NDIStreamer mocked out."""
    player = PlayerService(
        library=library,
        playlists=playlists,
        settings=settings,
        obs=obs,
        reaper=reaper,
    )
    return player


def _add_test_video(library, persistence) -> VideoAsset:
    """Add a fake video to the library without hitting the filesystem."""
    from core.models import VideoAsset

    asset = VideoAsset(
        name="test.mp4",
        path="/fake/test.mp4",
        duration_seconds=10.0,
        width=1920,
        height=1080,
        fps=30.0,
    )
    library._videos.append(asset)
    return asset


# ----------------------------------------------------------------- State machine


class TestPlayerStateMachine:
    def test_initial_state_is_stopped(self, library, playlists, settings, obs, reaper):
        player = _make_player(library, playlists, settings, obs, reaper)
        state = player.get_state()
        assert state.status == PlaybackStatus.STOPPED

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_play_single_transitions_to_playing(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        video = _add_test_video(library, persistence)
        mock_source = FakeSource()
        MockFileSource.return_value = mock_source

        mock_streamer = MagicMock()
        MockStreamer.return_value = mock_streamer

        player = _make_player(library, playlists, settings, obs, reaper)
        state = player.play(mode=PlaybackMode.SINGLE, video_id=video.id)
        assert state.status == PlaybackStatus.PLAYING
        assert state.current_video_id == video.id
        assert state.mode == PlaybackMode.SINGLE
        mock_streamer.start.assert_called_once()

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_pause_and_resume(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        mock_streamer = MagicMock()
        MockStreamer.return_value = mock_streamer

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.SINGLE, video_id=video.id)

        state = player.pause()
        assert state.status == PlaybackStatus.PAUSED
        mock_streamer.pause.assert_called_with(True)

        state = player.resume()
        assert state.status == PlaybackStatus.PLAYING
        mock_streamer.pause.assert_called_with(False)

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_stop_transitions_to_stopped(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        mock_streamer = MagicMock()
        MockStreamer.return_value = mock_streamer

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.SINGLE, video_id=video.id)
        state = player.stop()
        assert state.status == PlaybackStatus.STOPPED
        assert state.position_seconds == 0.0
        mock_streamer.stop.assert_called()

    def test_pause_when_stopped_is_noop(self, library, playlists, settings, obs, reaper):
        player = _make_player(library, playlists, settings, obs, reaper)
        state = player.pause()
        assert state.status == PlaybackStatus.STOPPED

    def test_resume_when_stopped_is_noop(self, library, playlists, settings, obs, reaper):
        player = _make_player(library, playlists, settings, obs, reaper)
        state = player.resume()
        assert state.status == PlaybackStatus.STOPPED

    def test_seek_when_stopped_is_noop(self, library, playlists, settings, obs, reaper):
        player = _make_player(library, playlists, settings, obs, reaper)
        state = player.seek(5.0)
        assert state.status == PlaybackStatus.STOPPED
        assert state.position_seconds == 0.0

    def test_play_missing_video_raises(self, library, playlists, settings, obs, reaper):
        player = _make_player(library, playlists, settings, obs, reaper)
        with pytest.raises(ValueError, match="video_id"):
            player.play(mode=PlaybackMode.SINGLE)

    def test_play_nonexistent_video_raises(self, library, playlists, settings, obs, reaper):
        player = _make_player(library, playlists, settings, obs, reaper)
        with pytest.raises(ValueError, match="not found"):
            player.play(mode=PlaybackMode.SINGLE, video_id="nonexistent")

    def test_play_empty_playlist_raises(self, library, playlists, settings, obs, reaper):
        player = _make_player(library, playlists, settings, obs, reaper)
        pl = playlists.create_playlist("empty", [])
        with pytest.raises(ValueError, match="empty"):
            player.play(mode=PlaybackMode.PLAYLIST, playlist_id=pl.id)

    def test_play_playlist_missing_id_raises(self, library, playlists, settings, obs, reaper):
        player = _make_player(library, playlists, settings, obs, reaper)
        with pytest.raises(ValueError, match="playlist_id"):
            player.play(mode=PlaybackMode.PLAYLIST)


# ----------------------------------------------------------------- Playlist


class TestPlaylistAdvance:
    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_next_track_advances_cursor(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        v1 = _add_test_video(library, persistence)
        v2 = VideoAsset(
            name="test2.mp4", path="/fake/test2.mp4",
            duration_seconds=5.0, width=1920, height=1080, fps=30.0,
        )
        library._videos.append(v2)
        pl = playlists.create_playlist("test", [v1.id, v2.id])

        MockFileSource.return_value = FakeSource()
        MockStreamer.return_value = MagicMock()

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.PLAYLIST, playlist_id=pl.id)
        assert player.get_state().playlist_cursor == 0

        state = player.next_track()
        assert state.playlist_cursor == 1
        assert state.current_video_id == v2.id

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_next_past_end_without_loop_stops(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        v1 = _add_test_video(library, persistence)
        pl = playlists.create_playlist("single", [v1.id])

        MockFileSource.return_value = FakeSource()
        MockStreamer.return_value = MagicMock()

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.PLAYLIST, playlist_id=pl.id, loop=False)

        state = player.next_track()
        assert state.status == PlaybackStatus.STOPPED

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_next_past_end_with_loop_wraps(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        v1 = _add_test_video(library, persistence)
        pl = playlists.create_playlist("single", [v1.id])

        MockFileSource.return_value = FakeSource()
        MockStreamer.return_value = MagicMock()

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.PLAYLIST, playlist_id=pl.id, loop=True)

        state = player.next_track()
        assert state.status == PlaybackStatus.PLAYING
        assert state.playlist_cursor == 0

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_previous_track(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        v1 = _add_test_video(library, persistence)
        v2 = VideoAsset(
            name="test2.mp4", path="/fake/test2.mp4",
            duration_seconds=5.0, width=1920, height=1080, fps=30.0,
        )
        library._videos.append(v2)
        pl = playlists.create_playlist("test", [v1.id, v2.id])

        MockFileSource.return_value = FakeSource()
        MockStreamer.return_value = MagicMock()

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.PLAYLIST, playlist_id=pl.id)
        player.next_track()
        state = player.previous_track()
        assert state.playlist_cursor == 0
        assert state.current_video_id == v1.id


# ----------------------------------------------------------------- Offsets & Mute


class TestOffsetsAndMute:
    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_set_offsets(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        mock_streamer = MagicMock()
        MockStreamer.return_value = mock_streamer

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.SINGLE, video_id=video.id)

        state = player.set_offsets(video_offset_ms=100, audio_offset_ms=-50)
        assert state.video_offset_ms == 100
        assert state.audio_offset_ms == -50
        mock_streamer.set_video_offset_ms.assert_called_with(100)
        mock_streamer.set_audio_offset_ms.assert_called_with(-50)

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_set_muted(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        mock_streamer = MagicMock()
        MockStreamer.return_value = mock_streamer

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.SINGLE, video_id=video.id)

        state = player.set_muted(True)
        assert state.muted is True
        mock_streamer.set_muted.assert_called_with(True)


# ----------------------------------------------------------------- Race conditions


class TestRaceConditions:
    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_concurrent_stop_and_next(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        """Simulate user pressing stop while auto-advance fires."""
        v1 = _add_test_video(library, persistence)
        v2 = VideoAsset(
            name="test2.mp4", path="/fake/test2.mp4",
            duration_seconds=5.0, width=1920, height=1080, fps=30.0,
        )
        library._videos.append(v2)
        pl = playlists.create_playlist("test", [v1.id, v2.id])

        MockFileSource.return_value = FakeSource()
        MockStreamer.return_value = MagicMock()

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.PLAYLIST, playlist_id=pl.id)

        errors = []

        def do_stop():
            try:
                player.stop()
            except Exception as e:
                errors.append(e)

        def do_next():
            try:
                player.next_track()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=do_stop)
        t2 = threading.Thread(target=do_next)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Race condition caused errors: {errors}"
        # Final state should be consistent — either stopped or playing next
        state = player.get_state()
        assert state.status in (PlaybackStatus.STOPPED, PlaybackStatus.PLAYING)

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_rapid_play_stop_cycles(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        """Rapid play/stop shouldn't crash or leave inconsistent state."""
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        MockStreamer.return_value = MagicMock()

        player = _make_player(library, playlists, settings, obs, reaper)

        errors = []
        for _ in range(20):
            try:
                player.play(mode=PlaybackMode.SINGLE, video_id=video.id)
                player.stop()
            except Exception as e:
                errors.append(e)

        assert not errors, f"Rapid cycles caused errors: {errors}"
        assert player.get_state().status == PlaybackStatus.STOPPED

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_generation_prevents_stale_on_finished(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        """_on_finished from a previous generation should be ignored."""
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        mock_streamer = MagicMock()
        MockStreamer.return_value = mock_streamer

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.SINGLE, video_id=video.id)

        old_gen = player._generation
        player.stop()

        # Simulate a stale callback from the old streamer
        player._on_finished(old_gen)

        # Should still be stopped — the stale callback was ignored
        assert player.get_state().status == PlaybackStatus.STOPPED

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_concurrent_play_calls(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        """Two concurrent play calls shouldn't crash."""
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        MockStreamer.return_value = MagicMock()

        player = _make_player(library, playlists, settings, obs, reaper)
        errors = []

        def do_play():
            try:
                player.play(mode=PlaybackMode.SINGLE, video_id=video.id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=do_play) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Concurrent play caused errors: {errors}"
        state = player.get_state()
        assert state.status == PlaybackStatus.PLAYING


# ----------------------------------------------------------------- Reaper lockstep


class TestReaperLockstep:
    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_play_sends_reaper_commands(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        MockStreamer.return_value = MagicMock()

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.SINGLE, video_id=video.id)

        assert "seek:0.0" in reaper.calls
        assert "play" in reaper.calls

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_stop_sends_reaper_stop(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        MockStreamer.return_value = MagicMock()

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.SINGLE, video_id=video.id)
        reaper.calls.clear()

        player.stop()
        assert "stop" in reaper.calls

    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_seek_when_stopped_doesnt_send_reaper(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        player = _make_player(library, playlists, settings, obs, reaper)
        player.seek(5.0)
        assert "seek:5.0" not in reaper.calls


# ----------------------------------------------------------------- Shutdown


class TestShutdown:
    @patch("services.player_service.NDIStreamer")
    @patch("services.player_service.FileSource")
    def test_shutdown_stops_streamer(
        self, MockFileSource, MockStreamer, library, playlists, settings, obs, reaper, persistence
    ):
        video = _add_test_video(library, persistence)
        MockFileSource.return_value = FakeSource()
        mock_streamer = MagicMock()
        MockStreamer.return_value = mock_streamer

        player = _make_player(library, playlists, settings, obs, reaper)
        player.play(mode=PlaybackMode.SINGLE, video_id=video.id)
        player.shutdown()
        mock_streamer.stop.assert_called()
