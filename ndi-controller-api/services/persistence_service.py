"""
Atomic JSON persistence.

Atomic = write to a sibling temp file then ``rename()``, which is atomic on
POSIX and on Windows since 3.3. A crash mid-write leaves the old file intact
instead of producing a half-written one. The Java reference project used
JAXB and didn't do this — easy way to lose your config.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from core.interfaces import IPersistence


class JsonPersistenceService(IPersistence):
    def load_json(self, path) -> Optional[dict]:
        path = Path(path)
        if not path.exists():
            return None
        try:
            with path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[persistence] failed to load {path}: {e}")
            return None

    def save_json(self, path, data: dict) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Write to a sibling temp file then atomically rename
        fd, tmp_path = tempfile.mkstemp(
            prefix=path.name + ".", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, path)
        except Exception:
            # Cleanup the partial temp file on failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
