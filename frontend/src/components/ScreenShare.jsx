import { useRef, useState, useCallback, useEffect } from 'react';
import { api } from '../api.js';

/**
 * ScreenShare — captures the user's screen via getDisplayMedia, draws frames
 * to an off-screen canvas, encodes as JPEG, and sends over WebSocket to
 * /ws/screen-share where the backend pipes it into NDI.
 *
 * Uses requestAnimationFrame with a frame-time gate instead of setInterval
 * to avoid overlapping toBlob encode calls that cause frame pile-up.
 * Each frame encode+send must complete before the next one starts.
 */

const TARGET_FPS = 30;
const JPEG_QUALITY = 0.75;
const FRAME_INTERVAL = 1000 / TARGET_FPS; // ms between frames

const isSecureContext =
    window.isSecureContext ||
    location.protocol === 'https:' ||
    location.hostname === 'localhost' ||
    location.hostname === '127.0.0.1';

export default function ScreenShare() {
    const [sharing, setSharing] = useState(false);
    const [error, setError] = useState(null);
    const streamRef = useRef(null);
    const wsRef = useRef(null);
    const rafRef = useRef(null);
    const canvasRef = useRef(null);
    const videoRef = useRef(null);
    const cleaningUp = useRef(false);

    // Cleanup helper — idempotent, safe to call multiple times
    const cleanup = useCallback(() => {
        if (cleaningUp.current) return;
        cleaningUp.current = true;

        if (rafRef.current) {
            cancelAnimationFrame(rafRef.current);
            rafRef.current = null;
        }
        if (wsRef.current) {
            try { wsRef.current.close(); } catch {}
            wsRef.current = null;
        }
        if (streamRef.current) {
            streamRef.current.getTracks().forEach((t) => t.stop());
            streamRef.current = null;
        }
        if (videoRef.current) {
            videoRef.current.srcObject = null;
            videoRef.current = null;
        }
        canvasRef.current = null;
        setSharing(false);
        cleaningUp.current = false;
    }, []);

    // Stop sharing — tell backend, then clean up locally
    const stopSharing = useCallback(async () => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            try {
                wsRef.current.send(JSON.stringify({ action: 'stop' }));
            } catch {}
        }
        cleanup();
        try { await api.stop(); } catch {}
    }, [cleanup]);

    // Cleanup on unmount
    useEffect(() => {
        return () => cleanup();
    }, [cleanup]);

    const startSharing = useCallback(async () => {
        setError(null);
        try {
            // 1. Request screen capture from browser
            const stream = await navigator.mediaDevices.getDisplayMedia({
                video: { frameRate: { ideal: TARGET_FPS } },
                audio: false,
            });
            streamRef.current = stream;

            // Detect when user clicks the browser's native "Stop sharing" button
            stream.getVideoTracks()[0].onended = () => {
                cleanup();
                api.stop().catch(() => {});
            };

            // 2. Set up hidden video element to receive the stream
            const video = document.createElement('video');
            video.srcObject = stream;
            video.muted = true;
            await video.play();
            videoRef.current = video;

            // Wait for video dimensions to be available
            await new Promise((resolve) => {
                const check = () => {
                    if (video.videoWidth > 0) resolve();
                    else requestAnimationFrame(check);
                };
                check();
            });

            // 3. Set up canvas for frame extraction
            const canvas = document.createElement('canvas');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            canvasRef.current = canvas;

            // 4. Tell backend to enter browser mode
            await api.play({ mode: 'browser' });

            // 5. Open WebSocket for frame transport
            const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${proto}//${window.location.host}/ws/screen-share`);
            wsRef.current = ws;

            ws.onopen = () => {
                setSharing(true);

                const ctx = canvas.getContext('2d');
                let lastFrameTime = 0;
                let encoding = false; // gate: prevents overlapping toBlob calls

                const captureLoop = (timestamp) => {
                    if (!wsRef.current || ws.readyState !== WebSocket.OPEN) return;

                    rafRef.current = requestAnimationFrame(captureLoop);

                    // Throttle to TARGET_FPS
                    if (timestamp - lastFrameTime < FRAME_INTERVAL) return;

                    // If the previous toBlob hasn't finished yet, skip this
                    // frame rather than piling up encode jobs
                    if (encoding) return;

                    lastFrameTime = timestamp;
                    encoding = true;

                    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
                    canvas.toBlob(
                        (blob) => {
                            encoding = false;
                            if (blob && wsRef.current && ws.readyState === WebSocket.OPEN) {
                                ws.send(blob);
                            }
                        },
                        'image/jpeg',
                        JPEG_QUALITY,
                    );
                };

                rafRef.current = requestAnimationFrame(captureLoop);
            };

            ws.onclose = () => {
                cleanup();
            };

            ws.onerror = (e) => {
                console.error('[screen-share] WS error', e);
                setError('WebSocket connection failed');
                cleanup();
            };
        } catch (e) {
            if (e.name === 'NotAllowedError') {
                return; // User cancelled the screen picker
            }
            console.error('[screen-share] start failed:', e);
            setError(e.message);
            cleanup();
        }
    }, [cleanup]);

    if (!isSecureContext) {
        return (
            <div className="screen-share-section">
                <button className="screen-share-btn" disabled>
                    <span className="screen-share-icon">⊞</span> Share Screen
                </button>
                <div style={{ fontSize: 11, color: 'var(--warning)', marginTop: 6 }}>
                    Screen sharing requires HTTPS. Access via https:// or localhost.
                </div>
            </div>
        );
    }

    return (
        <div className="screen-share-section">
            {!sharing ? (
                <button className="primary screen-share-btn" onClick={startSharing}>
                    <span className="screen-share-icon">⊞</span> Share Screen
                </button>
            ) : (
                <button
                    className="screen-share-btn stop"
                    onClick={stopSharing}
                >
                    <span className="screen-share-icon pulse">●</span> Stop Sharing
                </button>
            )}
            {error && (
                <div style={{ fontSize: 11, color: 'var(--danger)', marginTop: 6 }}>
                    {error}
                </div>
            )}
            {sharing && (
                <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 4 }}>
                    Streaming to NDI at ~{TARGET_FPS} fps
                </div>
            )}
        </div>
    );
}