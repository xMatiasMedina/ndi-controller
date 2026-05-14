# NDI Controller

A web dashboard for streaming a video file (or live screen capture) as **two
separate NDI sources** — video to OBS, audio to Reaper — with independent
real-time offset control, OBS scene switching, Reaper transport lockstep, and
playlist management.

## Stack

- **Backend:** Python 3.11, FastAPI, cyndilib (NDI), opencv-python + mss
  (capture), ffmpeg-python (audio decode), obsws-python (OBS WebSocket v5),
  python-osc (Reaper), Pydantic, JSON persistence.
- **Frontend:** React 18 + Vite, plain CSS, native fetch + WebSocket.

## Phase 1 status (this delivery)

✅ Library + playlists + JSON persistence
✅ Player state machine (single / playlist, auto-advance, optional loop)
✅ Dual NDI streamer (video to OBS, audio to Reaper) with offsets and mute
✅ File source for video files
✅ FastAPI routes: library, playlists, player, offsets, settings
✅ React shell: transport bar, library, playlist, offset sliders

🟡 Stubs (Phase 2): OBS scene control, Reaper OSC, screen-capture source,
WebSocket live state push.

## Quickstart

### Backend
```bash
cd ndi-controller-api
python3.11 -m venv .venv
source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend
```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

The Vite dev server proxies `/api` and `/ws` to FastAPI on `:8000`.

## Project layout

```
ndi-controller-api/
├── main.py                  # FastAPI app, dependency wiring
├── config.py                # Paths and defaults
├── core/
│   ├── interfaces.py        # ABCs every concrete class implements
│   ├── models.py            # Pydantic models
│   └── events.py            # In-process async event bus
├── streaming/
│   ├── ndi_streamer.py      # Two NDI senders (video + audio)
│   ├── file_source.py       # IStreamSource for video files
│   ├── screen_source.py     # IStreamSource for monitor capture
│   └── browser_source.py    # IStreamSource for browser screen share
├── services/
│   ├── library_service.py
│   ├── playlist_service.py
│   ├── player_service.py    # State machine + orchestration
│   ├── persistence_service.py
│   ├── obs_service.py       # OBS WebSocket v5 client
│   └── reaper_service.py    # Reaper OSC client
└── api/
    ├── routes_library.py
    ├── routes_playlists.py
    ├── routes_player.py
    ├── routes_settings.py
    └── routes_ws.py         # WebSocket state broadcast + screen share

frontend/
├── index.html
├── package.json
├── vite.config.js
└── src/
    ├── main.jsx
    ├── App.jsx
    ├── api.js               # fetch() wrappers
    ├── hooks/
    │   └── usePlayerState.js # WebSocket state sync hook
    ├── components/
    │   ├── TransportBar.jsx
    │   ├── Library.jsx
    │   ├── Playlist.jsx
    │   ├── OffsetSliders.jsx
    │   ├── OBSPanel.jsx
    │   ├── ScreenShare.jsx
    │   └── SettingsPanel.jsx
    └── styles/
        └── theme.css        # Black/blue media-player theme
```
