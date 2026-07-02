import { useCallback, useEffect, useState } from 'react';
import { api } from './api.js';
import { usePlayerState } from './hooks/usePlayerState.js';

/**
 * Simple "cast remote" — a stripped-down operator surface served at /remote.
 * Turns screens on/off (via the Modbus relay) and casts a video or playlist.
 *
 * Primary target is a Google Nest Hub (1024x600 landscape, touch). Reachable
 * by URL only — there is no link to or from the advanced console.
 */
export default function RemotePage() {
  const { state } = usePlayerState();
  const [videos, setVideos] = useState([]);
  const [playlists, setPlaylists] = useState([]);
  const [screens, setScreens] = useState(null);
  const [defaultId, setDefaultId] = useState(null);
  const [busy, setBusy] = useState(false);

  const loadScreens = useCallback(async () => {
    try {
      setScreens(await api.getScreens());
    } catch {
      /* relay offline */
    }
  }, []);

  useEffect(() => {
    api.listVideos().then(setVideos).catch(() => {});
    api.listPlaylists().then(setPlaylists).catch(() => {});
    api
      .getSettings()
      .then((s) => setDefaultId(s.default_playlist_id ?? null))
      .catch(() => {});
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

  const toggleDefault = async (id) => {
    const next = defaultId === id ? null : id;
    try {
      await api.setDefaultPlaylist(next);
      setDefaultId(next);
    } catch (e) {
      alert(e.message);
    }
  };

  const playing = !!state && state.status !== 'stopped';

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

  const groups = screens?.groups ?? [];

  return (
    <div className="remote-page">
      <header className="remote-header">
        <span className="remote-rule" aria-hidden="true" />
        <span className="remote-logo">
          <img src="/loreatec-logo.png" alt="LoreaTec" />
        </span>
        <span className="remote-rule" aria-hidden="true" />
      </header>

      <div className="remote-grid">
        {/* Screens */}
        <section className="remote-col remote-col-screens">
          <div className="remote-col-label">Screens</div>

          <div className="screen-master">
            <button
              className="mbtn mbtn-on"
              disabled={busy}
              onClick={() => screenAction(() => api.screensAll('on'))}
            >
              All On
            </button>
            <button
              className="mbtn mbtn-off"
              disabled={busy}
              onClick={() => screenAction(() => api.screensAll('off'))}
            >
              All Off
            </button>
          </div>

          <div className="screen-zones">
            {groups.map((g) => (
              <div key={g.id} className={`zone ${g.on ? 'is-on' : 'is-off'}`}>
                <div className="zone-head">
                  <span className="zone-name">{g.name}</span>
                  <span className="zone-state">{g.on ? 'On' : 'Off'}</span>
                </div>
                <div className="zone-toggle">
                  <button
                    className={`seg seg-on ${g.on ? 'active' : ''}`}
                    disabled={busy}
                    onClick={() => screenAction(() => api.screensGroup(g.id, 'on'))}
                  >
                    On
                  </button>
                  <button
                    className={`seg seg-off ${!g.on ? 'active' : ''}`}
                    disabled={busy}
                    onClick={() => screenAction(() => api.screensGroup(g.id, 'off'))}
                  >
                    Off
                  </button>
                </div>
              </div>
            ))}
            {screens && groups.length === 0 && (
              <div className="empty-hint">No screen groups configured.</div>
            )}
          </div>
        </section>

        {/* Cast */}
        <section className="remote-col remote-col-cast">
          <div className="now-casting">
            <div className="now-casting-info">
              <span className="now-label">Now casting</span>
              <span className="now-name">{nowName}</span>
            </div>
            <button
              className={`stop-btn ${playing ? 'armed' : ''}`}
              onClick={stop}
              disabled={!playing}
            >
              ■ Stop
            </button>
          </div>

          <div className="cast-scroll">
            <div className="cast-group-label">Playlists</div>
            <div className="cast-list">
              {playlists.length === 0 && (
                <div className="empty-hint">No playlists.</div>
              )}
              {playlists.map((p) => (
                <div key={p.id} className="cast-row">
                  <button
                    className={`cast-item cast-item-main ${
                      state?.current_playlist_id === p.id ? 'active' : ''
                    }`}
                    onClick={() => castPlaylist(p.id)}
                  >
                    <span className="cast-play">▶</span>
                    <span className="cast-item-name">{p.name}</span>
                    <span className="cast-item-meta">{p.video_ids.length}</span>
                  </button>
                  <button
                    className={`cast-star ${defaultId === p.id ? 'is-default' : ''}`}
                    onClick={() => toggleDefault(p.id)}
                    title={
                      defaultId === p.id
                        ? 'Default — auto-plays when idle (tap to unset)'
                        : 'Set as default'
                    }
                    aria-label="Set as default playlist"
                  >
                    {defaultId === p.id ? '★' : '☆'}
                  </button>
                </div>
              ))}
            </div>

            <div className="cast-group-label">Videos</div>
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
                  <span className="cast-play">▶</span>
                  <span className="cast-item-name">{v.name}</span>
                </button>
              ))}
            </div>
          </div>
        </section>
      </div>
      <video
        className="cast-keepalive"
        src="/keepalive.mp4"
        muted
        loop
        autoPlay
        playsInline
        aria-hidden="true"
      />
    </div>
  );
}
