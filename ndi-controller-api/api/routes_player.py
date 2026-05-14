"""HTTP adapter for the player service."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.models import (
    MuteRequest,
    OffsetsRequest,
    PlayerState,
    PlayRequest,
    SeekRequest,
)
from services.player_service import PlayerService

router = APIRouter(prefix="/api/player", tags=["player"])


def get_player() -> PlayerService:
    raise NotImplementedError


@router.get("/state", response_model=PlayerState)
def get_state(player: PlayerService = Depends(get_player)) -> PlayerState:
    return player.get_state()


@router.post("/play", response_model=PlayerState)
def play(
    req: PlayRequest, player: PlayerService = Depends(get_player)
) -> PlayerState:
    try:
        return player.play(
            mode=req.mode,
            video_id=req.video_id,
            playlist_id=req.playlist_id,
            monitor_index=req.monitor_index,
            loop=req.loop,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except NotImplementedError as e:
        raise HTTPException(status_code=501, detail=str(e))


@router.post("/pause", response_model=PlayerState)
def pause(player: PlayerService = Depends(get_player)) -> PlayerState:
    return player.pause()


@router.post("/resume", response_model=PlayerState)
def resume(player: PlayerService = Depends(get_player)) -> PlayerState:
    return player.resume()


@router.post("/stop", response_model=PlayerState)
def stop(player: PlayerService = Depends(get_player)) -> PlayerState:
    return player.stop()


@router.post("/seek", response_model=PlayerState)
def seek(
    req: SeekRequest, player: PlayerService = Depends(get_player)
) -> PlayerState:
    return player.seek(req.position_seconds)


@router.post("/next", response_model=PlayerState)
def next_track(player: PlayerService = Depends(get_player)) -> PlayerState:
    return player.next_track()


@router.post("/previous", response_model=PlayerState)
def previous_track(player: PlayerService = Depends(get_player)) -> PlayerState:
    return player.previous_track()


@router.post("/offsets", response_model=PlayerState)
def set_offsets(
    req: OffsetsRequest, player: PlayerService = Depends(get_player)
) -> PlayerState:
    return player.set_offsets(req.video_offset_ms, req.audio_offset_ms)


@router.post("/mute", response_model=PlayerState)
def set_muted(
    req: MuteRequest, player: PlayerService = Depends(get_player)
) -> PlayerState:
    return player.set_muted(req.muted)
