import { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';
import { usePlayerState } from './hooks/usePlayerState.js';
import Library from './components/Library.jsx';
import Playlist from './components/Playlist.jsx';
import OffsetSliders from './components/OffsetSliders.jsx';
import TransportBar from './components/TransportBar.jsx';
import OBSPanel from './components/OBSPanel.jsx';
import ScreenShare from './components/ScreenShare.jsx';
import SettingsPanel from './components/SettingsPanel.jsx';

export default function App() {
  const { state, connected, obsStatus, reaperStatus } = usePlayerState();
  const [videos, setVideos] = useState([]);
  const [loop, setLoop] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [activeTab, setActiveTab] = useState('offsets'); // 'offsets' | 'obs'

  // Keep a local list of videos for "now playing" name lookup
  useEffect(() => {
    api.listVideos().then(setVideos).catch(() => {});
    const id = setInterval(() => {
      api.listVideos().then(setVideos).catch(() => {});
    }, 5000);
    return () => clearInterval(id);
  }, []);

  // Sync loop checkbox with backend state when WS pushes updates
  useEffect(() => {
    if (state) setLoop(state.loop);
  }, [state?.loop]);

  const currentName = useMemo(() => {
    if (!state?.current_video_id) {
      if (state?.mode === 'screen') return 'Screen capture (server)';
      if (state?.mode === 'browser') return 'Screen share (browser)';
      return null;
    }
    return videos.find((v) => v.id === state.current_video_id)?.name ?? null;
  }, [state, videos]);

  const playVideo = async (videoId) => {
    try {
      await api.play({ mode: 'single', video_id: videoId, loop });
    } catch (e) {
      alert(`Play failed: ${e.message}`);
    }
  };

  const playPlaylist = async (playlistId) => {
    try {
      await api.play({ mode: 'playlist', playlist_id: playlistId, loop });
    } catch (e) {
      alert(`Play failed: ${e.message}`);
    }
  };

  const obsConnected = obsStatus?.connected ?? false;
  const reaperConfigured = reaperStatus?.configured ?? false;
  const isStreaming = state?.status === 'playing';

  return (
      <div className="app">
        <header className="app-header">
          <div className="app-title">
            NDI <span className="accent">Controller</span>
          </div>
          <div className="status-pills">
          <span className={`pill ${connected ? 'live' : 'off'}`}>
            <span className="dot" /> {connected ? 'Live' : 'Offline'}
          </span>
            <span className={`pill ${isStreaming ? 'live' : 'off'}`}>
            <span className="dot" /> NDI {isStreaming ? 'streaming' : 'idle'}
          </span>
            <span className={`pill ${obsConnected ? 'live' : 'off'}`}>
            <span className="dot" /> OBS {obsConnected ? 'linked' : 'off'}
          </span>
            <span className={`pill ${reaperConfigured ? 'live' : 'off'}`}>
            <span className="dot" /> Reaper {reaperConfigured ? 'ready' : 'off'}
          </span>
            <button
                onClick={() => setShowSettings(true)}
                className="settings-btn"
                title="Settings"
            >
              ⚙
            </button>
          </div>
        </header>

        <main className="app-body">
          <Library
              currentVideoId={state?.current_video_id}
              onPlay={playVideo}
          />

          <div className="center-column">
            <Playlist
                currentPlaylistId={state?.current_playlist_id}
                loop={loop}
                onPlay={playPlaylist}
                onLoopChange={setLoop}
            />
            <ScreenShare />
          </div>

          <div className="right-column">
            <div className="tab-bar">
              <button
                  className={`tab-btn ${activeTab === 'offsets' ? 'active' : ''}`}
                  onClick={() => setActiveTab('offsets')}
              >
                Sync
              </button>
              <button
                  className={`tab-btn ${activeTab === 'obs' ? 'active' : ''}`}
                  onClick={() => setActiveTab('obs')}
              >
                OBS
              </button>
            </div>
            {activeTab === 'offsets' && (
                <OffsetSliders
                    videoOffsetMs={state?.video_offset_ms ?? 0}
                    audioOffsetMs={state?.audio_offset_ms ?? 0}
                />
            )}
            {activeTab === 'obs' && <OBSPanel obsStatus={obsStatus} />}
          </div>
        </main>

        <TransportBar state={state} currentName={currentName} />

        {showSettings && <SettingsPanel onClose={() => setShowSettings(false)} />}
      </div>
  );
}