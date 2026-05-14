import { useState, useEffect } from 'react';
import { api } from '../api.js';

function fmt(s) {
  if (!isFinite(s)) return '--:--';
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${String(sec).padStart(2, '0')}`;
}

export default function TransportBar({ state, currentName }) {
  const [scrub, setScrub] = useState(null);

  // Reset scrub override whenever the live position updates
  useEffect(() => {
    if (scrub === null) return;
    const t = setTimeout(() => setScrub(null), 800);
    return () => clearTimeout(t);
  }, [state?.position_seconds]);

  if (!state) return <div className="transport-bar">Connecting…</div>;

  const isPlaying = state.status === 'playing';
  const isPaused = state.status === 'paused';
  const stopped = state.status === 'stopped';

  const onPlayPause = async () => {
    if (isPlaying) await api.pause();
    else if (isPaused) await api.resume();
  };

  const display = scrub ?? state.position_seconds;

  return (
    <div className="transport-bar">
      <div className="transport-row">
        <div className="now-playing">
          <div className="title">{currentName || 'Nothing loaded'}</div>
          <div className="subtitle">
            {state.mode.toUpperCase()}
            {state.mode === 'playlist' && ` · item ${state.playlist_cursor + 1}`}
            {state.loop && ' · LOOP'}
          </div>
        </div>

        <div className="transport-controls">
          <button
            className="icon"
            onClick={() => api.previous()}
            disabled={state.mode !== 'playlist'}
            title="Previous"
          >
            ⏮
          </button>
          <button
            className="icon primary"
            onClick={onPlayPause}
            disabled={stopped}
            title={isPlaying ? 'Pause' : 'Play'}
          >
            {isPlaying ? '⏸' : '▶'}
          </button>
          <button
            className="icon"
            onClick={() => api.stop()}
            disabled={stopped}
            title="Stop"
          >
            ⏹
          </button>
          <button
            className="icon"
            onClick={() => api.next()}
            disabled={state.mode !== 'playlist'}
            title="Next"
          >
            ⏭
          </button>
          <button
            className="icon"
            onClick={() => api.setMuted(!state.muted)}
            title={state.muted ? 'Unmute' : 'Mute'}
            style={state.muted ? { background: 'var(--danger)', borderColor: 'var(--danger)' } : {}}
          >
            {state.muted ? '🔇' : '🔊'}
          </button>
        </div>
      </div>

      <div className="scrubber-row">
        <span className="time-label">{fmt(display)}</span>
        <input
          type="range"
          min={0}
          max={state.duration_seconds || 0}
          step={0.1}
          value={display}
          disabled={stopped || !state.duration_seconds}
          onChange={(e) => setScrub(parseFloat(e.target.value))}
          onMouseUp={(e) => {
            const v = parseFloat(e.target.value);
            api.seek(v);
          }}
          onTouchEnd={(e) => {
            const v = parseFloat(e.target.value);
            api.seek(v);
          }}
        />
        <span className="time-label">{fmt(state.duration_seconds)}</span>
      </div>
    </div>
  );
}
