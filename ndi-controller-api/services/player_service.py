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
        self._lock = threading.Lock()  # guards state mutations
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._streamer: Optional[NDIStreamer] = None
        self._browser_source = None  # holds BrowserSource when in BROWSER mode

    # ---------------------------------------------------------------- public
    def get_state(self) -> PlayerState:
        with self._lock:
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
        self.stop()

        with self._lock:
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

        self._start_current_source()
        return self.get_state()

    def pause(self) -> PlayerState:
        with self._lock:
            if self._state.status == PlaybackStatus.PLAYING:
                self._state.status = PlaybackStatus.PAUSED
                if self._streamer:
                    self._streamer.pause(True)
                if self._settings.get().reaper.lockstep:
                    self._reaper.pause()
        self._publish_state_change()
        return self.get_state()

    def resume(self) -> PlayerState:
        with self._lock:
            if self._state.status == PlaybackStatus.PAUSED:
                self._state.status = PlaybackStatus.PLAYING
                if self._streamer:
                    self._streamer.pause(False)
                if self._settings.get().reaper.lockstep:
                    self._reaper.play()
        self._publish_state_change()
        return self.get_state()

    def stop(self) -> PlayerState:
        with self._lock:
            self._state.status = PlaybackStatus.STOPPED
            self._state.position_seconds = 0.0
        if self._streamer:
            self._streamer.stop()
            self._streamer = None
        self._browser_source = None  # release browser source
        if self._settings.get().reaper.lockstep:
            self._reaper.stop()
        self._publish_state_change()
        return self.get_state()

    def seek(self, position_seconds: float) -> PlayerState:
        with self._lock:
            self._state.position_seconds = max(0.0, position_seconds)
        if self._streamer:
            self._streamer.seek(self._state.position_seconds)
        if self._settings.get().reaper.lockstep:
            self._reaper.seek(self._state.position_seconds)
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
        with self._lock:
            if video_offset_ms is not None:
                self._state.video_offset_ms = video_offset_ms
                if self._streamer:
                    self._streamer.set_video_offset_ms(video_offset_ms)
            if audio_offset_ms is not None:
                self._state.audio_offset_ms = audio_offset_ms
                if self._streamer:
                    self._streamer.set_audio_offset_ms(audio_offset_ms)
        self._publish_state_change()
        return self.get_state()

    def set_muted(self, muted: bool) -> PlayerState:
        with self._lock:
            self._state.muted = muted
            if self._streamer:
                self._streamer.set_muted(muted)
        self._publish_state_change()
        return self.get_state()

    def push_browser_frame(self, jpeg_bytes: bytes) -> None:
        """Push a JPEG frame from the browser WebSocket into the active BrowserSource."""
        if self._browser_source is not None:
            self._browser_source.push_frame(jpeg_bytes)

    @property
    def is_browser_active(self) -> bool:
        """True when in BROWSER mode and source is ready for frames."""
        return self._browser_source is not None and self._state.mode == PlaybackMode.BROWSER

    def shutdown(self) -> None:
        """Called on app shutdown."""
        if self._streamer:
            self._streamer.stop()
            self._streamer = None
        self._browser_source = None

    # --------------------------------------------------------------- private
    def _start_current_source(self) -> None:
        """Build the IStreamSource for current state and hand it to the streamer."""
        source: IStreamSource

        if self._state.mode == PlaybackMode.SCREEN:
            from streaming.screen_source import ScreenSource
            source = ScreenSource(monitor_index=self._state.monitor_index)

        elif self._state.mode == PlaybackMode.BROWSER:
            from streaming.browser_source import BrowserSource
            browser_src = BrowserSource()
            self._browser_source = browser_src
            source = browser_src

        else:
            video = self._library.get_video(self._state.current_video_id)
            if not video:
                raise ValueError(f"Video not found: {self._state.current_video_id}")
            source = FileSource(video.path)

        ndi_settings = self._settings.get().ndi
        self._streamer = NDIStreamer(
            video_source_name=ndi_settings.video_source_name,
            audio_source_name=ndi_settings.audio_source_name,
            on_position=self._on_position,
            on_finished=self._on_finished,
        )
        self._streamer.set_video_offset_ms(self._state.video_offset_ms)
        self._streamer.set_audio_offset_ms(self._state.audio_offset_ms)
        self._streamer.set_muted(self._state.muted)
        self._streamer.start(source)

        with self._lock:
            self._state.status = PlaybackStatus.PLAYING
            self._state.position_seconds = 0.0
            self._state.duration_seconds = source.duration_seconds

        if self._settings.get().reaper.lockstep:
            self._reaper.seek(0.0)
            self._reaper.play()

        self._publish_state_change()

    def _advance_playlist(self, delta: int) -> PlayerState:
        """Move ±1 in the playlist; respect loop; stop when off the end."""
        with self._lock:
            if self._state.mode != PlaybackMode.PLAYLIST:
                return self._state.model_copy(deep=True)

            playlist = self._playlists.get_playlist(self._state.current_playlist_id)
            if not playlist or not playlist.video_ids:
                return self._state.model_copy(deep=True)

            new_cursor = self._state.playlist_cursor + delta
            n = len(playlist.video_ids)

            if 0 <= new_cursor < n:
                self._state.playlist_cursor = new_cursor
            elif self._state.loop:
                self._state.playlist_cursor = new_cursor % n
            else:
                # off the end and not looping — stop
                self._stop_internal_locked()
                self._publish_state_change()
                return self._state.model_copy(deep=True)

            self._state.current_video_id = playlist.video_ids[
                self._state.playlist_cursor
            ]

        # Restart with the new track outside the lock
        if self._streamer:
            self._streamer.stop()
            self._streamer = None
        self._start_current_source()
        return self.get_state()

    def _stop_internal_locked(self) -> None:
        """Internal stop — caller must hold the lock."""
        self._state.status = PlaybackStatus.STOPPED
        self._state.position_seconds = 0.0

    # ----- callbacks from the streamer threads -----
    def _on_position(self, position: float) -> None:
        with self._lock:
            self._state.position_seconds = position
        # Position updates are noisy — publish a lighter event
        event_bus.publish_sync(
            Events.POSITION_CHANGED,
            {"position_seconds": position},
        )

    def _on_finished(self) -> None:
        """Streamer ran out of frames. Either advance, loop, or stop."""
        mode = self.get_state().mode
        if mode == PlaybackMode.PLAYLIST:
            # Schedule advance on the asyncio loop so it can call services
            self._schedule_async(self._advance_playlist_async(+1))
        elif mode == PlaybackMode.SINGLE and self.get_state().loop:
            self._schedule_async(self._restart_single_async())
        else:
            self._schedule_async(self._stop_async())

    def attach_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Called from main.py at startup so worker threads can schedule coroutines."""
        self._loop = loop

    def _schedule_async(self, coro) -> None:
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    async def _advance_playlist_async(self, delta: int) -> None:
        self._advance_playlist(delta)

    async def _restart_single_async(self) -> None:
        self._start_current_source()

    async def _stop_async(self) -> None:
        self.stop()

    def _publish_state_change(self) -> None:
        event_bus.publish_sync(
            Events.PLAYER_STATE_CHANGED, self.get_state().model_dump()
        )