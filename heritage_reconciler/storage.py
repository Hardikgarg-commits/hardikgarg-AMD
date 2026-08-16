"""Local JSON persistence for the event log, state and audit trail.

No external database is used: everything lives in memory and is mirrored to
plain JSON files so the audit trail can be explored offline (CLI, dashboard or
any text editor).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .engine import ReconciliationEngine

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

EVENTS_FILE = "events.json"
STATE_FILE = "state.json"
AUDIT_FILE = "audit_trail.json"
TIMELINE_FILE = "timeline.json"


class JsonStore:
    """Writes engine outputs to a directory of JSON files."""

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.data_dir = Path(data_dir) if data_dir is not None else DEFAULT_DATA_DIR

    @property
    def events_path(self) -> Path:
        return self.data_dir / EVENTS_FILE

    @property
    def state_path(self) -> Path:
        return self.data_dir / STATE_FILE

    @property
    def audit_path(self) -> Path:
        return self.data_dir / AUDIT_FILE

    @property
    def timeline_path(self) -> Path:
        return self.data_dir / TIMELINE_FILE

    def persist(self, engine: ReconciliationEngine) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        _write_json(self.events_path, engine.event_log)
        _write_json(self.state_path, engine.state())
        _write_json(self.audit_path, engine.audit_trail())
        _write_json(self.timeline_path, engine.timeline())

    def load_into(self, engine: ReconciliationEngine) -> bool:
        """Rehydrate an engine from the persisted event log. Returns True when
        data was found (state and audit trail are rebuilt by replaying)."""
        if not self.events_path.exists():
            return False
        with self.events_path.open("r", encoding="utf-8") as handle:
            events = json.load(handle)
        if not events:
            return False
        engine.load_snapshot({"events": events})
        return True

    def clear(self) -> None:
        for path in (
            self.events_path,
            self.state_path,
            self.audit_path,
            self.timeline_path,
        ):
            if path.exists():
                path.unlink()

    def read_audit(self) -> list[dict[str, Any]]:
        if not self.audit_path.exists():
            return []
        with self.audit_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"objects": [], "object_count": 0, "event_count": 0}
        with self.state_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    def read_timeline(self) -> list[dict[str, Any]]:
        if not self.timeline_path.exists():
            return []
        with self.timeline_path.open("r", encoding="utf-8") as handle:
            return json.load(handle)


def _write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")
    os.replace(tmp, path)
