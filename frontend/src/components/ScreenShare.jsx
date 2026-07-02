import { useRef, useState, useCallback, useEffect } from 'react';
import { api } from '../api.js';

/**
 * ScreenShare — captures the screen via getDisplayMedia and sends it to the
 * station over WebRTC (WHIP, H.264 + Opus). The station decodes the video on
 * the Intel iGPU (VAAPI) and republishes it as NDI. This replaces the old
 * JPEG-over-WebSocket path (which encoded on the main thread and choked /
 * froze on minimize). WebRTC encodes on the GPU, off the main thread.
 */

const isSecureContext =
    window.isSecureContext ||
    location.protocol === 'https:' ||
    location.hostname === 'localhost' ||
    location.hostname === '127.0.0.1';

export default function ScreenShare() {
    const [sharing, setSharing] = useState(false);
    const [error, setError] = useState(null);
    const [hasAudio, setHasAudio] = useState(false);
    const pcRef = useRef(null);
    const streamRef = useRef(null);
    const cleaningUp = useRef(false);

    const cleanup = useCallback(() => {
        if (cleaningUp.current) return;
        cleaningUp.current = true;
        if (pcRef.current) {
            try { pcRef.current.close(); } catch {}
            pcRef.current = null;
        }
        if (streamRef.current) {
            streamRef.current.getTracks().forEach((t) => t.stop());
            streamRef.current = null;
        }
        setSharing(false);
        setHasAudio(false);
        cleaningUp.current = false;
    }, []);

    const stopSharing = useCallback(async () => {
        cleanup();
        try { await api.screenShareStop(); } catch {}
    }, [cleanup]);

    useEffect(() => () => cleanup(), [cleanup]);

    const startSharing = useCallback(async () => {
        setError(null);
        try {
            const stream = await navigator.mediaDevices.getDisplayMedia({
                video: { frameRate: { ideal: 30 } },
                audio: true,
            });
            streamRef.current = stream;

            const videoTrack = stream.getVideoTracks()[0];
            videoTrack.contentHint = 'detail';
            videoTrack.onended = () => stopSharing();

            const pc = new RTCPeerConnection();
            pcRef.current = pc;

            // Video: prefer H.264 so the station decodes it with vah264dec (VAAPI).
            const vtx = pc.addTransceiver(videoTrack, { direction: 'sendonly' });
            try {
                const caps = RTCRtpSender.getCapabilities('video');
                const h264 = caps.codecs.filter((c) => c.mimeType === 'video/H264');
                if (h264.length && vtx.setCodecPreferences) vtx.setCodecPreferences(h264);
            } catch {}

            const audioTrack = stream.getAudioTracks()[0];
            if (audioTrack) {
                pc.addTransceiver(audioTrack, { direction: 'sendonly' });
                setHasAudio(true);
            }

            pc.onconnectionstatechange = () => {
                const s = pc.connectionState;
                if (s === 'failed' || s === 'closed' || s === 'disconnected') {
                    stopSharing();
                }
            };

            await pc.setLocalDescription(await pc.createOffer());
            // Non-trickle WHIP: wait for ICE gathering, then send the full offer.
            await new Promise((resolve) => {
                if (pc.iceGatheringState === 'complete') return resolve();
                const check = () => {
                    if (pc.iceGatheringState === 'complete') {
                        pc.removeEventListener('icegatheringstatechange', check);
                        resolve();
                    }
                };
                pc.addEventListener('icegatheringstatechange', check);
                setTimeout(resolve, 3000);
            });

            const answer = await api.screenShareWhip(pc.localDescription.sdp);
            await pc.setRemoteDescription({ type: 'answer', sdp: answer });
            setSharing(true);
        } catch (e) {
            if (e.name === 'NotAllowedError') return;
            console.error('[screen-share] start failed:', e);
            setError(e.message);
            cleanup();
        }
    }, [cleanup, stopSharing]);

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
                <button className="screen-share-btn stop" onClick={stopSharing}>
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
                    Streaming to NDI via WebRTC{hasAudio ? ' (with audio)' : ' (video only)'}
                </div>
            )}
        </div>
    );
}
