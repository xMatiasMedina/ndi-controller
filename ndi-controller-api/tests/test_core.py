"""Tests for core modules — persistence, event bus, models."""
from __future__ import annotations

import asyncio
import json
import tempfile
import threading
from pathlib import Path

import pytest

from core.events import EventBus
from core.models import PlayerState, PlaybackStatus, Settings, VideoAsset
from services.persistence_service import JsonPersistenceService


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        svc = JsonPersistenceService()
        path = tmp_path / "test.json"
        data = {"key": "value", "number": 42}

        svc.save_json(path, data)
        loaded = svc.load_json(path)
        assert loaded == data

    def test_load_nonexistent_returns_none(self, tmp_path):
        svc = JsonPersistenceService()
        assert svc.load_json(tmp_path / "nope.json") is None

    def test_load_corrupt_json_returns_none(self, tmp_path):
        svc = JsonPersistenceService()
        path = tmp_path / "bad.json"
        path.write_text("not valid json {{{", encoding="utf-8")
        assert svc.load_json(path) is None

    def test_atomic_write_creates_parent_dirs(self, tmp_path):
        svc = JsonPersistenceService()
        path = tmp_path / "sub" / "dir" / "file.json"
        svc.save_json(path, {"ok": True})
        assert svc.load_json(path) == {"ok": True}

    def test_concurrent_writes_dont_corrupt(self, tmp_path):
        """Multiple threads writing to the same file shouldn't produce corrupt JSON."""
        svc = JsonPersistenceService()
        path = tmp_path / "concurrent.json"
        errors = []

        def writer(i):
            try:
                for _ in range(20):
                    svc.save_json(path, {"writer": i, "data": list(range(100))})
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        loaded = svc.load_json(path)
        assert loaded is not None
        assert "writer" in loaded


class TestEventBus:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        bus = EventBus()
        received = []

        async def handler(event, payload):
            received.append((event, payload))

        bus.subscribe("test.event", handler)
        await bus.publish("test.event", {"msg": "hello"})

        assert len(received) == 1
        assert received[0] == ("test.event", {"msg": "hello"})

    @pytest.mark.asyncio
    async def test_unsubscribe(self):
        bus = EventBus()
        received = []

        async def handler(event, payload):
            received.append(payload)

        bus.subscribe("test.event", handler)
        bus.unsubscribe("test.event", handler)
        await bus.publish("test.event", "should not appear")

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_bad_handler_doesnt_crash_publisher(self):
        bus = EventBus()
        good_received = []

        async def bad_handler(event, payload):
            raise RuntimeError("boom")

        async def good_handler(event, payload):
            good_received.append(payload)

        bus.subscribe("test.event", bad_handler)
        bus.subscribe("test.event", good_handler)
        await bus.publish("test.event", "data")

        assert good_received == ["data"]


class TestModels:
    def test_video_asset_gets_uuid(self):
        v1 = VideoAsset(name="a.mp4", path="/a.mp4")
        v2 = VideoAsset(name="b.mp4", path="/b.mp4")
        assert v1.id != v2.id
        assert len(v1.id) > 10

    def test_player_state_defaults(self):
        state = PlayerState()
        assert state.status == PlaybackStatus.STOPPED
        assert state.position_seconds == 0.0
        assert state.muted is False

    def test_settings_defaults(self):
        s = Settings()
        assert s.obs.port == 4455
        assert s.reaper.lockstep is True
        assert s.ndi.video_source_name == "OBS Video"
