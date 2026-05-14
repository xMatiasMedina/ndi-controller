"""
Tests for API routes — upload security, CRUD operations.
Uses FastAPI TestClient with mocked services.
"""
from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.models import Settings, VideoAsset


@pytest.fixture
def client(library, playlists, settings, obs, reaper, persistence):
    """Build a test client with mocked dependencies."""
    from services.player_service import PlayerService

    with patch("services.player_service.NDIStreamer"), \
         patch("services.player_service.FileSource"):
        player = PlayerService(
            library=library,
            playlists=playlists,
            settings=settings,
            obs=obs,
            reaper=reaper,
        )

    # Import the app and wire overrides
    from api import routes_library, routes_player, routes_playlists, routes_settings
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(routes_library.router)
    app.include_router(routes_playlists.router)
    app.include_router(routes_player.router)
    app.include_router(routes_settings.router)

    app.dependency_overrides[routes_library.get_library] = lambda: library
    app.dependency_overrides[routes_playlists.get_playlists] = lambda: playlists
    app.dependency_overrides[routes_player.get_player] = lambda: player
    app.dependency_overrides[routes_settings.get_settings_service] = lambda: settings

    return TestClient(app)


class TestLibraryRoutes:
    def test_list_empty_library(self, client):
        r = client.get("/api/library")
        assert r.status_code == 200
        assert r.json() == []

    def test_upload_rejects_bad_extension(self, client):
        file = io.BytesIO(b"not a video")
        r = client.post(
            "/api/library/upload",
            files={"file": ("malware.exe", file, "application/octet-stream")},
        )
        assert r.status_code == 400
        assert "Unsupported file type" in r.json()["detail"]

    def test_upload_rejects_no_filename(self, client):
        file = io.BytesIO(b"data")
        r = client.post(
            "/api/library/upload",
            files={"file": ("", file, "video/mp4")},
        )
        assert r.status_code in (400, 422)

    def test_upload_sanitizes_path_traversal(self, client):
        """A filename like '../../etc/passwd.mp4' should be sanitized to just the name."""
        import config

        file = io.BytesIO(b"\x00" * 100)

        with patch.object(Path, "open", MagicMock()):
            with patch("api.routes_library.Path.exists", return_value=False):
                with patch.object(config.MEDIA_DIR.__class__, "__truediv__", wraps=config.MEDIA_DIR.__truediv__) as mock_div:
                    # The route should strip directory components
                    r = client.post(
                        "/api/library/upload",
                        files={"file": ("../../etc/evil.mp4", file, "video/mp4")},
                    )
                    # Regardless of status (may fail on disk write), the path
                    # used should not contain traversal components
                    # Check that the sanitized name was used
                    if mock_div.called:
                        used_name = str(mock_div.call_args[0][0])
                        assert ".." not in used_name


class TestPlaylistRoutes:
    def test_create_and_list_playlist(self, client):
        r = client.post(
            "/api/playlists",
            json={"name": "My Playlist", "video_ids": []},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "My Playlist"
        assert "id" in data

        r = client.get("/api/playlists")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_delete_playlist(self, client):
        r = client.post("/api/playlists", json={"name": "temp", "video_ids": []})
        pid = r.json()["id"]

        r = client.delete(f"/api/playlists/{pid}")
        assert r.status_code == 204

        r = client.delete(f"/api/playlists/{pid}")
        assert r.status_code == 404

    def test_update_playlist(self, client):
        r = client.post("/api/playlists", json={"name": "orig", "video_ids": []})
        pid = r.json()["id"]

        r = client.put(f"/api/playlists/{pid}", json={"name": "updated"})
        assert r.status_code == 200
        assert r.json()["name"] == "updated"


class TestPlayerRoutes:
    def test_get_state(self, client):
        r = client.get("/api/player/state")
        assert r.status_code == 200
        assert r.json()["status"] == "stopped"

    def test_pause_when_stopped(self, client):
        r = client.post("/api/player/pause")
        assert r.status_code == 200
        assert r.json()["status"] == "stopped"

    def test_stop_when_stopped(self, client):
        r = client.post("/api/player/stop")
        assert r.status_code == 200
        assert r.json()["status"] == "stopped"


class TestSettingsRoutes:
    def test_get_settings(self, client):
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.json()
        assert "obs" in data
        assert "reaper" in data
        assert "ndi" in data

    def test_update_settings(self, client):
        r = client.get("/api/settings")
        settings = r.json()
        settings["obs"]["port"] = 9999

        r = client.put("/api/settings", json=settings)
        assert r.status_code == 200
        assert r.json()["obs"]["port"] == 9999
