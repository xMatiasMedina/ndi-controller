"""
Pydantic models — the shared vocabulary of the app.

These travel between the persistence layer, the services, and the HTTP/WS API.
Defined in ONE place so a change to a field shows up everywhere instead of
silently drifting between layers (one of the pain points of the Java reference
project, which had to keep XML schemas and DTOs in sync by hand).
"""
from __future__ import annotations

from enum import Enum
from typing import List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# -----------------------------------------------------------------------------
# Library
# -----------------------------------------------------------------------------
class VideoAsset(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    path: str  # absolute path on the ndi-controller-api host
    duration_seconds: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None


# -----------------------------------------------------------------------------
# Playlists
# -----------------------------------------------------------------------------
class Playlist(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    video_ids: List[str] = Field(default_factory=list)


# -----------------------------------------------------------------------------
# Player state
# -----------------------------------------------------------------------------
class PlaybackMode(str, Enum):
    SINGLE = "single"
    PLAYLIST = "playlist"
    SCREEN = "screen"    # server-side monitor capture via mss
    BROWSER = "browser"  # browser getDisplayMedia → WebSocket → NDI


class PlaybackStatus(str, Enum):
    STOPPED = "stopped"
    PLAYING = "playing"
    PAUSED = "paused"


class PlayerState(BaseModel):
    status: PlaybackStatus = PlaybackStatus.STOPPED
    mode: PlaybackMode = PlaybackMode.SINGLE
    # Loop preference: 'off' | 'all' | 'one'. 'all'/'one' are persistent user
    # choices (shown blue) that carry across media; 'off' means no user loop.
    # The default playlist still loops as the resting state even when 'off'
    # (shown yellow) — see is_default below.
    loop_mode: str = "off"
    muted: bool = False

    # What's loaded
    current_video_id: Optional[str] = None
    current_playlist_id: Optional[str] = None
    playlist_cursor: int = 0       # index into the playlist
    monitor_index: int = 0         # for SCREEN mode

    # Live position (the streamer updates this)
    position_seconds: float = 0.0
    duration_seconds: float = 0.0

    # True when the active source is live (screen/browser).
    is_live: bool = False
    # True when the active content is the auto-resume default playlist. Drives
    # the UI's "yellow" loop state: it's looping only because it's the default,
    # so loop won't carry over when the user switches to other media.
    is_default: bool = False

    # Offsets in milliseconds — positive = delay this stream. For file sources
    # they shift the frame scheduler; for live (screen/browser) they delay the
    # stream in the WebRTC→NDI pipeline when the matching *_enabled flag is set.
    video_offset_ms: int = 0
    audio_offset_ms: int = 0
    # Whether each offset is applied (UI checkbox). When off the offset is
    # bypassed entirely — for live that means no delay buffer (minimal latency).
    # Default on, but with a 0 ms value that's a no-op until the slider moves.
    video_offset_enabled: bool = True
    audio_offset_enabled: bool = True

    # The configured auto-resume default playlist (mirrors settings). Surfaced
    # in player state so the UI updates live when it changes — including from
    # Home Assistant — instead of only on a page reload.
    default_playlist_id: Optional[str] = None


# -----------------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------------
class ObsSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 4455
    password: str = ""


class ReaperSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    lockstep: bool = True  # mirror video transport to Reaper


class NdiSettings(BaseModel):
    video_source_name: str = "AV_Platform_NDI"
    audio_source_name: str = "AV_Platform_NDI_Audio"


class ScreenGroup(BaseModel):
    """A named set of relay channels controlled together (a zone of screens)."""
    id: str
    name: str
    channels: List[int] = Field(default_factory=list)


class ModbusSettings(BaseModel):
    """Modbus TCP relay board that powers the physical screens.

    Channel N maps to coil N-1 on the board (Waveshare 8-ch). Groups let the
    simple remote expose friendly buttons (e.g. "Led Oficina" = channel 1).
    """
    host: str = "10.10.30.2"
    port: int = 502
    unit_id: int = 1
    num_channels: int = 8
    groups: List[ScreenGroup] = Field(
        default_factory=lambda: [
            ScreenGroup(id="office", name="Led Oficina", channels=[1]),
            ScreenGroup(id="warehouse", name="Led Almacen", channels=[7, 8]),
        ]
    )


class ScreenShareSettings(BaseModel):
    """WebRTC encode settings for the browser screen-share → NDI path.

    Read by the frontend when a share starts (applied to getDisplayMedia + the
    RTCRtpSender). Sharpness on the wall comes from RESOLUTION, not bitrate:
    capture is capped at max_width/max_height (match the OBS canvas), the encoder
    keeps full resolution and sheds frames if constrained, and bitrate is just
    generous headroom. The wall runs ~20 fps, so fps above that is wasted.
    """
    max_bitrate_mbps: int = 40  # bit budget cap (encoder uses ≤ this; not the sharpness lever)
    framerate: int = 24         # capture + encode fps (wall runs ~20)
    max_width: int = 1920       # capture resolution cap — the real sharpness lever
    max_height: int = 1080      # (a larger source is cleanly downscaled to this)


class Settings(BaseModel):
    obs: ObsSettings = Field(default_factory=ObsSettings)
    reaper: ReaperSettings = Field(default_factory=ReaperSettings)
    ndi: NdiSettings = Field(default_factory=NdiSettings)
    modbus: ModbusSettings = Field(default_factory=ModbusSettings)
    screen_share: ScreenShareSettings = Field(default_factory=ScreenShareSettings)

    # Playlist that auto-plays (looping) whenever nothing else is active.
    default_playlist_id: Optional[str] = None


# -----------------------------------------------------------------------------
# API request bodies (kept here so the routes stay thin)
# -----------------------------------------------------------------------------
class AddVideoRequest(BaseModel):
    path: str  # absolute path to the file


class CreatePlaylistRequest(BaseModel):
    name: str
    video_ids: List[str] = Field(default_factory=list)


class UpdatePlaylistRequest(BaseModel):
    name: Optional[str] = None
    video_ids: Optional[List[str]] = None


class PlayRequest(BaseModel):
    mode: PlaybackMode
    video_id: Optional[str] = None      # for SINGLE
    playlist_id: Optional[str] = None   # for PLAYLIST
    monitor_index: Optional[int] = None  # for SCREEN
    # Optional loop preference to apply with this play ('off'|'all'|'one'). When
    # omitted the player keeps its current (persistent) loop_mode.
    loop_mode: Optional[str] = None


class SeekRequest(BaseModel):
    position_seconds: float


class OffsetsRequest(BaseModel):
    video_offset_ms: Optional[int] = None
    audio_offset_ms: Optional[int] = None
    video_offset_enabled: Optional[bool] = None
    audio_offset_enabled: Optional[bool] = None


class MuteRequest(BaseModel):
    muted: bool


class LoopRequest(BaseModel):
    loop_mode: str  # 'off' | 'all' | 'one'