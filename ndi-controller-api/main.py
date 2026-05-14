"""
FastAPI entry point. ALL dependency wiring happens here.

This is the only file that knows about every concrete class. Routes don't
import services directly — they get them via FastAPI's dependency override
mechanism. That means tests can inject mocks without monkey-patching modules.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

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

    async def shutdown(self) -> None:
        self.player.shutdown()
        await self.obs.disconnect()


container = Container()


# ----------------------------------------------------------------- app setup
@asynccontextmanager
async def lifespan(app: FastAPI):
    import asyncio
    from core.events import event_bus
    loop = asyncio.get_running_loop()
    event_bus.attach_loop(loop)
    container.player.attach_event_loop(loop)

    # Give the WS route a direct reference to the player for screen-share
    routes_ws.set_player_ref(container.player)

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


# Serve the built React frontend if present (production mode)
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# ----------------------------------------------------------------- dev runner
# Allows PyCharm to run this file directly with the Python interpreter.
# In production use: uvicorn main:app --host 0.0.0.0 --port 8000
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)