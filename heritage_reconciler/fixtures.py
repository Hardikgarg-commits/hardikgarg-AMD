"""Helpers for loading the JSON fixture datasets shipped with the repository."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import DetectionEvent

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def list_fixtures() -> list[str]:
    return sorted(path.stem for path in FIXTURES_DIR.glob("*.json"))


def fixture_path(name: str) -> Path:
    path = FIXTURES_DIR / (name if name.endswith(".json") else f"{name}.json")
    if not path.exists():
        raise FileNotFoundError(f"fixture not found: {path}")
    return path


def load_fixture_raw(name: str) -> dict[str, Any]:
    with fixture_path(name).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        return {"name": name, "description": "", "events": payload}
    return payload


def load_fixture(name: str) -> list[DetectionEvent]:
    payload = load_fixture_raw(name)
    return [DetectionEvent(**event) for event in payload["events"]]
