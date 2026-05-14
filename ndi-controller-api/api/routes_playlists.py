"""HTTP adapter for the playlist service."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, status

from core.models import (
    CreatePlaylistRequest,
    Playlist,
    UpdatePlaylistRequest,
)
from services.playlist_service import PlaylistService

router = APIRouter(prefix="/api/playlists", tags=["playlists"])


def get_playlists() -> PlaylistService:
    raise NotImplementedError


@router.get("", response_model=List[Playlist])
def list_playlists(svc: PlaylistService = Depends(get_playlists)) -> List[Playlist]:
    return svc.list_playlists()


@router.post("", response_model=Playlist, status_code=status.HTTP_201_CREATED)
def create_playlist(
    req: CreatePlaylistRequest, svc: PlaylistService = Depends(get_playlists)
) -> Playlist:
    return svc.create_playlist(req.name, req.video_ids)


@router.put("/{playlist_id}", response_model=Playlist)
def update_playlist(
    playlist_id: str,
    req: UpdatePlaylistRequest,
    svc: PlaylistService = Depends(get_playlists),
) -> Playlist:
    updated = svc.update_playlist(playlist_id, req.name, req.video_ids)
    if not updated:
        raise HTTPException(status_code=404, detail="Playlist not found")
    return updated


@router.delete("/{playlist_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_playlist(
    playlist_id: str, svc: PlaylistService = Depends(get_playlists)
) -> Response:
    if not svc.remove_playlist(playlist_id):
        raise HTTPException(status_code=404, detail="Playlist not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)