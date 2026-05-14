"""Playlist service — saved ordered groups of videos."""
from __future__ import annotations

from typing import List, Optional

import config
from core.interfaces import IPersistence
from core.models import Playlist
from services.library_service import LibraryService


class PlaylistService:
    def __init__(
        self, persistence: IPersistence, library: LibraryService
    ) -> None:
        self._persistence = persistence
        self._library = library
        self._playlists: List[Playlist] = []
        self._load()

    # ----- Persistence -----
    def _load(self) -> None:
        data = self._persistence.load_json(config.PLAYLISTS_FILE)
        if data and "playlists" in data:
            self._playlists = [Playlist(**p) for p in data["playlists"]]

    def _save(self) -> None:
        self._persistence.save_json(
            config.PLAYLISTS_FILE,
            {"playlists": [p.model_dump() for p in self._playlists]},
        )

    # ----- Public API -----
    def list_playlists(self) -> List[Playlist]:
        return list(self._playlists)

    def get_playlist(self, playlist_id: str) -> Optional[Playlist]:
        return next((p for p in self._playlists if p.id == playlist_id), None)

    def create_playlist(self, name: str, video_ids: List[str]) -> Playlist:
        validated = [vid for vid in video_ids if self._library.get_video(vid)]
        playlist = Playlist(name=name, video_ids=validated)
        self._playlists.append(playlist)
        self._save()
        return playlist

    def update_playlist(
        self,
        playlist_id: str,
        name: Optional[str] = None,
        video_ids: Optional[List[str]] = None,
    ) -> Optional[Playlist]:
        playlist = self.get_playlist(playlist_id)
        if not playlist:
            return None
        if name is not None:
            playlist.name = name
        if video_ids is not None:
            playlist.video_ids = [
                vid for vid in video_ids if self._library.get_video(vid)
            ]
        self._save()
        return playlist

    def remove_playlist(self, playlist_id: str) -> bool:
        before = len(self._playlists)
        self._playlists = [p for p in self._playlists if p.id != playlist_id]
        if len(self._playlists) != before:
            self._save()
            return True
        return False
