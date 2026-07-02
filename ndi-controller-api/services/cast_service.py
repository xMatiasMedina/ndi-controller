"""
Cast service — pushes the /remote dashboard onto a Google Nest Hub (or any
Chromecast display) via DashCast, and keeps it up without the periodic reload
that plagues naive "continuous casting" setups.

Anti-flicker, two layers:
  1. State-aware keep-alive (here): poll the device's running app and only
     re-cast when it has actually left DashCast — never on a blind timer. This
     removes the reload-every-N-seconds flicker.
  2. Page-side (frontend): /remote embeds a hidden looping muted video so the
     Cast media session never reaches the ~10-minute idle timeout in the first
     place (the same trick ha-catt-fix uses).

catt/pychromecast are synchronous; every device call runs in an executor so the
event loop stays free. A single global cast session, guarded by a lock.
"""
from __future__ import annotations

import asyncio
import threading
from typing import Optional

import dev_config

try:
    from catt.discovery import get_cast_with_ip
    from pychromecast.controllers.dashcast import DashCastController

    try:
        from pychromecast.config import APP_DASHCAST
    except Exception:
        APP_DASHCAST = "5FE44367"

    CAST_AVAILABLE = True
except ImportError:
    CAST_AVAILABLE = False
    APP_DASHCAST = "5FE44367"


class CastService:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cast = None              # pychromecast Chromecast
        self._controller = None        # DashCastController
        self._ip: Optional[str] = None
        self._url: Optional[str] = None
        self._casting = False
        self._keepalive_task: Optional[asyncio.Task] = None

    # --------------------------------------------------- sync (run in executor)
    def _do_cast_locked(self, ip: str, url: str) -> None:
        """Connect (if needed) and (re)load the dashboard URL via DashCast.
        Caller must hold self._lock."""
        if self._cast is None:
            cast = get_cast_with_ip(ip)        # connects + waits, or None
            if cast is None:
                raise RuntimeError(f"Nest Hub not found at {ip}")
            controller = DashCastController()
            cast.register_handler(controller)
            self._cast = cast
            self._controller = controller
        # force=True also loads pages that block iframe embedding.
        self._controller.load_url(url, force=True)

    def _cast_sync(self, ip: str, url: str) -> None:
        with self._lock:
            self._do_cast_locked(ip, url)

    def _tick_sync(self) -> None:
        """Keep-alive check: re-cast only if the device has left DashCast."""
        with self._lock:
            if self._cast is None or self._url is None:
                return
            try:
                app = self._cast.app_id
            except Exception:
                app = None
            if app == APP_DASHCAST:
                return  # dashboard still up — do nothing, no reload
            # Reverted to idle (or connection lost) — restore the dashboard.
            try:
                self._do_cast_locked(self._ip, self._url)
            except Exception as e:
                # Drop the likely-dead connection so the next tick reconnects.
                self._cast = None
                self._controller = None
                print(f"[cast] keep-alive re-cast failed: {e}")

    def _quit_sync(self) -> None:
        with self._lock:
            cast = self._cast
            self._cast = None
            self._controller = None
            if cast is not None:
                try:
                    cast.quit_app()
                except Exception:
                    pass
                try:
                    cast.disconnect()
                except Exception:
                    pass

    # ----------------------------------------------------------- async public
    async def start(self, ip: str, url: str) -> dict:
        if not CAST_AVAILABLE:
            return {"ok": False, "error": "casting support (catt) not installed"}

        await self.stop()  # tear down any existing session first

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(None, self._cast_sync, ip, url)
        except Exception as e:
            await loop.run_in_executor(None, self._quit_sync)
            return {"ok": False, "error": str(e)}

        self._ip = ip
        self._url = url
        self._casting = True

        try:
            cfg = dev_config.get()
            cfg.cast.last_ip = ip
            dev_config.save(cfg)
        except Exception:
            pass

        self._keepalive_task = asyncio.ensure_future(self._keepalive_loop())
        print(f"[cast] casting {url} -> {ip}")
        return {"ok": True, "ip": ip, "url": url}

    async def stop(self) -> dict:
        self._casting = False
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._quit_sync)
        self._ip = None
        self._url = None
        return {"ok": True}

    def status(self) -> dict:
        return {
            "available": CAST_AVAILABLE,
            "casting": self._casting,
            "ip": self._ip,
            "url": self._url,
        }

    async def _keepalive_loop(self) -> None:
        interval = max(10, dev_config.get().cast.check_interval_sec)
        loop = asyncio.get_running_loop()
        while self._casting:
            await asyncio.sleep(interval)
            if not self._casting:
                break
            try:
                await loop.run_in_executor(None, self._tick_sync)
            except Exception as e:
                print(f"[cast] keep-alive error: {e}")
