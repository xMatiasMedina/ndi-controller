import { useEffect, useState } from 'react';
import { api } from '../api.js';

export default function SettingsPanel({ onClose }) {
    const [settings, setSettings] = useState(null);
    const [saving, setSaving] = useState(false);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        api.getSettings().then(setSettings).catch((e) => alert(e.message));
    }, []);

    const update = (section, field, value) => {
        setSettings((prev) => ({
            ...prev,
            [section]: { ...prev[section], [field]: value },
        }));
        setSaved(false);
    };

    const updateGroup = (index, field, value) => {
        setSettings((prev) => ({
            ...prev,
            modbus: {
                ...prev.modbus,
                groups: prev.modbus.groups.map((g, i) =>
                    i === index ? { ...g, [field]: value } : g
                ),
            },
        }));
        setSaved(false);
    };

    const onSave = async () => {
        setSaving(true);
        try {
            const updated = await api.updateSettings(settings);
            setSettings(updated);
            setSaved(true);
            setTimeout(() => setSaved(false), 2000);
        } catch (e) {
            alert(`Save failed: ${e.message}`);
        } finally {
            setSaving(false);
        }
    };

    if (!settings) {
        return (
            <div className="settings-overlay" onClick={onClose}>
                <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
                    <div className="panel-header">Settings</div>
                    <div className="panel-body">
                        <div className="empty-hint">Loading…</div>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="settings-overlay" onClick={onClose}>
            <div className="settings-modal" onClick={(e) => e.stopPropagation()}>
                <div className="panel-header">
                    Settings
                    <button
                        onClick={onClose}
                        style={{ padding: '2px 8px', fontSize: 14 }}
                    >
                        ✕
                    </button>
                </div>
                <div className="panel-body" style={{ padding: 20 }}>
                    {/* OBS */}
                    <div className="settings-section">
                        <div className="settings-section-title">OBS WebSocket</div>
                        <div className="settings-row">
                            <label>Host</label>
                            <input
                                type="text"
                                value={settings.obs.host}
                                onChange={(e) => update('obs', 'host', e.target.value)}
                            />
                        </div>
                        <div className="settings-row">
                            <label>Port</label>
                            <input
                                type="number"
                                value={settings.obs.port}
                                onChange={(e) => update('obs', 'port', parseInt(e.target.value) || 0)}
                            />
                        </div>
                        <div className="settings-row">
                            <label>Password</label>
                            <input
                                type="password"
                                value={settings.obs.password}
                                onChange={(e) => update('obs', 'password', e.target.value)}
                                placeholder="(blank if none)"
                            />
                        </div>
                    </div>

                    {/* Reaper */}
                    <div className="settings-section">
                        <div className="settings-section-title">Reaper OSC</div>
                        <div className="settings-row">
                            <label>Host</label>
                            <input
                                type="text"
                                value={settings.reaper.host}
                                onChange={(e) => update('reaper', 'host', e.target.value)}
                            />
                        </div>
                        <div className="settings-row">
                            <label>Port</label>
                            <input
                                type="number"
                                value={settings.reaper.port}
                                onChange={(e) => update('reaper', 'port', parseInt(e.target.value) || 0)}
                            />
                        </div>
                        <div className="settings-row">
                            <label className="checkbox-row" style={{ justifyContent: 'flex-start' }}>
                                <input
                                    type="checkbox"
                                    checked={settings.reaper.lockstep}
                                    onChange={(e) => update('reaper', 'lockstep', e.target.checked)}
                                />
                                Lockstep transport (mirror play/stop/seek to Reaper)
                            </label>
                        </div>
                    </div>

                    {/* NDI */}
                    <div className="settings-section">
                        <div className="settings-section-title">NDI Source Names</div>
                        <div className="settings-row">
                            <label>Video (→ OBS)</label>
                            <input
                                type="text"
                                value={settings.ndi.video_source_name}
                                onChange={(e) => update('ndi', 'video_source_name', e.target.value)}
                            />
                        </div>
                        <div className="settings-row">
                            <label>Audio (→ Reaper)</label>
                            <input
                                type="text"
                                value={settings.ndi.audio_source_name}
                                onChange={(e) => update('ndi', 'audio_source_name', e.target.value)}
                            />
                        </div>
                    </div>

                    {/* Screens / Modbus relay */}
                    {settings.modbus && (
                        <div className="settings-section">
                            <div className="settings-section-title">Screens (Modbus relay)</div>
                            <div className="settings-row">
                                <label>Host</label>
                                <input
                                    type="text"
                                    value={settings.modbus.host}
                                    onChange={(e) => update('modbus', 'host', e.target.value)}
                                />
                            </div>
                            <div className="settings-row">
                                <label>Port</label>
                                <input
                                    type="number"
                                    value={settings.modbus.port}
                                    onChange={(e) => update('modbus', 'port', parseInt(e.target.value) || 0)}
                                />
                            </div>
                            <div className="settings-row">
                                <label>Unit ID</label>
                                <input
                                    type="number"
                                    value={settings.modbus.unit_id}
                                    onChange={(e) => update('modbus', 'unit_id', parseInt(e.target.value) || 0)}
                                />
                            </div>
                            <div className="settings-row">
                                <label>Channels</label>
                                <input
                                    type="number"
                                    value={settings.modbus.num_channels}
                                    onChange={(e) => update('modbus', 'num_channels', parseInt(e.target.value) || 0)}
                                />
                            </div>
                            <div className="settings-subtitle">
                                Screen groups — channels comma-separated
                            </div>
                            {settings.modbus.groups.map((g, i) => (
                                <div className="settings-row group-row" key={g.id}>
                                    <input
                                        className="group-name"
                                        type="text"
                                        value={g.name}
                                        onChange={(e) => updateGroup(i, 'name', e.target.value)}
                                    />
                                    <input
                                        className="group-channels"
                                        type="text"
                                        value={g.channels.join(', ')}
                                        placeholder="e.g. 7, 8"
                                        onChange={(e) =>
                                            updateGroup(
                                                i,
                                                'channels',
                                                e.target.value
                                                    .split(',')
                                                    .map((s) => parseInt(s.trim(), 10))
                                                    .filter((n) => !isNaN(n))
                                            )
                                        }
                                    />
                                </div>
                            ))}
                        </div>
                    )}

                    <div style={{ display: 'flex', gap: 8, marginTop: 20, justifyContent: 'flex-end' }}>
                        {saved && (
                            <span style={{ color: 'var(--success)', fontSize: 13, alignSelf: 'center' }}>
                ✓ Saved
              </span>
                        )}
                        <button onClick={onClose}>Cancel</button>
                        <button className="primary" onClick={onSave} disabled={saving}>
                            {saving ? 'Saving…' : 'Save'}
                        </button>
                    </div>
                </div>
            </div>
        </div>
    );
}