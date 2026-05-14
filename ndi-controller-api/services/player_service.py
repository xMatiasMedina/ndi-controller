"""
Player service — the orchestrator.

This is the only place that knows about ALL of:
  - the current source (file vs screen vs browser)
  - the NDI streamer
  - the playlist cursor and auto-advance logic
  - lockstep with Reaper (forwards transport actions via IReaperClient)

Everything else (routes, frontend) goes through this service. The routes are
thin HTTP adapters; the streamer doesn't know what a playlist is; the
reaper client doesn't know what's playing. Single Responsibility per layer.

Threading model:
  _state_lock guards all PlayerState reads and writes.
  _lifecycle_lock serialises streamer start/stop so that concurrent calls
  (user stop + auto-advance, rapid next-track clicks) don't interleave.
  Lock order: always acquire _lifecycle_lock BEFORE _state_lock to avoid
  deadlocks.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

from core.events import Events, event_bus
from core.interfaces import IObsClient, IReaperClient, IStreamSource
from core.models import (
    PlaybackMode,
    PlaybackStatus,
    PlayerState,
)
from services.library_service import LibraryService
from services.playlist_service import PlaylistService
from services.settings_service import SettingsService
from streaming.file_source import FileSource
from streaming.ndi_streamer import NDIStreamer


class PlayerService:
    def __init__(
        self,
        library: LibraryService,
        playlists: PlaylistService,
        settings: SettingsService,
        obs: IObsClient,
        reaper: IReaperClient,
    ) -> None:
        self._library = library
        self._playlists = playlists
        self._settings = settings
        self._obs = obs
        self._reaper = reaper

        self._state = PlayerState()
        self._state_lock = threading.Lock()
        self._lifecycle_lock = threading.RLock()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._streamer: Optional[NDIStreamer] = None
        self._browser_source = None
        self._generation = 0

    # ---------------------------------------------------------------- public
    def get_state(self) -> PlayerState:
        with self._state_lock:
            return self._state.model_copy(deep=True)

    def play(
        self,
        mode: PlaybackMode,
        video_id: Optional[str] = None,
        playlist_id: Optional[str] = None,
        monitor_index: Optional[int] = None,
        loop: bool = False,
    ) -> PlayerState:
        """Start playback. Stops any current playback first."""
        with self._lifecycle_lock:
            self._stop_streamer()

            with self._state_lock:
                self._state.mode = mode
                self._state.loop = loop

                if mode == PlaybackMode.SINGLE:
                    if not video_id:
                        raise ValueError("SINGLE mode requires video_id")
                    self._state.current_video_id = video_id
                    self._state.current_playlist_id = None
                    self._state.playlist_cursor = 0

                elif mode == PlaybackMode.PLAYLIST:
                    if not playlist_id:
                        raise ValueError("PLAYLIST mode requires playlist_id")
                    playlist = self._playlists.get_playlist(playlist_id)
                    if not playlist or not playlist.video_ids:
                        raise ValueError("Playlist is empty or not found")
                    self._state.current_playlist_id = playlist_id
                    self._state.playlist_cursor = 0
                    self._state.current_video_id = playlist.video_ids[0]

                elif mode == PlaybackMode.SCREEN:
                    self._state.monitor_index = monitor_index or 0
                    self._state.current_video_id = None
                    self._state.current_playlist_id = None

                elif mode == PlaybackMode.BROWSER:
                    self._state.current_video_id = None
                    self._state.current_playlist_id = None

                snap_mode = self._state.mode
                snap_video_id = self._state.current_video_id
                snap_monitor = self._state.monitor_index
                snap_v_off = self._state.video_offset_ms
                snap_a_off = self._state.audio_offset_ms
                snap_muted = self._state.muted

            self._start_source(
                snap_mode, snap_video_id, snap_monitor,
                snap_v_off, snap_a_off, snap_muted,
            )
        return self.get_state()

    def pause(self) -> PlayerState:
        with self._state_lock:
            if self._state.status != PlaybackStatus.PLAYING:
                return self._state.model_copy(deep=True)
            self._state.status = PlaybackStatus.PAUSED
            streamer = self._streamer
        if streamer:
            streamer.pause(True)
        if self._settings.get().reaper.lockstep:
            self._reaper.pause()
        self._publish_state_change()
        return self.get_state()

    def resume(self) -> PlayerState:
        with self._state_lock:
            if self._state.status != PlaybackStatus.PAUSED:
                return self._state.model_copy(deep=True)
            self._state.status = PlaybackStatus.PLAYING
            streamer = self._streamer
        if streamer:
            streamer.pause(False)
        if self._settings.get().reaper.lockstep:
            self._reaper.play()
        self._publish_state_change()
        return self.get_state()

    def stop(self) -> PlayerState:
        with self._lifecycle_lock:
            self._stop_streamer()
            with self._state_lock:
                self._state.status = PlaybackStatus.STOPPED
                self._state.position_seconds = 0.0
            if self._settings.get().reaper.lockstep:
                self._reaper.stop()
        self._publish_state_change()
        return self.get_state()

    def seek(self, position_seconds: float) -> PlayerState:
        with self._state_lock:
            if self._state.status == PlaybackStatus.STOPPED:
                return self._state.model_copy(deep=True)
            self._state.position_seconds = max(0.0, position_seconds)
            pos = self._state.position_seconds
            streamer = self._streamer
        if streamer:
            streamer.seek(pos)
        if self._settings.get().reaper.lockstep:
            self._reaper.seek(pos)
        self._publish_state_change()
        return self.get_state()

    def next_track(self) -> PlayerState:
        return self._advance_playlist(+1)

    def previous_track(self) -> PlayerState:
        return self._advance_playlist(-1)

    def set_offsets(
        self,
        video_offset_ms: Optional[int] = None,
        audio_offset_ms: Optional[int] = None,
    ) -> PlayerState:
        with self._state_lock:
            if video_offset_ms is not None:
                self._state.video_offset_ms = video_offset_ms
            if audio_offset_ms is not None:
                self._state.audio_offset_ms = audio_offset_ms
            streamer = self._streamer
        if streamer:
            if video_offset_ms is not None:
                streamer.set_video_offset_ms(video_offset_ms)
            if audio_offset_ms is not None:
                streamer.set_audio_offset_ms(audio_offset_ms)
        self._publish_state_change()
        return self.get_state()

    def set_muted(self, muted: bool) -> PlayerState:
        with self._state_lock:
            self._state.muted = muted
            streamer = self._streamer
        if streamer:
            streamer.set_muted(muted)
        self._publish_state_change()
        return self.get_state()

    def push_browser_frame(self, jpeg_bytes: bytes) -> None:
        bs = self._browser_source
        if bs is not None:
            bs.push_frame(jpeg_bytes)

    @property
    def is_browser_active(self) -> bool:
        return (
            self._browser_source is not None
            and self._state.mode == PlaybackMode.BROWSER
        )

    def shutdown(self) -> None:
        with self._lifecycle_lock:
            self._stop_streamer()

    # --------------------------------------------------------------- private
    def _stop_streamer(self) -> None:
        """Stop the current streamer and source. Caller must hold _lifecycle_lock."""
        self._generation += 1
        if self._streamer:
            self._streamer.stop()
            self._streamer = None
        self._browser_source = None

    def _start_source(
        self,
        mode: PlaybackMode,
        video_id: Optional[str],
        monitor_index: int,
        video_offset_ms: int,
        audio_offset_ms: int,
        muted: bool,
    ) -> None:
        """Build the IStreamSource and hand it to a new streamer.
        Caller must hold _lifecycle_lock."""
        source: IStreamSource
        gen = self._generation

        if mode == PlaybackMode.SCREEN:
            from streaming.screen_source import ScreenSource
            source = ScreenSource(monitor_index=monitor_index)

        elif mode == PlaybackMode.BROWSER:
            from streaming.browser_source import BrowserSource
            browser_src = BrowserSource()
            self._browser_source = browser_src
            source = browser_src

        else:
            video = self._library.get_video(video_id)
            if not video:
                raise ValueError(f"Video not found: {video_id}")
            source = FileSource(video.path)

        ndi_settings = self._settings.get().ndi
        streamer = NDIStreamer(
            video_source_name=ndi_settings.video_source_name,
            audio_source_name=ndi_settings.audio_source_name,
            on_position=self._on_position,
            on_finished=lambda: self._on_finished(gen),
        )
        streamer.set_video_offset_ms(video_offset_ms)
        streamer.set_audio_offset_ms(audio_offset_ms)
        streamer.set_muted(muted)
        streamer.start(source)
        self._streamer = streamer

        with self._state_lock:
            self._state.status = PlaybackStatus.PLAYING
            self._state.position_seconds = 0.0
            self._state.duration_seconds = source.duration_seconds

        if self._settings.get().reaper.lockstep:
            self._reaper.seek(0.0)
            self._reaper.play()

        self._publish_state_change()

    def _advance_playlist(self, delta: int) -> PlayerState:
        """Move +/-1 in the playlist; respect loop; stop when off the end."""
        with self._lifecycle_lock:
            should_stop = False
            with self._state_lock:
                if self._state.status == PlaybackStatus.STOPPED:
                    return self._state.model_copy(deep=True)
                if self._state.mode != PlaybackMode.PLAYLIST:
                    return self._state.model_copy(deep=True)

                playlist = self._playlists.get_playlist(
                    self._state.current_playlist_id
                )
                if not playlist or not playlist.video_ids:
                    return self._state.model_copy(deep=True)

                new_cursor = self._state.playlist_cursor + delta
                n = len(playlist.video_ids)

                if 0 <= new_cursor < n:
                    self._state.playlist_cursor = new_cursor
                elif self._state.loop:
                    self._state.playlist_cursor = new_cursor % n
                else:
                    self._state.status = PlaybackStatus.STOPPED
                    self._state.position_seconds = 0.0
                    should_stop = True

                if not should_stop:
                    self._state.current_video_id = playlist.video_ids[
                        self._state.playlist_cursor
                    ]
                    snap_video_id = self._state.current_video_id
                    snap_v_off = self._state.video_offset_ms
                    snap_a_off = self._state.audio_offset_ms
                    snap_muted = self._state.muted

            if should_stop:
                self._stop_streamer()
                self._publish_state_change()
                return self.get_state()

            self._stop_streamer()
            self._start_source(
                PlaybackMode.PLAYLIST, snap_video_id, 0,
                snap_v_off, snap_a_off, snap_muted,
            )
        return self.get_state()

    # ----- callbacks from the streamer threads -----
    def _on_position(self, position: float) -> None:
        with self._state_lock:
            self._state.position_seconds = position
        event_bus.publish_sync(
            Events.POSITION_CHANGED,
            {"position_seconds": position},
        )

    def _on_finished(self, generation: int) -> None:
        """Streamer ran out of frames. Either advance, loop, or stop.
        The generation check prevents stale callbacks from acting on a
        player that has already moved on (user pressed stop/next)."""
        if generation != self._generation:
            return

        with self._state_lock:
            mode = self._state.mode
            loop = self._state.loop
            status = self._state.status

        if status == PlaybackStatus.STOPPED:
            return

        if mode == PlaybackMode.PLAYLIST:
            self._schedule_async(self._advance_playlist_async(+1))
        elif mode == PlaybackMode.SINGLE and loop:
            self._schedule_async(self._restart_single_async())
        else:
            self._schedule_async(self._stop_async())

    def attach_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def _schedule_async(self, coro) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _advance_playlist_async(self, delta: int) -> None:
        self._advance_playlist(delta)

    async def _restart_single_async(self) -> None:
        with self._lifecycle_lock:
            with self._state_lock:
                if self._state.status == PlaybackStatus.STOPPED:
                    return
                snap_video_id = self._state.current_video_id
                snap_v_off = self._state.video_offset_ms
                snap_a_off = self._state.audio_offset_ms
                snap_muted = self._state.muted
                snap_mode = self._state.mode

            self._stop_streamer()
            self._start_source(
                snap_mode, snap_video_id, 0,
                snap_v_off, snap_a_off, snap_muted,
            )

    async def _stop_async(self) -> None:
        self.stop()

    def _publish_state_change(self) -> None:
        event_bus.publish_sync(
            Events.PLAYER_STATE_CHANGED, self.get_state().model_dump()
        )
