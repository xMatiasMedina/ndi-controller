import { useEffect, useRef, useState, useCallback } from 'react';
import { api } from '../api.js';

/**
 * Keeps player state in sync with the backend.
 *  - On mount: fetch initial state + OBS status
 *  - WebSocket: applies all event types as they arrive
 *  - Reconnects on socket close with backoff
 */
export function usePlayerState() {
  const [state, setState] = useState(null);
  const [connected, setConnected] = useState(false);
  const [obsStatus, setObsStatus] = useState({ connected: false });
  const [reaperStatus, setReaperStatus] = useState({ configured: false });
  const [ndiStatus, setNdiStatus] = useState({});
  const wsRef = useRef(null);

  const refresh = useCallback(async () => {
    try {
      const s = await api.getPlayerState();
      setState(s);
    } catch (e) {
      console.warn('refresh failed', e);
    }
  }, []);

  // Fetch initial states
  useEffect(() => {
    refresh();
    api.obsStatus().then(setObsStatus).catch(() => {});
  }, [refresh]);

  useEffect(() => {
    let cancelled = false;
    let backoff = 500;

    const connect = () => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const url = `${proto}//${window.location.host}/ws/state`;
      const ws = new WebSocket(url);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        backoff = 500;
      };
      ws.onclose = () => {
        setConnected(false);
        if (!cancelled) setTimeout(connect, backoff);
        backoff = Math.min(backoff * 2, 5000);
      };
      ws.onerror = () => ws.close();
      ws.onmessage = (e) => {
        try {
          const msg = JSON.parse(e.data);
          switch (msg.event) {
            case 'player.state_changed':
              setState(msg.payload);
              break;
            case 'player.position_changed':
              setState((prev) =>
                  prev
                      ? { ...prev, position_seconds: msg.payload.position_seconds }
                      : prev
              );
              break;
            case 'obs.status_changed':
              setObsStatus(msg.payload);
              break;
            case 'reaper.status_changed':
              setReaperStatus(msg.payload);
              break;
            case 'ndi.status_changed':
              setNdiStatus(msg.payload);
              break;
          }
        } catch {}
      };
    };

    connect();
    return () => {
      cancelled = true;
      if (wsRef.current) wsRef.current.close();
    };
  }, []);

  return { state, connected, obsStatus, reaperStatus, ndiStatus, refresh, setState };
}