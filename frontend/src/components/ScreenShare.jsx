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

// Force high-quality stereo Opus in an SDP. Default WebRTC Opus is mono and
// low-bitrate (~mediumband, ~6 kHz ceiling); rewriting the opus fmtp to stereo
// + a high maxaveragebitrate makes Chrome encode full-band stereo. The station
// WHIP receiver echoes the same params in its answer so both sides agree.
function boostOpus(sdp) {
    if (!sdp) return sdp;
    const m = sdp.match(/a=rtpmap:(\d+)\s+opus\/48000/i);
    if (!m) return sdp;
    const pt = m[1];
    const params =
        'minptime=10;useinbandfec=1;usedtx=0;stereo=1;sprop-stereo=1;' +
        'maxaveragebitrate=256000;maxplaybackrate=48000';
    const fmtp = new RegExp('a=fmtp:' + pt + ' [^\\r\\n]*');
    if (fmtp.test(sdp)) return sdp.replace(fmtp, 'a=fmtp:' + pt + ' ' + params);
    return sdp.replace(
        new RegExp('(a=rtpmap:' + pt + ' opus/48000[^\\r\\n]*)'),
        '$1\r\na=fmtp:' + pt + ' ' + params
    );
}

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
                audio: {
                    // Music, not voice: disable Chrome's voice DSP (AGC pumps
                    // the level; NS/EC eat the highs) and ask for stereo.
                    autoGainControl: false,
                    noiseSuppression: false,
                    echoCancellation: false,
                    channelCount: 2,
                },
            });
            streamRef.current = stream;

            const videoTrack = stream.getVideoTracks()[0];
            // Smooth motion: prioritise frame rate over per-frame detail so
            // moving content doesn't stutter (drops resolution, not frames).
            videoTrack.contentHint = 'motion';
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
            // Under CPU/bandwidth pressure, drop resolution rather than frame
            // rate — keeps the video smooth (no stutter) on the wall.
            try {
                const p = vtx.sender.getParameters();
                if (!p.encodings || !p.encodings.length) p.encodings = [{}];
                p.degradationPreference = 'maintain-framerate';
                await vtx.sender.setParameters(p);
            } catch (e) {
                console.warn('[screen-share] video setParameters failed:', e);
            }

            const audioTrack = stream.getAudioTracks()[0];
            if (audioTrack) {
                const atx = pc.addTransceiver(audioTrack, { direction: 'sendonly' });
                // Lift the encoder's bitrate cap (default throttles to ~mono
                // mediumband). Stereo + full-band comes from the Opus fmtp in
                // the offer (boostOpus) and the receiver's matching answer.
                try {
                    const p = atx.sender.getParameters();
                    if (!p.encodings || !p.encodings.length) p.encodings = [{}];
                    p.encodings[0].maxBitrate = 320_000;
                    await atx.sender.setParameters(p);
                } catch (e) {
                    console.warn('[screen-share] audio setParameters failed:', e);
                }
                setHasAudio(true);
            }

            pc.onconnectionstatechange = () => {
                const s = pc.connectionState;
                if (s === 'failed' || s === 'closed' || s === 'disconnected') {
                    stopSharing();
                }
            };

            const offer = await pc.createOffer();
            offer.sdp = boostOpus(offer.sdp);
            await pc.setLocalDescription(offer);
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
