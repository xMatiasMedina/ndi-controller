import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';

const RANGE = 2000; // ±2000 ms

export default function OffsetSliders({ videoOffsetMs, audioOffsetMs }) {
  const [v, setV] = useState(videoOffsetMs);
  const [a, setA] = useState(audioOffsetMs);
  const debounce = useRef(null);

  // External (WS) updates flow in
  useEffect(() => setV(videoOffsetMs), [videoOffsetMs]);
  useEffect(() => setA(audioOffsetMs), [audioOffsetMs]);

  const send = (newV, newA) => {
    clearTimeout(debounce.current);
    debounce.current = setTimeout(() => {
      api.setOffsets(newV, newA).catch((e) => console.warn(e));
    }, 60);
  };

  return (
    <div className="panel">
      <div className="panel-header">Sync Offsets</div>
      <div className="panel-body">
        <div className="offset-group">
          <label>
            <span>Video → OBS</span>
            <span className="value">{v >= 0 ? '+' : ''}{v} ms</span>
          </label>
          <input
            type="range"
            min={-RANGE}
            max={RANGE}
            value={v}
            onChange={(e) => {
              const nv = parseInt(e.target.value, 10);
              setV(nv);
              send(nv, a);
            }}
          />
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 10, color: 'var(--text-muted)' }}>
            <span>−{RANGE}</span><span>0</span><span>+{RANGE}</span>
          </div>
        </div>

        <div className="offset-group">
          <label>
            <span>Audio → Reaper</span>
            <span className="value">{a >= 0 ? '+' : ''}{a} ms</span>
          </label>
          <input
            type="range"
            min={-RANGE}
            max={RANGE}
            value={a}
            onChange={(e) => {
              const na = parseInt(e.target.value, 10);
              setA(na);
              send(v, na);
            }}
          />
        </div>

        <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 12 }}>
          Positive values delay that stream. Use to compensate downstream latency.
        </div>

        <div style={{ marginTop: 16, display: 'flex', gap: 6, justifyContent: 'center' }}>
          <button onClick={() => { setV(0); setA(0); send(0, 0); }}>
            Reset both
          </button>
        </div>
      </div>
    </div>
  );
}
