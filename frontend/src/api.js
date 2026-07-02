/**
 * API client. All HTTP calls in the app go through here so the URL surface
 * is in one place. If we change a route we change one file.
 */

async function request(path, opts = {}) {
    const res = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...opts,
    });
    if (!res.ok) {
        const err = await res.text();
        throw new Error(`${res.status}: ${err}`);
    }
    if (res.status === 204) return null;
    return res.json();
}

export const api = {
    // Library
    listVideos: () => request('/api/library'),
    addVideo: (path) =>
        request('/api/library', { method: 'POST', body: JSON.stringify({ path }) }),
    removeVideo: (id) => request(`/api/library/${id}`, { method: 'DELETE' }),

    /**
     * Upload a video file to the server.
     * Accepts a File object (from drag-and-drop or file input).
     * Returns the created VideoAsset.
     */
    uploadVideo: async (file) => {
        const form = new FormData();
        form.append('file', file);
        const res = await fetch('/api/library/upload', {
            method: 'POST',
            body: form,
            // No Content-Type header — browser sets it with boundary for multipart
        });
        if (!res.ok) {
            const err = await res.text();
            throw new Error(`${res.status}: ${err}`);
        }
        return res.json();
    },

    // Playlists
    listPlaylists: () => request('/api/playlists'),
    createPlaylist: (name, video_ids = []) =>
        request('/api/playlists', {
            method: 'POST',
            body: JSON.stringify({ name, video_ids }),
        }),
    updatePlaylist: (id, body) =>
        request(`/api/playlists/${id}`, { method: 'PUT', body: JSON.stringify(body) }),
    removePlaylist: (id) => request(`/api/playlists/${id}`, { method: 'DELETE' }),

    // Player
    getPlayerState: () => request('/api/player/state'),
    play: (body) =>
        request('/api/player/play', { method: 'POST', body: JSON.stringify(body) }),
    pause: () => request('/api/player/pause', { method: 'POST' }),
    resume: () => request('/api/player/resume', { method: 'POST' }),
    stop: () => request('/api/player/stop', { method: 'POST' }),
    seek: (position_seconds) =>
        request('/api/player/seek', {
            method: 'POST',
            body: JSON.stringify({ position_seconds }),
        }),
    next: () => request('/api/player/next', { method: 'POST' }),
    previous: () => request('/api/player/previous', { method: 'POST' }),
    setOffsets: (video_offset_ms, audio_offset_ms, video_offset_enabled, audio_offset_enabled) =>
        request('/api/player/offsets', {
            method: 'POST',
            body: JSON.stringify({
                video_offset_ms,
                audio_offset_ms,
                video_offset_enabled,
                audio_offset_enabled,
            }),
        }),
    setMuted: (muted) =>
        request('/api/player/mute', {
            method: 'POST',
            body: JSON.stringify({ muted }),
        }),

    // OBS
    obsConnect: () => request('/api/obs/connect', { method: 'POST' }),
    obsDisconnect: () => request('/api/obs/disconnect', { method: 'POST' }),
    obsStatus: () => request('/api/obs/status'),
    obsScenes: () => request('/api/obs/scenes'),
    obsSetScene: (sceneName) =>
        request(`/api/obs/scene/${encodeURIComponent(sceneName)}`, { method: 'POST' }),
    obsSceneSources: (sceneName) =>
        request(`/api/obs/scene/${encodeURIComponent(sceneName)}/sources`),
    obsSetSourceEnabled: (sceneName, itemId, enabled) =>
        request(`/api/obs/scene/${encodeURIComponent(sceneName)}/source/${itemId}`, {
            method: 'POST',
            body: JSON.stringify({ enabled }),
        }),

    // Settings
    getSettings: () => request('/api/settings'),
    updateSettings: (settings) =>
        request('/api/settings', { method: 'PUT', body: JSON.stringify(settings) }),

    // Screens (Modbus relay)
    getScreens: () => request('/api/screens'),
    screensAll: (action) =>
        request(`/api/screens/all/${action}`, { method: 'POST' }),
    screensGroup: (groupId, action) =>
        request(`/api/screens/group/${encodeURIComponent(groupId)}/${action}`, {
            method: 'POST',
        }),

    // Default playlist (resting-state loop)
    setDefaultPlaylist: (playlist_id) =>
        request('/api/default-playlist', {
            method: 'POST',
            body: JSON.stringify({ playlist_id }),
        }),

    // Cast the /remote dashboard to a Google Nest Hub
    castStatus: () => request('/api/cast/status'),
    castStart: (ip, url) =>
        request('/api/cast/start', {
            method: 'POST',
            body: JSON.stringify(url ? { ip, url } : { ip }),
        }),
    castStop: () => request('/api/cast/stop', { method: 'POST' }),

    // WebRTC screen share (WHIP — raw SDP in, raw SDP answer out)
    screenShareWhip: async (sdp) => {
        const res = await fetch('/api/screen-share/whip', {
            method: 'POST',
            headers: { 'content-type': 'application/sdp' },
            body: sdp,
        });
        if (!res.ok) throw new Error(`${res.status}: ${await res.text()}`);
        return res.text();
    },
    screenShareStop: () => request('/api/screen-share/stop', { method: 'POST' }),
};