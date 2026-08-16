"""Pydantic models for telemetry ingestion and API responses."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .geometry import normalize_bbox

BBOX_PRECISION = 4


def canonical_timestamp(value: datetime) -> str:
    """Serialize a datetime to a canonical UTC ISO-8601 string."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


class DetectionEvent(BaseModel):
    """A single detection reported by one drone."""

    model_config = ConfigDict(populate_by_name=True)

    drone_id: str = Field(min_length=1)
    timestamp: datetime
    bbox: list[float] = Field(min_length=4, max_length=4)
    class_label: str = Field(min_length=1, alias="class")
    confidence: float = Field(ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("bbox")
    @classmethod
    def _validate_bbox(cls, bbox: list[float]) -> list[float]:
        x_min, y_min, x_max, y_max = bbox
        if x_max <= x_min or y_max <= y_min:
            raise ValueError("bbox must satisfy x_max > x_min and y_max > y_min")
        return [float(v) for v in bbox]

    @property
    def normalized_bbox(self) -> tuple[float, float, float, float]:
        return normalize_bbox(self.bbox)

    @property
    def iso_timestamp(self) -> str:
        return canonical_timestamp(self.timestamp)

    @property
    def bbox_key(self) -> tuple[float, ...]:
        return tuple(round(v, BBOX_PRECISION) for v in self.normalized_bbox)

    @property
    def dedup_key(self) -> str:
        """Idempotency key: ``drone_id + timestamp + bbox``."""
        bbox = ",".join(f"{v:.{BBOX_PRECISION}f}" for v in self.bbox_key)
        return f"{self.drone_id}|{self.iso_timestamp}|{bbox}"

    @property
    def event_id(self) -> str:
        digest = hashlib.sha1(self.dedup_key.encode("utf-8")).hexdigest()
        return f"evt_{digest[:16]}"

    def to_record(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "drone_id": self.drone_id,
            "timestamp": self.iso_timestamp,
            "bbox": list(self.normalized_bbox),
            "class": self.class_label,
            "confidence": self.confidence,
            "metadata": dict(self.metadata),
        }


class ReplayRequest(BaseModel):
    """Replay payload.

    ``events`` may be omitted to replay the events already stored in the log.
    ``time_offset_seconds`` shifts every replayed event in time, which allows
    replaying a historical capture into an arbitrary time window.
    ``reset_state`` (default ``True``) rebuilds the state from scratch so a
    replay of the same events is fully reproducible.
    """

    events: list[DetectionEvent] | None = None
    time_offset_seconds: float = 0.0
    reset_state: bool = True


class DroneReliability(BaseModel):
    drone_id: str = Field(min_length=1)
    reliability: float = Field(ge=0.0, le=1.0)


class ReliabilityConfigUpdate(BaseModel):
    drones: list[DroneReliability] = Field(default_factory=list)
    default_reliability: float | None = Field(default=None, ge=0.0, le=1.0)
