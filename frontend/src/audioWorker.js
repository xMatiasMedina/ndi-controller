// Dedicated AUDIO capture worker. Reads AudioData off the shared-audio track
// via MediaStreamTrackProcessor, downmixes to interleaved stereo float32,
// coalesces into 20 ms (960-frame) chunks, and sends them over the audio
// WebSocket. Runs on its OWN worker thread so it never competes with the
// video worker (that contention was dropping the video frame rate).

let aws = null;
let areader = null;
let running = false;
let acc = new Float32Array(0);
const CHUNK = 960 * 2; // 20 ms of interleaved stereo @ 48 kHz

function stop() {
    running = false;
    try { if (areader) areader.cancel(); } catch {}
    areader = null;
    try { if (aws && aws.readyState === WebSocket.OPEN) aws.close(); } catch {}
    aws = null;
}

self.onmessage = async (e) => {
    const m = e.data || {};
    if (m.type === 'stop') { stop(); return; }
    if (m.type !== 'start') return;
    const { audioReadable, audioWsUrl } = m;
    try {
        aws = new WebSocket(audioWsUrl);
        aws.binaryType = 'arraybuffer';
        await new Promise((resolve, reject) => {
            aws.onopen = resolve;
            aws.onerror = () => reject(new Error('audio WS open failed'));
        });
        areader = audioReadable.getReader();
        running = true;
        while (running) {
            const { value: adata, done } = await areader.read();
            if (done) break;
            if (!adata) continue;
            try {
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
                const merged = new Float32Array(acc.length + out.length);
                merged.set(acc);
                merged.set(out, acc.length);
                acc = merged;
                while (acc.length >= CHUNK) {
                    if (aws && aws.readyState === WebSocket.OPEN && aws.bufferedAmount < 1000000) {
                        aws.send(acc.slice(0, CHUNK).buffer);
                    }
                    acc = acc.slice(CHUNK);
                }
            } finally {
                adata.close();
            }
        }
    } catch (err) {
        self.postMessage({ type: 'audio-error', error: String((err && err.message) || err) });
    } finally {
        stop();
    }
};
