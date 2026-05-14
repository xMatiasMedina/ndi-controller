import { useEffect, useState, useCallback } from 'react';
import { api } from '../api.js';

export default function OBSPanel({ obsStatus }) {
    const [scenes, setScenes] = useState([]);
    const [current, setCurrent] = useState(null);
    const [sources, setSources] = useState([]);
    const [busy, setBusy] = useState(false);

    const isConnected = obsStatus?.connected ?? false;

    // ---- Scenes ----
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

    // ---- Sources — reload when current scene changes ----
    const loadSources = useCallback(async (sceneName) => {
        if (!isConnected || !sceneName) {
            setSources([]);
            return;
        }
        try {
            const data = await api.obsSceneSources(sceneName);
            setSources(data.sources || []);
        } catch (e) {
            console.warn('Failed to load scene sources:', e);
            setSources([]);
        }
    }, [isConnected]);

    useEffect(() => {
        loadSources(current);
    }, [current, loadSources]);

    // ---- Actions ----
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
            setSources([]);
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

    const onToggleSource = async (itemId, currentlyEnabled) => {
        if (!current) return;
        try {
            await api.obsSetSourceEnabled(current, itemId, !currentlyEnabled);
            setSources((prev) =>
                prev.map((s) =>
                    s.sceneItemId === itemId
                        ? { ...s, sceneItemEnabled: !currentlyEnabled }
                        : s
                )
            );
        } catch (e) {
            alert(`Toggle source failed: ${e.message}`);
        }
    };

    // ---- Not connected state ----
    if (!isConnected) {
        return (
            <div className="panel obs-panel">
                <div className="panel-header">
                    OBS
                    <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                        <span className="conn-badge" title="Disconnected" />
                        <button
                            onClick={onConnect}
                            disabled={busy}
                            className="primary"
                            style={{ padding: '2px 10px', fontSize: 11 }}
                        >
                            Connect
                        </button>
                    </div>
                </div>
                <div className="panel-body">
                    <div className="empty-hint">
                        Not connected to OBS.<br />
                        Check Settings for host/port, then click Connect.
                    </div>
                </div>
            </div>
        );
    }

    // ---- Connected: 50/50 split ----
    return (
        <div className="panel obs-panel obs-split">
            {/* Header with connection controls */}
            <div className="panel-header">
                OBS
                <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <span className="conn-badge on" title="Connected" />
                    <button
                        onClick={onDisconnect}
                        disabled={busy}
                        style={{ padding: '2px 10px', fontSize: 11 }}
                    >
                        Disconnect
                    </button>
                    <button
                        onClick={() => { loadScenes(); loadSources(current); }}
                        style={{ padding: '2px 8px', fontSize: 11 }}
                        title="Refresh"
                    >
                        ↻
                    </button>
                </div>
            </div>

            {/* Scenes half */}
            <div className="obs-half">
                <div className="obs-half-header">
                    Scenes
                    <span className="obs-half-count">{scenes.length}</span>
                </div>
                <div className="obs-half-body">
                    {scenes.length === 0 ? (
                        <div className="empty-hint">No scenes found.</div>
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

            {/* Sources half */}
            <div className="obs-half">
                <div className="obs-half-header">
                    Sources
                    <span className="obs-half-count">
                        {current ? sources.length : '—'}
                    </span>
                </div>
                <div className="obs-half-body">
                    {!current ? (
                        <div className="empty-hint">Select a scene above.</div>
                    ) : sources.length === 0 ? (
                        <div className="empty-hint">No sources in this scene.</div>
                    ) : (
                        sources.map((src) => (
                            <div
                                key={src.sceneItemId}
                                className={`list-item ${src.sceneItemEnabled ? 'active' : ''}`}
                                onClick={() => onToggleSource(src.sceneItemId, src.sceneItemEnabled)}
                            >
                                <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                                    <span
                                        className="source-eye"
                                        title={src.sceneItemEnabled ? 'Visible' : 'Hidden'}
                                    >
                                        {src.sceneItemEnabled ? '👁' : '👁‍🗨'}
                                    </span>
                                    <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                        {src.sourceName}
                                    </span>
                                </div>
                                {src.inputKind && (
                                    <span style={{ fontSize: 10, color: 'var(--text-muted)', flexShrink: 0 }}>
                                        {src.inputKind.replace(/_/g, ' ').replace(/^obs[-_]?/i, '')}
                                    </span>
                                )}
                            </div>
                        ))
                    )}
                </div>
            </div>
        </div>
    );
}
