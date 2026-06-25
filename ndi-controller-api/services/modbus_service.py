"""
Modbus TCP relay controller for the physical screens.

Ported from the standalone Modbus project (Waveshare 8-channel relay board).
Channel N maps to coil N-1; state is read with read_coils(0, count=num_channels).

pymodbus is synchronous, so every board call is wrapped in run_in_executor to
keep the event loop free. A threading.Lock serialises access to the single
client, and we lazily reconnect (with a short cooldown) after any failure —
mirroring the proven pattern from the reference project.

Connection parameters (host/port/unit) come from Settings → modbus, so they
are editable from the web UI without touching code.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Dict, List, Optional

from services.settings_service import SettingsService

try:
    from pymodbus.client import ModbusTcpClient

    PYMODBUS_AVAILABLE = True
except ImportError:
    PYMODBUS_AVAILABLE = False


_TIMEOUT = 3              # socket timeout (seconds)
_RETRIES = 1             # pymodbus internal retries per call
_RECONNECT_COOLDOWN = 2  # min seconds between our own reconnect attempts


class ModbusService:
    def __init__(self, settings: SettingsService) -> None:
        self._settings = settings
        self._client = None
        self._lock = threading.Lock()
        self._connected = False
        self._last_reconnect = 0.0
        # Remember what the live client was built with, so a settings change
        # (host/port) forces a rebuild on the next call.
        self._cur_host: Optional[str] = None
        self._cur_port: Optional[int] = None

    # ------------------------------------------------------------- sync core
    # (these run inside run_in_executor; they assume self._lock is held)

    def _create_client(self, host: str, port: int) -> None:
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
        self._client = ModbusTcpClient(
            host, port=port, timeout=_TIMEOUT, retries=_RETRIES
        )
        self._cur_host, self._cur_port = host, port
        self._connected = False

    def _ensure_connected(self, host: str, port: int) -> bool:
        if (
            self._client is None
            or host != self._cur_host
            or port != self._cur_port
        ):
            self._create_client(host, port)

        if self._client.connected:
            self._connected = True
            return True

        now = time.monotonic()
        if now - self._last_reconnect < _RECONNECT_COOLDOWN:
            self._connected = False
            return False
        self._last_reconnect = now
        ok = self._client.connect()
        self._connected = ok
        return ok

    def _set_coil_sync(
        self, channel: int, state: bool, host: str, port: int, unit_id: int
    ) -> bool:
        with self._lock:
            if not self._ensure_connected(host, port):
                return False
            try:
                result = self._client.write_coil(
                    channel - 1, value=state, device_id=unit_id
                )
                if result.isError():
                    self._client.close()
                    self._connected = False
                    return False
                return True
            except Exception:
                self._client.close()
                self._connected = False
                return False

    def _read_coils_sync(
        self, host: str, port: int, unit_id: int, count: int
    ) -> Optional[List[bool]]:
        with self._lock:
            if not self._ensure_connected(host, port):
                return None
            try:
                result = self._client.read_coils(
                    address=0, count=count, device_id=unit_id
                )
                if result.isError():
                    self._client.close()
                    self._connected = False
                    return None
                self._connected = True
                return list(result.bits[:count])
            except Exception:
                self._client.close()
                self._connected = False
                return None

    # ----------------------------------------------------------- async API

    async def set_channels(self, channels: List[int], state: bool) -> bool:
        """Drive one or more channels to the given state. True if all succeed."""
        if not PYMODBUS_AVAILABLE or not channels:
            return False
        cfg = self._settings.get().modbus
        loop = asyncio.get_running_loop()
        ok_all = True
        for ch in channels:
            ok = await loop.run_in_executor(
                None, self._set_coil_sync, ch, state, cfg.host, cfg.port, cfg.unit_id
            )
            ok_all = ok_all and ok
        return ok_all

    async def set_all(self, state: bool) -> bool:
        """Drive every channel used by a configured group (the master switch)."""
        cfg = self._settings.get().modbus
        all_channels = sorted({ch for g in cfg.groups for ch in g.channels})
        return await self.set_channels(all_channels, state)

    async def read_states(self) -> dict:
        """Return connection status plus per-channel and per-group on/off state."""
        cfg = self._settings.get().modbus
        if not PYMODBUS_AVAILABLE:
            return {
                "connected": False,
                "available": False,
                "channels": {},
                "groups": [g.model_dump() | {"on": False} for g in cfg.groups],
                "all_on": False,
            }

        loop = asyncio.get_running_loop()
        bits = await loop.run_in_executor(
            None, self._read_coils_sync, cfg.host, cfg.port, cfg.unit_id, cfg.num_channels
        )
        connected = bits is not None

        channels: Dict[int, bool] = {}
        if bits is not None:
            channels = {i + 1: bool(bits[i]) for i in range(len(bits))}

        groups = []
        for g in cfg.groups:
            on = connected and all(channels.get(ch, False) for ch in g.channels)
            groups.append(
                {"id": g.id, "name": g.name, "channels": g.channels, "on": on}
            )

        all_used = sorted({ch for g in cfg.groups for ch in g.channels})
        all_on = connected and bool(all_used) and all(
            channels.get(ch, False) for ch in all_used
        )

        return {
            "connected": connected,
            "available": True,
            "channels": channels,
            "groups": groups,
            "all_on": all_on,
        }

    def is_connected(self) -> bool:
        return self._connected

    async def disconnect(self) -> None:
        if not PYMODBUS_AVAILABLE:
            return
        loop = asyncio.get_running_loop()

        def _close():
            with self._lock:
                if self._client is not None:
                    try:
                        self._client.close()
                    except Exception:
                        pass
                self._connected = False

        await loop.run_in_executor(None, _close)
