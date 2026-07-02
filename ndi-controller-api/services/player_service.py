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
        # True while the auto-started default playlist is the active content.
        self._is_default_active = False
        # True while an external source (WebRTC screen-share via GStreamer)
        # owns the NDI senders — blocks default-playlist resume.
        self._screenshare_active = False
        # Applies offsets to the live (screen-share) WebRTC→NDI pipeline.
        # Signature: (video_ms, audio_ms, video_enabled, audio_enabled).
        self._live_offset_handler = None

    def set_live_offset_handler(self, handler) -> None:
        """Wire the live (screen-share) offset applier — see ScreenShareService."""
        self._live_offset_handler = handler

    # ---------------------------------------------------------------- public
    def get_state(self) -> PlayerState:
        default_id = self._settings.get().default_playlist_id
        with self._state_lock:
            st = self._state.model_copy(deep=True)
        # default_playlist_id lives in settings; surface it on every state read
        # so each WS broadcast carries the current value — keeping the UI in
        # sync even when it's changed externally (e.g. via Home Assistant).
        st.default_playlist_id = default_id
        # Whether the default playlist is the ACTIVE content (drives the UI's
        # "yellow" loop state). Computed from the consistent snapshot — not
        # re-read from live state — and only while actually playing (stop()
        # leaves mode/current_playlist_id set, so the status check matters).
        st.is_default = (
            default_id is not None
            and st.status != PlaybackStatus.STOPPED
            and st.mode == PlaybackMode.PLAYLIST
            and st.current_playlist_id == default_id
        )
        return st

    def _is_default_content(self) -> bool:
        """True when the ACTIVE content is the configured default playlist —
        however it started (auto-resume OR the user manually selecting it). So
        manually playing the default playlist loops and shows the yellow state
        just like the resting state does. Reads live state without locking —
        call under _state_lock or where a slightly stale read is harmless."""
        default_id = self._settings.get().default_playlist_id
        return (
            default_id is not None
            and self._state.status != PlaybackStatus.STOPPED
            and self._state.mode == PlaybackMode.PLAYLIST
            and self._state.current_playlist_id == default_id
        )

    def _effective_loop_mode(self) -> str:
        """The loop behaviour for the current item: the user's persistent choice
        if any, otherwise the default playlist still loops ('all', shown yellow)
        as the resting state while user-selected media plays once. Reads state
        without locking — call while holding _state_lock or where a slightly
        stale read is harmless."""
        if self._state.loop_mode != "off":
            return self._state.loop_mode
        return "all" if self._is_default_content() else "off"

    def publish_state(self) -> None:
        """Force a state broadcast to WS clients. Use after something that
        affects state but isn't routed through a player method — e.g. the
        default playlist being changed in settings (UI or Home Assistant)."""
        self._publish_state_change()

    def play(
        self,
        mode: PlaybackMode,
        video_id: Optional[str] = None,
        playlist_id: Optional[str] = None,
        monitor_index: Optional[int] = None,
        loop_mode: Optional[str] = None,
    ) -> PlayerState:
        """Start playback. Stops any current playback first."""
        with self._lifecycle_lock:
            # A manual play takes over from the default resting state.
            self._is_default_active = False
            self._screenshare_active = False
            self._stop_streamer()

            with self._state_lock:
                self._state.mode = mode
                # 'all'/'one' persist across plays; omit (None) to keep the
                # current preference. The default playlist's resting-state
                # looping is handled by _effective_loop_mode, not stored here.
                if loop_mode is not None:
                    self._state.loop_mode = loop_mode

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
            was_default = self._is_default_active
            self._stop_streamer()
            self._is_default_active = False
            with self._state_lock:
                self._state.status = PlaybackStatus.STOPPED
                self._state.position_seconds = 0.0
                self._state.is_live = False
            if self._settings.get().reaper.lockstep:
                self._reaper.stop()
            self._publish_state_change()
            # Return to the default playlist (the resting state) unless what we
            # just stopped WAS the default — stopping the default is a real
            # "off" (escape hatch), otherwise it could never be silenced.
            if not was_default:
                self._maybe_start_default_locked()
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
        video_offset_enabled: Optional[bool] = None,
        audio_offset_enabled: Optional[bool] = None,
    ) -> PlayerState:
        with self._state_lock:
            if video_offset_ms is not None:
                self._state.video_offset_ms = video_offset_ms
            if audio_offset_ms is not None:
                self._state.audio_offset_ms = audio_offset_ms
            if video_offset_enabled is not None:
                self._state.video_offset_enabled = video_offset_enabled
            if audio_offset_enabled is not None:
                self._state.audio_offset_enabled = audio_offset_enabled
            is_live = self._state.is_live
            v = self._state.video_offset_ms
            a = self._state.audio_offset_ms
            v_en = self._state.video_offset_enabled
            a_en = self._state.audio_offset_enabled
            streamer = self._streamer

        if is_live:
            # Live (screen/browser): apply via the WebRTC→NDI receiver, which
            # delays each branch. The file streamer isn't running here.
            if self._live_offset_handler is not None:
                try:
                    self._live_offset_handler(v, a, v_en, a_en)
                except Exception as e:
                    print(f"[player] live offset handler failed: {e}")
        elif streamer:
            # File source: shift the frame scheduler. Bypassed (0) when the
            # stream's checkbox is unticked, so the UI behaves the same as live.
            streamer.set_video_offset_ms(v if v_en else 0)
            streamer.set_audio_offset_ms(a if a_en else 0)

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

    def set_loop_mode(self, loop_mode: str) -> PlayerState:
        """Set the loop preference live: 'off' | 'all' | 'one'. 'all'/'one' are
        persistent user choices that carry across media; 'off' clears the user
        loop (the default playlist still loops as the resting state, shown
        yellow). Read live by _on_finished so it affects the current item too."""
        mode = loop_mode if loop_mode in ("off", "all", "one") else "off"
        with self._state_lock:
            self._state.loop_mode = mode
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

    # ----------------------------------------------------------- default loop
    def start_default(self) -> None:
        """Start the default playlist if one is set and nothing is playing.
        Called once at startup so the screens are never blank by default."""
        with self._lifecycle_lock:
            with self._state_lock:
                status = self._state.status
            if status == PlaybackStatus.STOPPED:
                self._maybe_start_default_locked()

    def refresh_default(self) -> None:
        """Re-evaluate the default after the selection changed in settings.
        Start it if idle, or restart it if the default is what's currently
        showing so the new selection takes effect immediately. Never interrupts
        manual playback or a live stream."""
        with self._lifecycle_lock:
            with self._state_lock:
                status = self._state.status
            if status == PlaybackStatus.STOPPED or self._is_default_active:
                self._maybe_start_default_locked()

    def suspend_for_screenshare(self) -> None:
        """Hand the NDI senders to an external WebRTC screen-share: stop the
        Python streamer and block default-playlist resume until it ends."""
        with self._lifecycle_lock:
            self._screenshare_active = True
            self._is_default_active = False
            self._stop_streamer()
            with self._state_lock:
                self._state.mode = PlaybackMode.BROWSER
                self._state.status = PlaybackStatus.PLAYING
                self._state.is_live = True
                self._state.current_video_id = None
                self._state.current_playlist_id = None
                self._state.position_seconds = 0.0
            if self._settings.get().reaper.lockstep:
                self._reaper.stop()
            self._publish_state_change()

    def resume_after_screenshare(self) -> None:
        """Screen-share ended — return to the default resting state."""
        with self._lifecycle_lock:
            self._screenshare_active = False
            with self._state_lock:
                self._state.status = PlaybackStatus.STOPPED
                self._state.is_live = False
            self._publish_state_change()
            self._maybe_start_default_locked()

    def _maybe_start_default_locked(self) -> None:
        """Start the configured default playlist, looping, as the resting state.
        No-op if no default is set or it is empty/missing.
        Caller must hold _lifecycle_lock."""
        if self._screenshare_active:
            return  # an external screen-share owns the NDI senders
        default_id = self._settings.get().default_playlist_id
        if not default_id:
            return
        playlist = self._playlists.get_playlist(default_id)
        if not playlist or not playlist.video_ids:
            return

        self._stop_streamer()
        with self._state_lock:
            self._state.mode = PlaybackMode.PLAYLIST
            # Entering the resting default clears any persistent user loop, so
            # it shows the YELLOW "looping because default" state and rotates
            # through ALL its items (loop_mode 'off' + is_default → effective
            # 'all'). A leftover 'one' would otherwise pin the default to its
            # first item forever; a leftover 'all' would just mis-show as blue.
            self._state.loop_mode = "off"
            self._state.current_playlist_id = default_id
            self._state.playlist_cursor = 0
            self._state.current_video_id = playlist.video_ids[0]
            snap_video_id = self._state.current_video_id
            snap_v_off = self._state.video_offset_ms
            snap_a_off = self._state.audio_offset_ms
            snap_muted = self._state.muted

        self._is_default_active = True
        self._start_source(
            PlaybackMode.PLAYLIST, snap_video_id, 0,
            snap_v_off, snap_a_off, snap_muted,
        )

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
            self._state.is_live = source.duration_seconds == 0.0

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
                elif self._effective_loop_mode() != "off":
                    # Wrap when looping at all (user 'all'/'one', or the default
                    # playlist). 'one' only reaches here via a manual next/prev.
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
                was_default = self._is_default_active
                self._stop_streamer()
                self._is_default_active = False
                self._publish_state_change()
                if not was_default:
                    self._maybe_start_default_locked()
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
            status = self._state.status

        if status == PlaybackStatus.STOPPED:
            return

        eff = self._effective_loop_mode()  # 'off' | 'all' | 'one'
        if eff == "one":
            # Loop the current item only — replay it without advancing, even
            # inside a playlist.
            self._schedule_async(self._restart_single_async())
        elif mode == PlaybackMode.PLAYLIST:
            # Advance; _advance_playlist wraps when looping ('all'/default) or
            # stops at the end when 'off'.
            self._schedule_async(self._advance_playlist_async(+1))
        elif eff == "all":
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
