import { useRef, useState, useCallback, useEffect } from 'react';
import { api } from '../api.js';

/**
 * ScreenShare — captures the user's screen via getDisplayMedia, draws frames
 * to an off-screen canvas, encodes as JPEG, and sends over WebSocket to
 * /ws/screen-share where the backend pipes it into NDI.
 *
 * Audio capture: if the browser supports it and the user allows it,
 * captures system/tab audio via AudioContext + ScriptProcessorNode,
 * encodes as raw float32 PCM, and sends with a 4-byte "AUD\x00" prefix
 * so the backend can distinguish audio from video frames.
 */

const TARGET_FPS = 30;
const JPEG_QUALITY = 0.75;
const FRAME_INTERVAL = 1000 / TARGET_FPS;
const AUDIO_MAGIC = new Uint8Array([0x41, 0x55, 0x44, 0x00]); // "AUD\x00"

const isSecureContext =
    window.isSecureContext ||
    location.protocol === 'https:' ||
    location.hostname === 'localhost' ||
    location.hostname === '127.0.0.1';

export default function ScreenShare() {
    const [sharing, setSharing] = useState(false);
    const [error, setError] = useState(null);
    const [hasAudio, setHasAudio] = useState(false);
    const streamRef = useRef(null);
    const wsRef = useRef(null);
    const timerRef = useRef(null);
    const canvasRef = useRef(null);
    const videoRef = useRef(null);
    const audioCtxRef = useRef(null);
    const audioProcessorRef = useRef(null);
    const cleaningUp = useRef(false);

    const cleanup = useCallback(() => {
        if (cleaningUp.current) return;
        cleaningUp.current = true;

        if (timerRef.current) {
            clearTimeout(timerRef.current);
            timerRef.current = null;
        }
        if (audioProcessorRef.current) {
            try { audioProcessorRef.current.disconnect(); } catch {}
            audioProcessorRef.current = null;
        }
        if (audioCtxRef.current) {
            try { audioCtxRef.current.close(); } catch {}
            audioCtxRef.current = null;
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
        setHasAudio(false);
        cleaningUp.current = false;
    }, []);

    const stopSharing = useCallback(async () => {
        if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            try {
                wsRef.current.send(JSON.stringify({ action: 'stop' }));
            } catch {}
        }
        cleanup();
        try { await api.stop(); } catch {}
    }, [cleanup]);

    useEffect(() => {
        return () => cleanup();
    }, [cleanup]);

    const startSharing = useCallback(async () => {
        setError(null);
        try {
            // Request screen capture — ask for audio too
            const stream = await navigator.mediaDevices.getDisplayMedia({
                video: { frameRate: { ideal: TARGET_FPS } },
                audio: true,
            });
            streamRef.current = stream;

            stream.getVideoTracks()[0].onended = () => {
                cleanup();
                api.stop().catch(() => {});
            };

            const video = document.createElement('video');
            video.srcObject = stream;
            video.muted = true;
            await video.play();
            videoRef.current = video;

            await new Promise((resolve) => {
                const check = () => {
                    if (video.videoWidth > 0) resolve();
                    else setTimeout(check, 50);
                };
                check();
            });

            const canvas = document.createElement('canvas');
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            canvasRef.current = canvas;

            await api.play({ mode: 'browser' });

            const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            const ws = new WebSocket(`${proto}//${window.location.host}/ws/screen-share`);
            wsRef.current = ws;

            ws.onopen = () => {
                setSharing(true);

                // --- Video capture loop ---
                const ctx = canvas.getContext('2d');
                let encoding = false;

                const captureFrame = () => {
                    if (!wsRef.current || ws.readyState !== WebSocket.OPEN) return;

                    timerRef.current = setTimeout(captureFrame, FRAME_INTERVAL);

                    if (encoding) return;
                    if (ws.bufferedAmount > 0) return;

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

                timerRef.current = setTimeout(captureFrame, 0);

                // --- Audio capture ---
                const audioTracks = stream.getAudioTracks();
                if (audioTracks.length > 0) {
                    try {
                        const audioCtx = new (window.AudioContext || window.webkitAudioContext)({
                            sampleRate: 48000,
                        });
                        audioCtxRef.current = audioCtx;

                        const audioStream = new MediaStream(audioTracks);
                        const source = audioCtx.createMediaStreamSource(audioStream);

                        // ScriptProcessorNode: 4096 buffer, 2 in, 2 out
                        const processor = audioCtx.createScriptProcessor(4096, 2, 2);
                        audioProcessorRef.current = processor;

                        processor.onaudioprocess = (e) => {
                            if (!wsRef.current || ws.readyState !== WebSocket.OPEN) return;

                            const left = e.inputBuffer.getChannelData(0);
                            const right = e.inputBuffer.getChannelData(1);
                            const numSamples = left.length;

                            // Interleave stereo: [L0, R0, L1, R1, ...]
                            const interleaved = new Float32Array(numSamples * 2);
                            for (let i = 0; i < numSamples; i++) {
                                interleaved[i * 2] = left[i];
                                interleaved[i * 2 + 1] = right[i];
                            }

                            // Prefix with AUDIO_MAGIC
                            const payload = new Uint8Array(
                                AUDIO_MAGIC.byteLength + interleaved.byteLength
                            );
                            payload.set(AUDIO_MAGIC, 0);
                            payload.set(
                                new Uint8Array(interleaved.buffer),
                                AUDIO_MAGIC.byteLength
                            );

                            ws.send(payload.buffer);
                        };

                        source.connect(processor);
                        processor.connect(audioCtx.destination);

                        setHasAudio(true);
                    } catch (audioErr) {
                        console.warn('[screen-share] audio capture failed:', audioErr);
                    }
                }
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
                return;
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
                    {hasAudio ? ' (with audio)' : ' (video only)'}
                </div>
            )}
        </div>
    );
}
