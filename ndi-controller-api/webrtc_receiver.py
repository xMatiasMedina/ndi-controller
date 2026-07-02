#!/usr/bin/env python3
"""
WHIP → VAAPI decode → NDI receiver.

Run as a SUBPROCESS by the FastAPI app, using the SYSTEM python3 (which has
PyGObject / `gi` — the app's venv does not). The app proxies the browser's WHIP
SDP offer here over local HTTP and returns the answer.

Video is decoded on the Intel iGPU (`vah264dec`) and published as two NDI
sources so OBS and Reaper consume them unchanged:
  - video → ndisink ndi-name=<video-name>   (e.g. "OBS Video")
  - audio → ndisink ndi-name=<audio-name>   (e.g. "Reaper Audio")

Each branch carries an adjustable delay queue right before its NDI sink so the
app can apply live sync offsets (compensate downstream OBS-vs-Reaper latency),
mirroring the file player's offsets. A delay of 0 is a passthrough (minimal
latency). Offsets can be set at startup (args) or live (POST /offset).

    python3 webrtc_receiver.py --video-name "OBS Video" --audio-name "Reaper Audio" --port 8089
"""
import argparse
import json
import re
import sys
import threading

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstWebRTC", "1.0")
gi.require_version("GstSdp", "1.0")
from gi.repository import GLib, Gst, GstSdp, GstWebRTC  # noqa: E402

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402

VIDEO_NAME = "OBS Video"
AUDIO_NAME = "Reaper Audio"

# Per-stream delay applied just before the NDI sink, in nanoseconds. 0 means
# disabled — the branch is a passthrough with minimal latency. Live streams can
# only be DELAYED (you cannot pull a frame from the future), so to make one
# stream appear earlier you delay the other.
VIDEO_DELAY_NS = 0
AUDIO_DELAY_NS = 0
DELAY_MAX_NS = 5_000_000_000  # 5s of buffering headroom (caps the max offset)

# The currently-active Session, so live offset changes can reach its queues.
CURRENT = None


def log(*a):
    print("[webrtc-recv]", *a, flush=True)


def boost_opus(sdp: str) -> str:
    """Rewrite the Opus fmtp so the browser encodes full-band STEREO at a high
    bitrate instead of the default mono ~mediumband (~6 kHz ceiling). Applied to
    the answer we hand back; opusdec on the receive side decodes whatever the
    browser actually sends."""
    m = re.search(r'a=rtpmap:(\d+)\s+opus/48000', sdp, re.IGNORECASE)
    if not m:
        return sdp
    pt = m.group(1)
    params = ("minptime=10;useinbandfec=1;usedtx=0;stereo=1;sprop-stereo=1;"
              "maxaveragebitrate=256000;maxplaybackrate=48000")
    fmtp = "a=fmtp:%s %s" % (pt, params)
    if re.search(r'a=fmtp:%s [^\r\n]*' % pt, sdp):
        return re.sub(r'a=fmtp:%s [^\r\n]*' % pt, fmtp, sdp)
    return re.sub(r'(a=rtpmap:%s opus/48000[^\r\n]*)' % pt,
                  lambda mo: mo.group(1) + "\r\n" + fmtp, sdp)


def _delay_ns(ms, on):
    """Effective delay in ns: 0 unless enabled; negatives clamped to 0."""
    if not on:
        return 0
    return min(max(0, int(ms)) * 1_000_000, DELAY_MAX_NS)


