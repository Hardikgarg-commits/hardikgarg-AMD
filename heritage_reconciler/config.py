"""Configuration for spatial association and deterministic conflict resolution."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "drones.json"

#: Order in which tie-breaking rules are applied. The list is part of the
#: deterministic contract of the engine: rules are evaluated left to right and
#: the first one that discriminates two candidate events wins.
DEFAULT_RULE_ORDER: tuple[str, ...] = (
    "higher_confidence",
    "newer_timestamp",
    "higher_drone_reliability",
    "higher_historical_class_frequency",
    "lexicographic_event_id",
)


@dataclass
class ReconcilerConfig:
    """Tunable knobs of the reconciliation engine."""

    #: IoU above which two detections are considered the same physical object.
    iou_threshold: float = 0.7
    #: Per-drone reliability signal used as a tie-breaker.
    drone_reliability: dict[str, float] = field(default_factory=dict)
    #: Reliability applied to drones missing from ``drone_reliability``.
    default_reliability: float = 0.5
    #: Number of decimals used when comparing confidences (keeps ties explicit
    #: and floating point noise from silently deciding a conflict).
    confidence_precision: int = 6
    #: Only detections within this many seconds of each other compete for the
    #: resolved class. ``None`` means the whole track history competes.
    conflict_window_seconds: float | None = None
    rule_order: tuple[str, ...] = DEFAULT_RULE_ORDER
    #: When true, a second event with the same ``drone_id`` + ``timestamp`` is
    #: rejected as a duplicate even if its bbox differs. When false (default) a
    #: drone may report several objects in the same frame.
    strict_duplicate_drone_timestamp: bool = False

    def reliability_of(self, drone_id: str) -> float:
        return self.drone_reliability.get(drone_id, self.default_reliability)

    def to_dict(self) -> dict[str, Any]:
        return {
            "iou_threshold": self.iou_threshold,
            "drone_reliability": dict(sorted(self.drone_reliability.items())),
            "default_reliability": self.default_reliability,
            "confidence_precision": self.confidence_precision,
            "conflict_window_seconds": self.conflict_window_seconds,
            "rule_order": list(self.rule_order),
            "strict_duplicate_drone_timestamp": self.strict_duplicate_drone_timestamp,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ReconcilerConfig:
        known = {
            "iou_threshold",
            "drone_reliability",
            "default_reliability",
            "confidence_precision",
            "conflict_window_seconds",
            "strict_duplicate_drone_timestamp",
        }
        kwargs = {key: payload[key] for key in known if key in payload}
        if "rule_order" in payload:
            kwargs["rule_order"] = tuple(payload["rule_order"])
        return cls(**kwargs)

    @classmethod
    def load(cls, path: Path | str | None = None) -> ReconcilerConfig:
        config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        if not config_path.exists():
            return cls()
        with config_path.open("r", encoding="utf-8") as handle:
            return cls.from_dict(json.load(handle))

    def save(self, path: Path | str | None = None) -> Path:
        config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with config_path.open("w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        return config_path
