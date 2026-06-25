"""
FastAPI entry point. ALL dependency wiring happens here.

This is the only file that knows about every concrete class. Routes don't
import services directly — they get them via FastAPI's dependency override
mechanism. That means tests can inject mocks without monkey-patching modules.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from api import (
    routes_library,
    routes_player,
    routes_playlists,
    routes_settings,
    routes_ws,
)
from services.library_service import LibraryService
from services.modbus_service import ModbusService
from services.obs_service import ObsService
from services.persistence_service import JsonPersistenceService
from services.player_service import PlayerService
from services.playlist_service import PlaylistService
from services.reaper_service import ReaperService
from services.settings_service import SettingsService


# ----------------------------------------------------------------- container
class Container:
    """Manual DI container. Created once at startup."""

    def __init__(self) -> None:
        self.persistence = JsonPersistenceService()
        self.settings = SettingsService(self.persistence)
        self.library = LibraryService(self.persistence)
        self.playlists = PlaylistService(self.persistence, self.library)
        self.obs = ObsService(self.settings)
        self.reaper = ReaperService(self.settings)
        self.modbus = ModbusService(self.settings)
        self.player = PlayerService(
            library=self.library,
            playlists=self.playlists,
            settings=self.settings,
            obs=self.obs,
            reaper=self.reaper,
        )

    async def startup(self) -> None:
        """Async init — connect to external services."""
        # Try connecting to OBS (non-fatal if it's not running)
        connected = await self.obs.connect()
        if not connected:
            print("[startup] OBS not available — will retry when needed")

        # Begin the default playlist (resting state) if one is configured.
        # Run in a thread — opening a file source can decode audio (blocking).
        import asyncio
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.player.start_default)

    async def shutdown(self) -> None:
        self.player.shutdown()
        await self.obs.disconnect()
        await self.modbus.disconnect()


container = Container()


# ----------------------------------------------------------------- app setup
@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from core.events import event_bus
    loop = asyncio.get_running_loop()
    event_bus.attach_loop(loop)
    container.player.attach_event_loop(loop)

    # Give the WS route direct references to player and OBS for screen-share
    routes_ws.set_player_ref(container.player)
    routes_ws.set_obs_ref(container.obs)

    await container.startup()
    yield
    await container.shutdown()


app = FastAPI(title="NDI Controller", lifespan=lifespan)

# In dev the React dev server runs on :5173; allow it to call the API.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Wire dependency overrides — routes ask via Depends(get_xxx),
# we point those at the container's shared instances.
app.dependency_overrides[routes_library.get_library] = lambda: container.library
app.dependency_overrides[routes_playlists.get_playlists] = lambda: container.playlists
app.dependency_overrides[routes_player.get_player] = lambda: container.player
app.dependency_overrides[routes_settings.get_settings_service] = lambda: container.settings

app.include_router(routes_library.router)
app.include_router(routes_playlists.router)
app.include_router(routes_player.router)
app.include_router(routes_settings.router)
app.include_router(routes_ws.router)


# ---- OBS routes (connect/disconnect/scenes) ----
from fastapi import HTTPException


@app.post("/api/obs/connect")
async def obs_connect():
    ok = await container.obs.connect()
    if not ok:
        raise HTTPException(status_code=503, detail="Could not connect to OBS")
    return {"connected": True}


@app.post("/api/obs/disconnect")
async def obs_disconnect():
    await container.obs.disconnect()
    return {"connected": False}


@app.get("/api/obs/status")
async def obs_status():
    return {"connected": container.obs.is_connected()}


@app.get("/api/obs/scenes")
async def obs_scenes():
    scenes = await container.obs.list_scenes()
    current = await container.obs.get_current_scene()
    return {"scenes": scenes, "current": current}


@app.post("/api/obs/scene/{scene_name}")
async def obs_set_scene(scene_name: str):
    ok = await container.obs.set_current_scene(scene_name)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to switch scene")
    return {"scene": scene_name}


@app.get("/api/obs/scene/{scene_name}/sources")
async def obs_scene_sources(scene_name: str):
    items = await container.obs.list_scene_items(scene_name)
    return {"sources": items}


from pydantic import BaseModel


class SetSourceEnabledBody(BaseModel):
    enabled: bool


@app.post("/api/obs/scene/{scene_name}/source/{item_id}")
async def obs_set_source_enabled(scene_name: str, item_id: int, body: SetSourceEnabledBody):
    ok = await container.obs.set_scene_item_enabled(scene_name, item_id, body.enabled)
    if not ok:
        raise HTTPException(status_code=400, detail="Failed to toggle source")
    return {"sceneItemId": item_id, "enabled": body.enabled}


# ---- Screen power routes (Modbus relay board) ----
@app.get("/api/screens")
async def screens_state():
    return await container.modbus.read_states()


@app.post("/api/screens/all/{action}")
async def screens_all(action: str):
    if action not in ("on", "off"):
        raise HTTPException(status_code=400, detail="action must be 'on' or 'off'")
    ok = await container.modbus.set_all(action == "on")
    return {"ok": ok}


@app.post("/api/screens/group/{group_id}/{action}")
async def screens_group(group_id: str, action: str):
    if action not in ("on", "off"):
        raise HTTPException(status_code=400, detail="action must be 'on' or 'off'")
    groups = container.settings.get().modbus.groups
    group = next((g for g in groups if g.id == group_id), None)
    if group is None:
        raise HTTPException(status_code=404, detail=f"Unknown screen group: {group_id}")
    ok = await container.modbus.set_channels(group.channels, action == "on")
    return {"ok": ok}


# ---- Default playlist (resting-state loop) ----
class DefaultPlaylistBody(BaseModel):
    playlist_id: Optional[str] = None


@app.post("/api/default-playlist")
async def set_default_playlist(body: DefaultPlaylistBody):
    s = container.settings.get()
    s.default_playlist_id = body.playlist_id
    container.settings.update(s)
    # Apply right away: start it if idle, or swap if the default is showing.
    import asyncio
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, container.player.refresh_default)
    return {"default_playlist_id": body.playlist_id}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ---- Frontend (built React app) ----
from fastapi.responses import FileResponse

frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"


@app.get("/remote")
async def remote_page():
    """Serve the SPA shell for the simple remote page (client-side route)."""
    index = frontend_dist / "index.html"
    if index.exists():
        return FileResponse(str(index))
    raise HTTPException(status_code=404, detail="frontend not built")


# Serve the built React frontend if present (production mode)
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


# ----------------------------------------------------------------- dev runner
# Allows PyCharm to run this file directly with the Python interpreter.
# In production use: uvicorn main:app --host 0.0.0.0 --port 8000
if __name__ == "__main__":
    import uvicorn

    cert_dir = Path(__file__).parent
    cert_file = cert_dir / "cert.pem"
    key_file = cert_dir / "key.pem"

    if cert_file.exists() and key_file.exists():
        uvicorn.run(
            "main:app",
            host="0.0.0.0",
            port=8000,
            reload=True,
            ssl_certfile=str(cert_file),
            ssl_keyfile=str(key_file),
        )
    else:
        uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)