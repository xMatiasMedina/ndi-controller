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
    loop: bool = False
    muted: bool = False

    # What's loaded
    current_video_id: Optional[str] = None
    current_playlist_id: Optional[str] = None
    playlist_cursor: int = 0       # index into the playlist
    monitor_index: int = 0         # for SCREEN mode

    # Live position (the streamer updates this)
    position_seconds: float = 0.0
    duration_seconds: float = 0.0

    # Offsets in milliseconds — positive = delay this stream
    video_offset_ms: int = 0
    audio_offset_ms: int = 0


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
    video_source_name: str = "OBS Video"
    audio_source_name: str = "Reaper Audio"


class Settings(BaseModel):
    obs: ObsSettings = Field(default_factory=ObsSettings)
    reaper: ReaperSettings = Field(default_factory=ReaperSettings)
    ndi: NdiSettings = Field(default_factory=NdiSettings)


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
    loop: bool = False


class SeekRequest(BaseModel):
    position_seconds: float


class OffsetsRequest(BaseModel):
    video_offset_ms: Optional[int] = None
    audio_offset_ms: Optional[int] = None


class MuteRequest(BaseModel):
    muted: bool