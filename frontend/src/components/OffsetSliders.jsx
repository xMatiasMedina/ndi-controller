import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';

const RANGE = 2000; // ±2000 ms

export default function OffsetSliders({
  videoOffsetMs,
  audioOffsetMs,
  videoOffsetEnabled,
  audioOffsetEnabled,
  isLive,
}) {
  const [v, setV] = useState(videoOffsetMs);
  const [a, setA] = useState(audioOffsetMs);
  const [vEn, setVEn] = useState(videoOffsetEnabled !== false);
  const [aEn, setAEn] = useState(audioOffsetEnabled !== false);
  const debounce = useRef(null);

  // External (WS) updates flow in
  useEffect(() => setV(videoOffsetMs), [videoOffsetMs]);
  useEffect(() => setA(audioOffsetMs), [audioOffsetMs]);
  useEffect(() => setVEn(videoOffsetEnabled !== false), [videoOffsetEnabled]);
  useEffect(() => setAEn(audioOffsetEnabled !== false), [audioOffsetEnabled]);

  // Live streams can only be delayed (not advanced), so the floor is 0.
  const min = isLive ? 0 : -RANGE;
  const clamp = (x) => (isLive ? Math.max(0, x) : x);

  const send = (nv, na, nvEn, naEn) => {
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      api.setOffsets(clamp(nv), clamp(na), nvEn, naEn).catch((e) => console.warn(e));
    }, 60);
  };

  const fmt = (x) => `${x >= 0 ? '+' : ''}${x} ms`;
  const cb = { accentColor: 'var(--accent, #4f9dff)', width: 15, height: 15, cursor: 'pointer' };

  return (
    <div className="panel">
      <div className="panel-header">Sync Offsets</div>
      <div className="panel-body">
        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginBottom: 10 }}>
          Tick a stream to apply its offset; untick to bypass it — no delay buffer,
          minimal latency.
        </div>

        <div className="offset-group">
          <label>
            <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <input
                type="checkbox"
                style={cb}
                checked={vEn}
                onChange={(e) => { setVEn(e.target.checked); send(v, a, e.target.checked, aEn); }}
                title="Enable the video offset. Uncheck to bypass its delay buffer."
              />
              <span>Video → OBS</span>
            </span>
            <span className="value" style={{ opacity: vEn ? 1 : 0.45 }}>
              {vEn ? fmt(clamp(v)) : 'off'}
            </span>
          </label>
          <input
            type="range"
            min={min}
            max={RANGE}
            value={clamp(v)}
            disabled={!vEn}
            onChange={(e) => { const nv = parseInt(e.target.value, 10); setV(nv); send(nv, a, vEn, aEn); }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)' }}>
            <span>{min === 0 ? '0' : `−${RANGE}`}</span>
            <span>{min === 0 ? '' : '0'}</span>
            <span>+{RANGE}</span>
          </div>
        </div>

        <div className="offset-group">
          <label>
            <span style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <input
                type="checkbox"
                style={cb}
                checked={aEn}
                onChange={(e) => { setAEn(e.target.checked); send(v, a, vEn, e.target.checked); }}
                title="Enable the audio offset. Uncheck to bypass its delay buffer."
              />
              <span>Audio → Reaper</span>
            </span>
            <span className="value" style={{ opacity: aEn ? 1 : 0.45 }}>
              {aEn ? fmt(clamp(a)) : 'off'}
            </span>
          </label>
          <input
            type="range"
            min={min}
            max={RANGE}
            value={clamp(a)}
            disabled={!aEn}
            onChange={(e) => { const na = parseInt(e.target.value, 10); setA(na); send(v, na, vEn, aEn); }}
          />
        </div>

        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12 }}>
          Positive values delay that stream. Use to compensate downstream latency.
          {isLive && ' Live streams can only be delayed, not advanced.'}
        </div>

        <div style={{ marginTop: 16, display: 'flex', gap: 6, justifyContent: 'center' }}>
          <button onClick={() => { setV(0); setA(0); send(0, 0, vEn, aEn); }}>
            Reset both
          </button>
        </div>
      </div>
    </div>
  );
}
