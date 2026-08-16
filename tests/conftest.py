from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from heritage_reconciler.config import ReconcilerConfig
from heritage_reconciler.engine import ReconciliationEngine
from heritage_reconciler.models import DetectionEvent

RELIABILITY = {
    "drone_001": 0.95,
    "drone_002": 0.9,
    "drone_003": 0.85,
    "drone_004": 0.8,
    "drone_005": 0.7,
    "drone_006": 0.6,
    "drone_007": 0.55,
    "drone_008": 0.5,
    "drone_009": 0.45,
}


@pytest.fixture
def config() -> ReconcilerConfig:
    return ReconcilerConfig(drone_reliability=dict(RELIABILITY))


@pytest.fixture
def engine(config: ReconcilerConfig) -> ReconciliationEngine:
    return ReconciliationEngine(config)


def make_event(
    drone_id: str = "drone_001",
    timestamp: str = "2025-04-05T12:30:00Z",
    bbox: list[float] | None = None,
    class_label: str = "archaeological_site",
    confidence: float = 0.87,
    **metadata: object,
) -> DetectionEvent:
    return DetectionEvent(
        drone_id=drone_id,
        timestamp=timestamp,
        bbox=bbox or [100.0, 100.0, 200.0, 200.0],
        **{"class": class_label},
        confidence=confidence,
        metadata=dict(metadata),
    )


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from heritage_reconciler import api

    api.engine = ReconciliationEngine(
        ReconcilerConfig(drone_reliability=dict(RELIABILITY))
    )
    api.store = api.JsonStore(tmp_path / "data")
    monkeypatch.setattr(api, "PERSIST", True)
    with TestClient(api.app) as client:
        yield client
