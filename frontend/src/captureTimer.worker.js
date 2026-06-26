// Posts a "tick" at the requested interval to drive the screen-capture loop.
// A Web Worker timer is NOT clamped to ~1 fps the way a page-thread setTimeout
// is when the tab is backgrounded, so capture keeps running at the full frame
// rate even when the operator is looking at the NDI output instead of this
// controller page.
let timerId = null;

self.onmessage = (e) => {
    const msg = e.data || {};
    if (msg.type === 'start') {
        const interval = Math.max(1, msg.interval || 33);
        if (timerId !== null) clearInterval(timerId);
        timerId = setInterval(() => self.postMessage('tick'), interval);
    } else if (msg.type === 'stop') {
        if (timerId !== null) {
            clearInterval(timerId);
            timerId = null;
        }
    }
};