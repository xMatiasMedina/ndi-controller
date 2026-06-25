import { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';
import { usePlayerState } from './hooks/usePlayerState.js';

/**
 * Simple "cast remote" — a stripped-down operator surface served at /remote.
 * Turns screens on/off (via the Modbus relay) and casts a video or playlist.
 * The full technical console lives at / (advanced).
 */
export default function RemotePage() {
  const { state } = usePlayerState();
  const [videos, setVideos] = useState([]);
  const [playlists, setPlaylists] = useState([]);
  const [screens, setScreens] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadScreens = useCallback(async () => {
    try {
      setScreens(await api.getScreens());
    } catch {
      /* relay offline — handled by the connection pill */
    }
  }, []);

  useEffect(() => {
    api.listVideos().then(setVideos).catch(() => {});
    api.listPlaylists().then(setPlaylists).catch(() => {});
    loadScreens();
    const id = setInterval(loadScreens, 3000);
    return () => clearInterval(id);
  }, [loadScreens]);

  const screenAction = async (fn) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      alert(e.message);
    } finally {
      setBusy(false);
      loadScreens();
    }
  };

  const castVideo = async (id) => {
    try {
      await api.play({ mode: 'single', video_id: id, loop: false });
    } catch (e) {
      alert(`Cast failed: ${e.message}`);
    }
  };

  const castPlaylist = async (id) => {
    try {
      await api.play({ mode: 'playlist', playlist_id: id, loop: true });
    } catch (e) {
      alert(`Cast failed: ${e.message}`);
    }
  };

  const stop = async () => {
    try {
      await api.stop();
    } catch (e) {
      alert(e.message);
    }
  };

  const nowName = (() => {
    if (!state) return '—';
    if (state.mode === 'browser') return 'Screen share';
    if (state.mode === 'screen') return 'Screen capture';
    if (state.current_video_id) {
      return (
        videos.find((v) => v.id === state.current_video_id)?.name ?? 'Playing'
      );
    }
    return state.status === 'stopped' ? 'Nothing playing' : 'Playing';
  })();

  const connected = screens?.connected;

  return (
    <div className="remote-page">
      <header className="remote-header">
        <div className="remote-title">
          Cast <span className="accent">Remote</span>
        </div>
        <a className="remote-advanced" href="/">
          Advanced ›
        </a>
      </header>

      {/* Screens */}
      <section className="remote-section">
        <div className="remote-section-head">
          <span>Screens</span>
          <span className={`pill ${connected ? 'live' : 'off'}`}>
            <span className="dot" /> {connected ? 'Relay online' : 'Relay offline'}
          </span>
        </div>

        <div className="screen-master">
          <button
            className="remote-btn big on"
            disabled={busy}
            onClick={() => screenAction(() => api.screensAll('on'))}
          >
            All On
          </button>
          <button
            className="remote-btn big off"
            disabled={busy}
            onClick={() => screenAction(() => api.screensAll('off'))}
          >
            All Off
          </button>
        </div>

        <div className="screen-grid">
          {(screens?.groups ?? []).map((g) => (
            <div key={g.id} className={`screen-ctl ${g.on ? 'is-on' : ''}`}>
              <div className="screen-name">
                {g.name}
                <span className="screen-state">{g.on ? 'ON' : 'OFF'}</span>
              </div>
              <div className="screen-buttons">
                <button
                  className="remote-btn on"
                  disabled={busy}
                  onClick={() => screenAction(() => api.screensGroup(g.id, 'on'))}
                >
                  On
                </button>
                <button
                  className="remote-btn off"
                  disabled={busy}
                  onClick={() => screenAction(() => api.screensGroup(g.id, 'off'))}
                >
                  Off
                </button>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Now casting */}
      <section className="remote-section">
        <div className="remote-section-head">
          <span>Now Casting</span>
        </div>
        <div className="now-casting">
          <div className="now-name">{nowName}</div>
          <button className="remote-btn stop" onClick={stop}>
            ■ Stop
          </button>
        </div>
      </section>

      {/* Cast targets */}
      <section className="remote-section">
        <div className="remote-section-head">
          <span>Playlists</span>
        </div>
        <div className="cast-list">
          {playlists.length === 0 && (
            <div className="empty-hint">No playlists.</div>
          )}
          {playlists.map((p) => (
            <button
              key={p.id}
              className={`cast-item ${
                state?.current_playlist_id === p.id ? 'active' : ''
              }`}
              onClick={() => castPlaylist(p.id)}
            >
              <span className="cast-item-name">{p.name}</span>
              <span className="cast-item-meta">{p.video_ids.length} ▶</span>
            </button>
          ))}
        </div>

        <div className="remote-section-head" style={{ marginTop: 18 }}>
          <span>Videos</span>
        </div>
        <div className="cast-list">
          {videos.length === 0 && <div className="empty-hint">No videos.</div>}
          {videos.map((v) => (
            <button
              key={v.id}
              className={`cast-item ${
                state?.current_video_id === v.id && state?.mode === 'single'
                  ? 'active'
                  : ''
              }`}
              onClick={() => castVideo(v.id)}
            >
              <span className="cast-item-name">{v.name}</span>
              <span className="cast-item-meta">▶</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}
