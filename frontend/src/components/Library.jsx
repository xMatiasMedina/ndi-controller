import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';

export default function Library({ currentVideoId, onPlay }) {
  const [videos, setVideos] = useState([]);
  const [busy, setBusy] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(null); // filename being uploaded
  const fileInputRef = useRef(null);

  const reload = async () => setVideos(await api.listVideos());

  useEffect(() => {
    reload();
  }, []);

  // ---- Upload handler (shared by drag-and-drop and file picker) ----
  const uploadFiles = async (fileList) => {
    const files = Array.from(fileList);
    if (files.length === 0) return;

    setBusy(true);
    for (const file of files) {
      try {
        setUploadProgress(file.name);
        await api.uploadVideo(file);
      } catch (e) {
        alert(`Failed to upload "${file.name}": ${e.message}`);
      }
    }
    setUploadProgress(null);
    setBusy(false);
    await reload();
  };

  // ---- Drag and drop handlers ----
  const onDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(true);
  };

  const onDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
  };

  const onDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      uploadFiles(e.dataTransfer.files);
    }
  };

  // ---- File picker ----
  const onFilePickerChange = (e) => {
    if (e.target.files.length > 0) {
      uploadFiles(e.target.files);
    }
    // Reset so the same file can be selected again
    e.target.value = '';
  };

  const onRemove = async (id, e) => {
    e.stopPropagation();
    if (!confirm('Remove this video from the library?')) return;
    await api.removeVideo(id);
    await reload();
  };

  return (
      <div className="panel">
        <div className="panel-header">
          Library
          <span style={{ fontWeight: 400, color: 'var(--text-muted)' }}>
          {videos.length}
        </span>
        </div>
        <div className="panel-body">
          {/* Drop zone */}
          <div
              className={`drop-zone ${dragOver ? 'drag-over' : ''}`}
              onDragOver={onDragOver}
              onDragEnter={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}
              onClick={() => fileInputRef.current?.click()}
          >
            <input
                ref={fileInputRef}
                type="file"
                accept="video/*"
                multiple
                style={{ display: 'none' }}
                onChange={onFilePickerChange}
            />
            {uploadProgress ? (
                <div className="drop-zone-text uploading">
                  <span className="upload-spinner" />
                  Uploading {uploadProgress}…
                </div>
            ) : (
                <div className="drop-zone-text">
                  <span style={{ fontSize: 20, marginBottom: 4 }}>⇪</span>
                  Drop video files here or click to browse
                </div>
            )}
          </div>

          {/* Video list */}
          {videos.length === 0 ? (
              <div className="empty-hint">No videos yet. Drop files above to upload.</div>
          ) : (
              videos.map((v) => (
                  <div
                      key={v.id}
                      className={`list-item ${currentVideoId === v.id ? 'active' : ''}`}
                      onClick={() => onPlay(v.id)}
                      title={v.path}
                  >
                    <div style={{ minWidth: 0 }}>
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {v.name}
                      </div>
                      <div className="meta">
                        {v.width && v.height ? `${v.width}×${v.height}` : '?'}
                        {' · '}
                        {v.fps ? `${v.fps.toFixed(1)}fps` : '?fps'}
                        {' · '}
                        {v.duration_seconds ? `${v.duration_seconds.toFixed(1)}s` : '?'}
                      </div>
                    </div>
                    <button
                        onClick={(e) => onRemove(v.id, e)}
                        style={{ padding: '2px 8px', fontSize: 11, flexShrink: 0 }}
                        title="Remove"
                    >
                      ×
                    </button>
                  </div>
              ))
          )}
        </div>
      </div>
  );
}