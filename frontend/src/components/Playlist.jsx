import { useEffect, useState } from 'react';
import { api } from '../api.js';

export default function Playlist({ currentPlaylistId, loop, onPlay, onLoopChange }) {
  const [playlists, setPlaylists] = useState([]);
  const [videos, setVideos] = useState([]);
  const [newName, setNewName] = useState('');

  const reload = async () => {
    setPlaylists(await api.listPlaylists());
    setVideos(await api.listVideos());
  };

  useEffect(() => {
    reload();
  }, []);

  const onCreate = async () => {
    if (!newName.trim()) return;
    await api.createPlaylist(newName.trim());
    setNewName('');
    await reload();
  };

  const onRemove = async (id, e) => {
    e.stopPropagation();
    if (!confirm('Delete this playlist?')) return;
    await api.removePlaylist(id);
    await reload();
  };

  const onAddVideo = async (playlistId, videoId) => {
    const pl = playlists.find((p) => p.id === playlistId);
    if (!pl || pl.video_ids.includes(videoId)) return;
    await api.updatePlaylist(playlistId, {
      video_ids: [...pl.video_ids, videoId],
    });
    await reload();
  };

  const onRemoveVideoFromPlaylist = async (playlistId, videoId) => {
    const pl = playlists.find((p) => p.id === playlistId);
    if (!pl) return;
    await api.updatePlaylist(playlistId, {
      video_ids: pl.video_ids.filter((id) => id !== videoId),
    });
    await reload();
  };

  const videoName = (id) =>
    videos.find((v) => v.id === id)?.name ?? '(missing)';

  return (
    <div className="panel">
      <div className="panel-header">
        Playlists
        <label className="checkbox-row">
          <input
            type="checkbox"
            checked={loop}
            onChange={(e) => onLoopChange(e.target.checked)}
          />
          Loop
        </label>
      </div>
      <div className="panel-body">
        <div className="add-form">
          <input
            type="text"
            placeholder="New playlist name"
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && onCreate()}
          />
          <button onClick={onCreate}>Create</button>
        </div>

        {playlists.length === 0 ? (
          <div className="empty-hint">No playlists yet.</div>
        ) : (
          playlists.map((p) => (
            <div key={p.id} style={{ marginBottom: 16 }}>
              <div
                className={`list-item ${currentPlaylistId === p.id ? 'active' : ''}`}
                onClick={() => onPlay(p.id)}
              >
                <div>
                  <div>{p.name}</div>
                  <div className="meta">{p.video_ids.length} items</div>
                </div>
                <button
                  onClick={(e) => onRemove(p.id, e)}
                  style={{ padding: '2px 8px', fontSize: 11 }}
                >
                  ×
                </button>
              </div>

              {p.video_ids.length > 0 && (
                <ul style={{ margin: '4px 0 0 16px', padding: 0, listStyle: 'none' }}>
                  {p.video_ids.map((vid, i) => (
                    <li
                      key={vid}
                      style={{
                        fontSize: 12,
                        color: 'var(--text-secondary)',
                        padding: '2px 0',
                        display: 'flex',
                        justifyContent: 'space-between',
                        alignItems: 'center',
                      }}
                    >
                      <span>{i + 1}. {videoName(vid)}</span>
                      <button
                        onClick={() => onRemoveVideoFromPlaylist(p.id, vid)}
                        style={{ padding: '0 6px', fontSize: 10 }}
                      >
                        ×
                      </button>
                    </li>
                  ))}
                </ul>
              )}

              <select
                onChange={(e) => {
                  if (e.target.value) {
                    onAddVideo(p.id, e.target.value);
                    e.target.value = '';
                  }
                }}
                style={{ marginTop: 4, marginLeft: 16, fontSize: 11 }}
                defaultValue=""
              >
                <option value="">+ add video…</option>
                {videos.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>
          ))
        )}
      </div>
    </div>
  );
}
