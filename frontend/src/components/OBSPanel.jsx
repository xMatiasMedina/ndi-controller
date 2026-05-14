import { useEffect, useState, useCallback } from 'react';
import { api } from '../api.js';

export default function OBSPanel({ obsStatus }) {
    const [scenes, setScenes] = useState([]);
    const [current, setCurrent] = useState(null);
    const [busy, setBusy] = useState(false);

    const isConnected = obsStatus?.connected ?? false;

    const loadScenes = useCallback(async () => {
        if (!isConnected) return;
        try {
            const data = await api.obsScenes();
            setScenes(data.scenes || []);
            setCurrent(data.current || null);
        } catch (e) {
            console.warn('Failed to load OBS scenes:', e);
        }
    }, [isConnected]);

    useEffect(() => {
        loadScenes();
    }, [loadScenes]);

    const onConnect = async () => {
        setBusy(true);
        try {
            await api.obsConnect();
        } catch (e) {
            alert(`OBS connect failed: ${e.message}`);
        } finally {
            setBusy(false);
        }
    };

    const onDisconnect = async () => {
        setBusy(true);
        try {
            await api.obsDisconnect();
            setScenes([]);
            setCurrent(null);
        } catch (e) {
            console.warn(e);
        } finally {
            setBusy(false);
        }
    };

    const onSwitchScene = async (name) => {
        try {
            await api.obsSetScene(name);
            setCurrent(name);
        } catch (e) {
            alert(`Scene switch failed: ${e.message}`);
        }
    };

    return (
        <div className="panel obs-panel">
            <div className="panel-header">
                OBS Scenes
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span
              className={`conn-badge ${isConnected ? 'on' : ''}`}
              title={isConnected ? 'Connected' : 'Disconnected'}
          />
                    {isConnected ? (
                        <button
                            onClick={onDisconnect}
                            disabled={busy}
                            style={{ padding: '2px 10px', fontSize: 11 }}
                        >
                            Disconnect
                        </button>
                    ) : (
                        <button
                            onClick={onConnect}
                            disabled={busy}
                            className="primary"
                            style={{ padding: '2px 10px', fontSize: 11 }}
                        >
                            Connect
                        </button>
                    )}
                    {isConnected && (
                        <button
                            onClick={loadScenes}
                            style={{ padding: '2px 10px', fontSize: 11 }}
                            title="Refresh scene list"
                        >
                            ↻
                        </button>
                    )}
                </div>
            </div>
            <div className="panel-body">
                {!isConnected ? (
                    <div className="empty-hint">
                        Not connected to OBS.<br />
                        Check Settings for host/port, then click Connect.
                    </div>
                ) : scenes.length === 0 ? (
                    <div className="empty-hint">No scenes found in OBS.</div>
                ) : (
                    scenes.map((name) => (
                        <div
                            key={name}
                            className={`list-item ${current === name ? 'active' : ''}`}
                            onClick={() => onSwitchScene(name)}
                        >
                            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                {current === name && (
                                    <span style={{ color: 'var(--success)', fontSize: 10 }}>●</span>
                                )}
                                <span>{name}</span>
                            </div>
                            {current === name && (
                                <span style={{ fontSize: 10, color: 'var(--text-muted)' }}>LIVE</span>
                            )}
                        </div>
                    ))
                )}
            </div>
        </div>
    );
}