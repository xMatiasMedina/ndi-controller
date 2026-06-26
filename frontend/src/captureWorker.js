// Encodes display-capture VIDEO frames and forwards captured AUDIO to the NDI
// controller, driven by MediaStreamTrackProcessor (the media pipeline) rather
// than JS timers. Runs in a Worker, so Chrome background-tab throttling does
// not apply: both stay at full rate when the controller tab is hidden.

let ws = null;
let reader = null;
let aws = null;
let areader = null;
let running = false;
let canvas = null;
let ctx = null;

function cleanup() {
    running = false;
    try { if (reader) reader.cancel(); } catch {}
    reader = null;
    try { if (areader) areader.cancel(); } catch {}
    areader = null;
    try { if (ws && ws.readyState === WebSocket.OPEN) ws.close(); } catch {}
    ws = null;
    try { if (aws && aws.readyState === WebSocket.OPEN) aws.close(); } catch {}
    aws = null;
}

async function runAudio(audioReadable, audioWsUrl, maxBuffer) {
    aws = new WebSocket(audioWsUrl);
    aws.binaryType = 'arraybuffer';
    await new Promise((resolve, reject) => {
        aws.onopen = resolve;
        aws.onerror = () => reject(new Error('audio WS open failed'));
    });
    areader = audioReadable.getReader();
    while (running) {
        const { value: adata, done } = await areader.read();
        if (done) break;
        if (!adata) continue;
        try {
            if (!aws || aws.readyState !== WebSocket.OPEN) continue;
            if (aws.bufferedAmount > (maxBuffer || 524288)) continue;
            const frames = adata.numberOfFrames;
            const channels = adata.numberOfChannels || 1;
            const out = new Float32Array(frames * 2);
            if (adata.format && adata.format.indexOf('planar') !== -1) {
                const c0 = new Float32Array(frames);
                adata.copyTo(c0, { planeIndex: 0 });
                let c1 = c0;
                if (channels > 1) {
                    c1 = new Float32Array(frames);
                    adata.copyTo(c1, { planeIndex: 1 });
                }
                for (let i = 0; i < frames; i++) { out[2 * i] = c0[i]; out[2 * i + 1] = c1[i]; }
            } else {
                const inter = new Float32Array(frames * channels);
                adata.copyTo(inter, { planeIndex: 0 });
                for (let i = 0; i < frames; i++) {
                    const l = inter[i * channels];
                    const r = channels > 1 ? inter[i * channels + 1] : l;
                    out[2 * i] = l; out[2 * i + 1] = r;
                }
            }
            aws.send(out.buffer);
        } finally {
            adata.close();
        }
    }
}

self.onmessage = async (e) => {
    const msg = e.data || {};
    if (msg.type === 'stop') {
        try { if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ action: 'stop' })); } catch {}
        cleanup();
        self.postMessage({ type: 'stopped' });
        return;
    }
    if (msg.type !== 'start') return;
    const { readable, audioReadable, wsUrl, audioWsUrl, maxWidth, quality, maxBuffer } = msg;
    running = true;
    if (audioReadable && audioWsUrl) {
        runAudio(audioReadable, audioWsUrl, maxBuffer).catch((err) =>
            self.postMessage({ type: 'audio-error', error: String((err && err.message) || err) }));
    }
    try {
        ws = new WebSocket(wsUrl);
        ws.binaryType = 'arraybuffer';
        ws.onclose = () => { running = false; };
        await new Promise((resolve, reject) => {
            ws.onopen = resolve;
            ws.onerror = () => reject(new Error('WebSocket open failed'));
        });
        self.postMessage({ type: 'open' });
        reader = readable.getReader();
        while (running) {
            const { value: frame, done } = await reader.read();
            if (done) break;
            if (!frame) continue;
            try {
                if (!ws || ws.readyState !== WebSocket.OPEN) continue;
                if (ws.bufferedAmount > maxBuffer) continue;
                const fw = frame.displayWidth || frame.codedWidth;
                const fh = frame.displayHeight || frame.codedHeight;
                const scale = Math.min(1, maxWidth / fw);
                const w = Math.max(2, Math.round(fw * scale));
                const h = Math.max(2, Math.round(fh * scale));
                if (!canvas || canvas.width !== w || canvas.height !== h) {
                    canvas = new OffscreenCanvas(w, h);
                    ctx = canvas.getContext('2d');
                }
                ctx.drawImage(frame, 0, 0, w, h);
                const blob = await canvas.convertToBlob({ type: 'image/jpeg', quality });
                const buf = await blob.arrayBuffer();
                if (ws && ws.readyState === WebSocket.OPEN) ws.send(buf);
            } finally {
                frame.close();
            }
        }
    } catch (err) {
        self.postMessage({ type: 'error', error: String((err && err.message) || err) });
    } finally {
        cleanup();
        self.postMessage({ type: 'stopped' });
    }
};
