"""HTTP adapter for the library service. No business logic here."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Response, UploadFile, File, status

import config
from core.models import AddVideoRequest, VideoAsset
from services.library_service import LibraryService

router = APIRouter(prefix="/api/library", tags=["library"])

# Allowed video extensions
ALLOWED_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".mts",
}


def get_library() -> LibraryService:
    """Overridden by main.py to inject the shared instance."""
    raise NotImplementedError


@router.get("", response_model=List[VideoAsset])
def list_videos(library: LibraryService = Depends(get_library)) -> List[VideoAsset]:
    return library.list_videos()


@router.post("", response_model=VideoAsset, status_code=status.HTTP_201_CREATED)
def add_video(
    req: AddVideoRequest, library: LibraryService = Depends(get_library)
) -> VideoAsset:
    try:
        return library.add_video(req.path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/upload", response_model=VideoAsset, status_code=status.HTTP_201_CREATED)
async def upload_video(
    file: UploadFile = File(...),
    library: LibraryService = Depends(get_library),
) -> VideoAsset:
    """
    Accept a video file upload, save it to the media directory,
    and add it to the library.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    ext = Path(file.filename).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    # Sanitize filename: strip directory components to prevent path traversal
    safe_name = Path(file.filename).name
    if not safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Save to media directory — avoid overwriting by appending a counter
    dest = config.MEDIA_DIR / safe_name
    counter = 1
    while dest.exists():
        stem = Path(safe_name).stem
        dest = config.MEDIA_DIR / f"{stem}_{counter}{ext}"
        counter += 1

    try:
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save file: {e}")
    finally:
        await file.close()

    try:
        return library.add_video(str(dest))
    except Exception as e:
        # Clean up the saved file if library add fails
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_video(
    video_id: str, library: LibraryService = Depends(get_library)
) -> Response:
    if not library.remove_video(video_id):
        raise HTTPException(status_code=404, detail="Video not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)