def set_delays(video_ns, audio_ns):
    """Update the per-stream delays and apply them to the live pipeline."""
    global VIDEO_DELAY_NS, AUDIO_DELAY_NS
    VIDEO_DELAY_NS = max(0, min(int(video_ns), DELAY_MAX_NS))
    AUDIO_DELAY_NS = max(0, min(int(audio_ns), DELAY_MAX_NS))
    sess = CURRENT
    if sess is not None:
        try:
            if sess.vdelay is not None:
                sess.vdelay.set_property("min-threshold-time", VIDEO_DELAY_NS)
            if sess.adelay is not None:
                sess.adelay.set_property("min-threshold-time", AUDIO_DELAY_NS)
        except Exception as e:
            log("WARNING: applying delays failed:", e)
    log("delays set video=%dms audio=%dms"
        % (VIDEO_DELAY_NS // 1_000_000, AUDIO_DELAY_NS // 1_000_000))


class Session:
    """One webrtcbin pipeline per WHIP ingest. Negotiation runs on the GLib
    main loop (canonical async chain); the HTTP thread waits on `done`."""

    def __init__(self, offer_sdp: str):
        global CURRENT
        self.offer_sdp = offer_sdp
        self.answer_sdp = None
        self.done = threading.Event()
        self.vdelay = None  # video delay queue (set when the branch is built)
        self.adelay = None  # audio delay queue

        self.pipe = Gst.Pipeline.new(None)
        self.webrtc = Gst.ElementFactory.make("webrtcbin", "recv")
        self.webrtc.set_property("bundle-policy", "max-bundle")
        self.webrtc.set_property("stun-server", "stun://stun.l.google.com:19302")
        self.pipe.add(self.webrtc)
        self.webrtc.connect("pad-added", self._on_pad_added)
        self.webrtc.connect("notify::ice-gathering-state", self._on_ice)

        bus = self.pipe.get_bus()
        bus.add_signal_watch()
        bus.connect("message::error", self._on_error)

        self.pipe.set_state(Gst.State.PLAYING)
        CURRENT = self
        GLib.idle_add(self._start)

    def _on_error(self, _bus, msg):
        err, dbg = msg.parse_error()
        log("PIPELINE ERROR:", err.message, "|", dbg)

    def _start(self):
        parsed = GstSdp.SDPMessage.new_from_text(self.offer_sdp)
        sdpmsg = parsed[1] if isinstance(parsed, tuple) else parsed
        if sdpmsg is None:
            log("ERROR: failed to parse offer SDP")
            self.done.set()
            return False
        log("offer parsed")
        offer = GstWebRTC.WebRTCSessionDescription.new(
            GstWebRTC.WebRTCSDPType.OFFER, sdpmsg
        )
        promise = Gst.Promise.new_with_change_func(self._on_remote_set, None)
        self.webrtc.emit("set-remote-description", offer, promise)
        return False  # one-shot idle

    def _on_remote_set(self, promise, _data):
        promise.wait()
        log("remote description set; creating answer")
        p = Gst.Promise.new_with_change_func(self._on_answer, None)
        self.webrtc.emit("create-answer", None, p)

    def _on_answer(self, promise, _data):
        promise.wait()
        reply = promise.get_reply()
        if reply is None:
            log("ERROR: create-answer returned no reply")
            self.done.set()
            return
        answer = reply.get_value("answer")
        if answer is None:
            log("ERROR: reply has no 'answer':", reply.to_string())
            self.done.set()
            return
        log("answer created; setting local description")
        p = Gst.Promise.new()
        self.webrtc.emit("set-local-description", answer, p)
        p.interrupt()

    def _on_ice(self, webrtc, _param):
        if (
            webrtc.get_property("ice-gathering-state")
            == GstWebRTC.WebRTCICEGatheringState.COMPLETE
        ):
            local = webrtc.get_property("local-description")
            if local is not None:
                self.answer_sdp = boost_opus(local.sdp.as_text())
                log("ICE complete; answer ready (opus: stereo/high-bitrate)")
            else:
                log("ERROR: ICE complete but no local description")
            self.done.set()

    def _on_pad_added(self, _webrtc, pad):
        if pad.direction != Gst.PadDirection.SRC:
            return
        caps = pad.get_current_caps() or pad.query_caps(None)
        s = caps.to_string() if caps else ""
        # An adjustable delay queue sits right before the NDI sink. With
        # min-threshold-time=0 it's a passthrough; a positive value delays the
        # stream by that much (held buffer), which is the sync offset.
        delay_q = (
            "queue name=%s max-size-time=%d max-size-buffers=0 "
            "max-size-bytes=0 min-threshold-time=%d"
        )
        kind = None
        if "H264" in s:
            desc = (
                "queue ! rtph264depay ! h264parse ! vah264dec ! videoconvert ! "
                + (delay_q % ("voffdelay", DELAY_MAX_NS, VIDEO_DELAY_NS))
                + ' ! ndisink ndi-name="%s" sync=false' % VIDEO_NAME
            )
            kind = "video"
            log("video → NDI", VIDEO_NAME, "delay=%dms" % (VIDEO_DELAY_NS // 1_000_000))
        elif "OPUS" in s or "opus" in s:
            # The teltek ndisink CANNOT publish an audio-only NDI source (fed
            # audio alone it emits a bogus tiny video with no audio). Audio must
            # go through ndisinkcombiner, which requires a video pad — so we feed
            # it a tiny black dummy video plus the real audio (F32LE, as the
            # combiner's audio pad requires). The result is a proper NDI source
            # carrying the audio for Reaper. Verified receivable over NDI.
            desc = (
                "queue ! rtpopusdepay ! opusdec ! audioconvert ! audioresample "
                "! audio/x-raw,format=F32LE ! "
                + (delay_q % ("aoffdelay", DELAY_MAX_NS, AUDIO_DELAY_NS))
                + ' ! comb.audio'
                + ' ndisinkcombiner name=comb ! ndisink ndi-name="%s" sync=false' % AUDIO_NAME
                + ' videotestsrc is-live=true pattern=black'
                + ' ! video/x-raw,width=160,height=90,framerate=5/1 ! videoconvert ! comb.video'
            )
            kind = "audio"
            log("audio → NDI", AUDIO_NAME, "delay=%dms" % (AUDIO_DELAY_NS // 1_000_000))
        else:
            desc = "queue ! fakesink sync=false"
        branch = Gst.parse_bin_from_description(desc, True)
        self.pipe.add(branch)
        branch.sync_state_with_parent()
        pad.link(branch.get_static_pad("sink"))
        if kind == "video":
            self.vdelay = branch.get_by_name("voffdelay")
        elif kind == "audio":
            self.adelay = branch.get_by_name("aoffdelay")


class WhipHandler(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "content-type")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_POST(self):
        if self.path.rstrip("/").endswith("/offset"):
            return self._handle_offset()
        return self._handle_whip()

    def _handle_offset(self):
        length = int(self.headers.get("content-length", 0))
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            d = json.loads(raw)
        except Exception:
            d = {}
        v = _delay_ns(d.get("video_ms", 0), d.get("video_on", False))
        a = _delay_ns(d.get("audio_ms", 0), d.get("audio_on", False))
        set_delays(v, a)
        self.send_response(200)
        self.send_header("content-length", "0")
        self._cors()
        self.end_headers()

    def _handle_whip(self):
        length = int(self.headers.get("content-length", 0))
        offer = self.rfile.read(length).decode("utf-8")
        log("WHIP offer (%d bytes)" % length)
        sess = Session(offer)
        sess.done.wait(timeout=12)
        answer = sess.answer_sdp
        if not answer:
            log("no answer produced")
            self.send_response(500)
            self._cors()
            self.end_headers()
            return
        body = answer.encode("utf-8")
        self.send_response(201)
        self.send_header("content-type", "application/sdp")
        self.send_header("location", "/whip")
        self._cors()
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main():
    global VIDEO_NAME, AUDIO_NAME
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-name", default=VIDEO_NAME)
    ap.add_argument("--audio-name", default=AUDIO_NAME)
    ap.add_argument("--port", type=int, default=8089)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--video-offset-ms", type=int, default=0)
    ap.add_argument("--audio-offset-ms", type=int, default=0)
    ap.add_argument("--video-offset-on", action="store_true")
    ap.add_argument("--audio-offset-on", action="store_true")
    args = ap.parse_args()
    VIDEO_NAME, AUDIO_NAME = args.video_name, args.audio_name

    Gst.init(None)
    log("GStreamer", Gst.version_string())
    for el in ("vah264dec", "ndisink", "nicesrc"):
        if not Gst.ElementFactory.make(el, None):
            log("WARNING: element missing:", el)

    # Seed initial offsets (re-applied each time a share starts the receiver).
    set_delays(_delay_ns(args.video_offset_ms, args.video_offset_on),
               _delay_ns(args.audio_offset_ms, args.audio_offset_on))

    srv = ThreadingHTTPServer((args.host, args.port), WhipHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log("WHIP on http://%s:%d/whip  video=%r audio=%r"
        % (args.host, args.port, VIDEO_NAME, AUDIO_NAME))

    try:
        GLib.MainLoop().run()
    except KeyboardInterrupt:
        sys.exit(0)


if __name__ == "__main__":
    main()
