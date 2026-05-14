"""Library service — manages the catalogue of video files."""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import ffmpeg

import config
from core.interfaces import IPersistence
from core.models import VideoAsset


class LibraryService:
    def __init__(self, persistence: IPersistence) -> None:
        self._persistence = persistence
        self._videos: List[VideoAsset] = []
        self._load()

    # ----- Persistence -----
    def _load(self) -> None:
        data = self._persistence.load_json(config.LIBRARY_FILE)
        if data and "videos" in data:
            self._videos = [VideoAsset(**v) for v in data["videos"]]

    def _save(self) -> None:
        self._persistence.save_json(
            config.LIBRARY_FILE,
            {"videos": [v.model_dump() for v in self._videos]},
        )

    # ----- Public API -----
    def list_videos(self) -> List[VideoAsset]:
        return list(self._videos)

    def get_video(self, video_id: str) -> Optional[VideoAsset]:
        return next((v for v in self._videos if v.id == video_id), None)

    def add_video(self, path: str) -> VideoAsset:
        p = Path(path)
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"No such file: {path}")

        meta = self._probe(p)
        asset = VideoAsset(name=p.name, path=str(p.resolve()), **meta)
        self._videos.append(asset)
        self._save()
        return asset

    def remove_video(self, video_id: str) -> bool:
        before = len(self._videos)
        self._videos = [v for v in self._videos if v.id != video_id]
        if len(self._videos) != before:
            self._save()
            return True
        return False

    # ----- Helpers -----
    @staticmethod
    def _probe(path: Path) -> dict:
        """Best-effort ffprobe — failures don't stop adding the video."""
        try:
            info = ffmpeg.probe(str(path))
            v = next(
                (s for s in info["streams"] if s["codec_type"] == "video"), None
            )
            if not v:
                return {}
            num, den = (int(x) for x in v.get("r_frame_rate", "0/1").split("/"))
            fps = num / den if den else 0.0
            return {
                "duration_seconds": float(info["format"].get("duration", 0)),
                "width": int(v.get("width", 0)),
                "height": int(v.get("height", 0)),
                "fps": fps,
            }
        except (ffmpeg.Error, KeyError, ValueError, StopIteration) as e:
            print(f"[library] probe failed for {path}: {e}")
            return {}
