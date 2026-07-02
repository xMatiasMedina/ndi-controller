"""
ScreenShareService — runs the GStreamer WHIP→VAAPI→NDI receiver as a subprocess
and proxies WHIP signaling to it.

The receiver needs system PyGObject/GStreamer, which the app's venv lacks, so it
runs as a separate `python3 webrtc_receiver.py` process. The browser POSTs its
WHIP offer to the app (same HTTPS origin, no mixed content); the app forwards it
here over local HTTP and hands the answer back.

All methods are blocking (subprocess + urllib) — call them via run_in_executor.
"""
from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

from services.settings_service import SettingsService

_RECEIVER = Path(__file__).resolve().parent.parent / "webrtc_receiver.py"
_PORT = 8089
_HOST = "127.0.0.1"


class ScreenShareService:
    def __init__(self, settings: SettingsService) -> None:
        self._settings = settings
        self._proc: Optional[subprocess.Popen] = None
        # Live sync offsets, remembered so they survive a re-share (each share
        # spawns a fresh receiver). Applied as args on start + live via /offset.
        self._offset = {
            "video_ms": 0, "audio_ms": 0,
            "video_on": False, "audio_on": False,
        }

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    @staticmethod
    def _python() -> str:
        # The receiver needs SYSTEM python3 (PyGObject). The systemd unit's PATH
        # puts the venv first, so we must bypass it with an absolute path.
        if sys.platform == "win32":
            return "python"
        for p in ("/usr/bin/python3", "/usr/local/bin/python3"):
            if Path(p).exists():
                return p
        return "python3"

    def start(self) -> dict:
        if self.is_running():
            return {"ok": True, "already": True}
        ndi = self._settings.get().ndi
        cmd = [
            self._python(), str(_RECEIVER),
            "--video-name", ndi.video_source_name,
            "--audio-name", ndi.audio_source_name,
            "--port", str(_PORT), "--host", _HOST,
            "--video-offset-ms", str(int(self._offset["video_ms"])),
            "--audio-offset-ms", str(int(self._offset["audio_ms"])),
        ]
        if self._offset["video_on"]:
            cmd.append("--video-offset-on")
        if self._offset["audio_on"]:
            cmd.append("--audio-offset-on")
        try:
            self._proc = subprocess.Popen(cmd)
        except Exception as e:
            return {"ok": False, "error": f"failed to spawn receiver: {e}"}

        # Wait for the receiver's WHIP port to come up.
        deadline = time.time() + 8.0
        while time.time() < deadline:
            if self._proc.poll() is not None:
                return {"ok": False, "error": "receiver exited during startup"}
            with socket.socket() as s:
                s.settimeout(0.3)
                if s.connect_ex((_HOST, _PORT)) == 0:
                    return {"ok": True}
            time.sleep(0.2)
        self.stop()
        return {"ok": False, "error": "receiver did not open its WHIP port"}

    def forward_whip(self, offer_sdp: str) -> Optional[str]:
        req = urllib.request.Request(
            f"http://{_HOST}:{_PORT}/whip",
            data=offer_sdp.encode("utf-8"),
            headers={"content-type": "application/sdp"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=12) as resp:
            return resp.read().decode("utf-8")

    def set_offset(self, video_ms: int, audio_ms: int,
                   video_on: bool, audio_on: bool) -> dict:
        """Remember the live sync offsets and, if a share is running, push them
        to the receiver so they take effect immediately. If no share is active
        they're stored and applied when the next share starts."""
        self._offset = {
            "video_ms": int(video_ms), "audio_ms": int(audio_ms),
            "video_on": bool(video_on), "audio_on": bool(audio_on),
        }
        if not self.is_running():
            return {"ok": True, "applied": False}
        try:
            req = urllib.request.Request(
                f"http://{_HOST}:{_PORT}/offset",
                data=json.dumps(self._offset).encode("utf-8"),
                headers={"content-type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=3) as resp:
                resp.read()
            return {"ok": True, "applied": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def stop(self) -> dict:
        if self._proc is not None:
            try:
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=3)
                except Exception:
                    self._proc.kill()
            except Exception:
                pass
            self._proc = None
        return {"ok": True}
