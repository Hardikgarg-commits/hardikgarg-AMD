"""Deterministic reconciliation engine for multi-drone heritage detections.

The engine keeps an append-only event log and derives all state from it. Every
observable output (global state, audit trail, timeline) is a pure function of
the *canonically ordered* event log, so the same set of events always produces
the same state and the same audit trail regardless of the order in which the
events arrived over the wire.

Canonical order is ``(timestamp, drone_id, event_id)``. When an event arrives
late (its canonical key sorts before the last processed key), the derived state
is rebuilt from the log instead of being patched, which keeps late-event
handling identical to a full replay.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .config import ReconcilerConfig
from .geometry import BBox, iou, normalize_bbox
from .models import DetectionEvent, canonical_timestamp

RULE_DESCRIPTIONS: dict[str, str] = {
    "initial_detection": "First detection of this object; no competing events.",
    "unanimous_class": "All competing detections agreed on the class label.",
    "higher_confidence": "Chose the detection with the higher confidence score.",
    "newer_timestamp": "Confidences tied; chose the newer detection.",
    "higher_drone_reliability": (
        "Confidence and timestamp tied; chose the more reliable drone."
    ),
    "higher_historical_class_frequency": (
        "Confidence, timestamp and drone reliability tied; chose the class seen "
        "most often in this object's history."
    ),
    "lexicographic_event_id": (
        "All configured rules tied; broke the tie on event_id to stay deterministic."
    ),
}


class DuplicateEventError(Exception):
    """Raised when an event with an already-known idempotency key is ingested."""

    def __init__(self, event_id: str, dedup_key: str, reason: str) -> None:
        super().__init__(f"duplicate event: {dedup_key}")
        self.event_id = event_id
        self.dedup_key = dedup_key
        self.reason = reason


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _canonical_key(record: dict[str, Any]) -> tuple[str, str, str]:
    return (record["timestamp"], record["drone_id"], record["event_id"])


def _object_id(record: dict[str, Any]) -> str:
    bbox = ",".join(f"{v:.4f}" for v in record["bbox"])
    seed = f"{record['drone_id']}|{record['timestamp']}|{bbox}"
    return f"obj_{hashlib.sha1(seed.encode('utf-8')).hexdigest()[:12]}"


@dataclass
class HeritageObject:
    """A reconciled heritage object (a track built from multiple detections)."""

    object_id: str
    created_by_event: str
    first_seen: str
    last_seen: str
    tracking_bbox: BBox
    resolved_class: str
    resolved_confidence: float
    resolved_by_event: str
    events: list[dict[str, Any]] = field(default_factory=list)
    class_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def drone_ids(self) -> list[str]:
        return sorted({event["drone_id"] for event in self.events})

    @property
    def class_frequency(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for event in self.events:
            counts[event["class"]] = counts.get(event["class"], 0) + 1
        return dict(sorted(counts.items()))

    def snapshot(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "class": self.resolved_class,
            "confidence": self.resolved_confidence,
            "bbox": [round(v, 4) for v in self.tracking_bbox],
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "resolved_by_event": self.resolved_by_event,
            "created_by_event": self.created_by_event,
            "drone_ids": self.drone_ids,
            "detection_count": len(self.events),
            "class_frequency": self.class_frequency,
        }

    def detail(self) -> dict[str, Any]:
        payload = self.snapshot()
        payload["events"] = list(self.events)
        payload["class_history"] = list(self.class_history)
        return payload


class ReconciliationEngine:
    """Ingests detections and maintains reconciled state plus an audit trail."""

    def __init__(self, config: ReconcilerConfig | None = None) -> None:
        self.config = config or ReconcilerConfig()
        self._log: list[dict[str, Any]] = []
        self._dedup_keys: dict[str, str] = {}
        self._drone_timestamp_keys: dict[str, str] = {}
        self._objects: dict[str, HeritageObject] = {}
        self._audit: list[dict[str, Any]] = []
        self._timeline: list[dict[str, Any]] = []
        self._late_arrivals: list[dict[str, Any]] = []
        self._decision_seq = 0

    # ------------------------------------------------------------------ log

    @property
    def event_log(self) -> list[dict[str, Any]]:
        return list(self._log)

    def reset(self, *, keep_log: bool = False) -> None:
        """Drop derived state; optionally keep the raw event log."""
        log = list(self._log) if keep_log else []
        self._log = log
        self._dedup_keys = {rec["dedup_key"]: rec["event_id"] for rec in log}
        self._drone_timestamp_keys = {
            f"{rec['drone_id']}|{rec['timestamp']}": rec["event_id"] for rec in log
        }
        self._objects = {}
        self._audit = []
        self._timeline = []
        self._late_arrivals = []
        self._decision_seq = 0

    def ingest(self, event: DetectionEvent) -> dict[str, Any]:
        """Ingest one detection. Raises :class:`DuplicateEventError` on replays
        of an identical (``drone_id``, ``timestamp``, ``bbox``) triple."""
        record = event.to_record()
        record["dedup_key"] = event.dedup_key
        drone_ts_key = f"{record['drone_id']}|{record['timestamp']}"

        if record["dedup_key"] in self._dedup_keys:
            raise DuplicateEventError(
                record["event_id"],
                record["dedup_key"],
                "same drone_id + timestamp + bbox already ingested",
            )
        if (
            self.config.strict_duplicate_drone_timestamp
            and drone_ts_key in self._drone_timestamp_keys
        ):
            raise DuplicateEventError(
                record["event_id"],
                drone_ts_key,
                "same drone_id + timestamp already ingested",
            )

        last_key = _canonical_key(self._log[-1]) if self._log else None
        is_late = last_key is not None and _canonical_key(record) < last_key
        self._log.append(record)
        self._dedup_keys[record["dedup_key"]] = record["event_id"]
        self._drone_timestamp_keys.setdefault(drone_ts_key, record["event_id"])

        if is_late:
            # Out-of-order arrival: re-derive everything in canonical order so the
            # outcome is identical to a clean replay of the same events. Arrival
            # order is recorded separately to keep the audit trail deterministic.
            self._late_arrivals.append(
                {
                    "event_id": record["event_id"],
                    "event_timestamp": record["timestamp"],
                    "arrived_after_timestamp": last_key[0],
                }
            )
            self._log.sort(key=_canonical_key)
            self._rebuild()
            decision = next(
                entry
                for entry in reversed(self._audit)
                if entry["trigger_event"]["event_id"] == record["event_id"]
            )
        else:
            decision = self._apply(record)

        return {
            "event_id": record["event_id"],
            "late_event": bool(is_late),
            "decision": decision,
        }

    def ingest_many(self, events: Iterable[DetectionEvent]) -> list[dict[str, Any]]:
        results = []
        for event in events:
            try:
                results.append(self.ingest(event))
            except DuplicateEventError as exc:
                results.append(
                    {
                        "event_id": exc.event_id,
                        "duplicate": True,
                        "reason": exc.reason,
                        "decision": None,
                    }
                )
        return results

    def replay(
        self,
        events: Iterable[DetectionEvent] | None = None,
        *,
        time_offset_seconds: float = 0.0,
        reset_state: bool = True,
    ) -> dict[str, Any]:
        """Replay events in canonical order.

        With ``events=None`` the stored log is replayed, which reproduces the
        current state and audit trail from scratch. ``time_offset_seconds``
        shifts every replayed event, allowing a historical capture to be
        replayed into an arbitrary time window.
        """
        if events is None:
            source = [dict(record) for record in self._log]
            if time_offset_seconds:
                source = [
                    _shift_record(record, time_offset_seconds) for record in source
                ]
            self.reset()
        else:
            source = []
            for event in events:
                record = event.to_record()
                record["dedup_key"] = event.dedup_key
                if time_offset_seconds:
                    record = _shift_record(record, time_offset_seconds)
                source.append(record)
            if reset_state:
                self.reset()

        replayed = 0
        duplicates = 0
        for record in sorted(source, key=_canonical_key):
            if record["dedup_key"] in self._dedup_keys:
                duplicates += 1
                continue
            self._log.append(record)
            self._dedup_keys[record["dedup_key"]] = record["event_id"]
            self._drone_timestamp_keys.setdefault(
                f"{record['drone_id']}|{record['timestamp']}", record["event_id"]
            )
            replayed += 1

        self._log.sort(key=_canonical_key)
        self._rebuild()
        return {
            "replayed_events": replayed,
            "skipped_duplicates": duplicates,
            "total_events": len(self._log),
            "objects": len(self._objects),
            "audit_entries": len(self._audit),
            "time_offset_seconds": time_offset_seconds,
            "state_digest": self.state_digest(),
        }

    # -------------------------------------------------------------- derived

    def _rebuild(self) -> None:
        self._objects = {}
        self._audit = []
        self._timeline = []
        self._decision_seq = 0
        for record in self._log:
            self._apply(record)

    def _apply(self, record: dict[str, Any]) -> dict[str, Any]:
        matched, match_iou = self._match_object(record)
        self._decision_seq += 1
        decision_id = f"dec_{self._decision_seq:06d}"

        if matched is None:
            obj = HeritageObject(
                object_id=_object_id(record),
                created_by_event=record["event_id"],
                first_seen=record["timestamp"],
                last_seen=record["timestamp"],
                tracking_bbox=normalize_bbox(record["bbox"]),
                resolved_class=record["class"],
                resolved_confidence=record["confidence"],
                resolved_by_event=record["event_id"],
                events=[_public_event(record)],
            )
            obj.class_history.append(
                {
                    "class": record["class"],
                    "since": record["timestamp"],
                    "decision_id": decision_id,
                }
            )
            self._objects[obj.object_id] = obj
            entry = self._audit_entry(
                decision_id=decision_id,
                obj=obj,
                record=record,
                state_before=None,
                considered=[_public_event(record)],
                conflicts=[],
                rule="initial_detection",
                evaluations=[],
                match_iou=None,
            )
            self._append_timeline(entry, obj, kind="object_created")
            return entry

        obj = matched
        state_before = obj.snapshot()
        obj.events.append(_public_event(record))
        obj.events.sort(key=lambda e: (e["timestamp"], e["drone_id"], e["event_id"]))
        obj.first_seen = obj.events[0]["timestamp"]
        obj.last_seen = obj.events[-1]["timestamp"]
        obj.tracking_bbox = normalize_bbox(obj.events[-1]["bbox"])

        considered = self._competing_events(obj)
        winner, rule, evaluations = self._resolve(obj, considered)
        conflicts = self._conflicts(obj, considered, winner)

        previous_class = obj.resolved_class
        obj.resolved_class = winner["class"]
        obj.resolved_confidence = winner["confidence"]
        obj.resolved_by_event = winner["event_id"]
        if winner["class"] != previous_class:
            obj.class_history.append(
                {
                    "class": winner["class"],
                    "since": record["timestamp"],
                    "decision_id": decision_id,
                    "previous_class": previous_class,
                }
            )

        entry = self._audit_entry(
            decision_id=decision_id,
            obj=obj,
            record=record,
            state_before=state_before,
            considered=considered,
            conflicts=conflicts,
            rule=rule,
            evaluations=evaluations,
            match_iou=match_iou,
        )
        self._append_timeline(
            entry,
            obj,
            kind="class_changed"
            if winner["class"] != previous_class
            else "observation",
        )
        return entry

    def _match_object(
        self, record: dict[str, Any]
    ) -> tuple[HeritageObject | None, float | None]:
        bbox = normalize_bbox(record["bbox"])
        best: tuple[float, str] | None = None
        for object_id, obj in self._objects.items():
            overlap = iou(bbox, obj.tracking_bbox)
            if overlap > self.config.iou_threshold:
                candidate = (-overlap, object_id)
                if best is None or candidate < best:
                    best = candidate
        if best is None:
            return None, None
        return self._objects[best[1]], -best[0]

    def _competing_events(self, obj: HeritageObject) -> list[dict[str, Any]]:
        window = self.config.conflict_window_seconds
        if window is None:
            return list(obj.events)
        latest = _parse_timestamp(obj.events[-1]["timestamp"])
        cutoff = latest - timedelta(seconds=window)
        return [
            event
            for event in obj.events
            if _parse_timestamp(event["timestamp"]) >= cutoff
        ]

    def _resolve(
        self, obj: HeritageObject, considered: list[dict[str, Any]]
    ) -> tuple[dict[str, Any], str, list[dict[str, Any]]]:
        frequency = obj.class_frequency
        ranked = sorted(
            considered, key=lambda event: self._rule_key(event, frequency), reverse=True
        )
        winner = ranked[0]
        if len({event["class"] for event in considered}) == 1:
            return winner, "unanimous_class", []

        runner_up = next(
            (event for event in ranked[1:] if event["class"] != winner["class"]), None
        )
        evaluations: list[dict[str, Any]] = []
        deciding_rule = self.config.rule_order[-1]
        if runner_up is not None:
            for rule in self.config.rule_order:
                win_value = self._rule_value(rule, winner, frequency)
                lose_value = self._rule_value(rule, runner_up, frequency)
                evaluations.append(
                    {
                        "rule": rule,
                        "winner_value": win_value,
                        "challenger_value": lose_value,
                        "decisive": win_value != lose_value,
                    }
                )
                if win_value != lose_value:
                    deciding_rule = rule
                    break
        return winner, deciding_rule, evaluations

    def _rule_key(
        self, event: dict[str, Any], frequency: dict[str, int]
    ) -> tuple[Any, ...]:
        return tuple(
            self._rule_value(rule, event, frequency) for rule in self.config.rule_order
        )

    def _rule_value(
        self, rule: str, event: dict[str, Any], frequency: dict[str, int]
    ) -> Any:
        if rule == "higher_confidence":
            return round(event["confidence"], self.config.confidence_precision)
        if rule == "newer_timestamp":
            return event["timestamp"]
        if rule == "higher_drone_reliability":
            return self.config.reliability_of(event["drone_id"])
        if rule == "higher_historical_class_frequency":
            return frequency.get(event["class"], 0)
        if rule == "lexicographic_event_id":
            return event["event_id"]
        raise ValueError(f"unknown resolution rule: {rule}")

    def _conflicts(
        self,
        obj: HeritageObject,
        considered: list[dict[str, Any]],
        winner: dict[str, Any],
    ) -> list[dict[str, Any]]:
        conflicts: list[dict[str, Any]] = []
        drones = sorted({event["drone_id"] for event in considered})
        classes = sorted({event["class"] for event in considered})
        if len(drones) > 1:
            conflicts.append({"type": "multi_drone_same_object", "drone_ids": drones})
        if len(classes) > 1:
            conflicts.append({"type": "conflicting_class_labels", "classes": classes})
        if winner["class"] != obj.resolved_class:
            conflicts.append(
                {
                    "type": "class_change_over_time",
                    "from": obj.resolved_class,
                    "to": winner["class"],
                }
            )
            reverted = [
                entry["class"]
                for entry in obj.class_history[:-1]
                if entry["class"] == winner["class"]
            ]
            if reverted:
                conflicts.append(
                    {
                        "type": "class_reverted",
                        "class": winner["class"],
                        "previous_assignments": len(reverted),
                    }
                )
        return conflicts

    def _audit_entry(
        self,
        *,
        decision_id: str,
        obj: HeritageObject,
        record: dict[str, Any],
        state_before: dict[str, Any] | None,
        considered: list[dict[str, Any]],
        conflicts: list[dict[str, Any]],
        rule: str,
        evaluations: list[dict[str, Any]],
        match_iou: float | None,
    ) -> dict[str, Any]:
        entry = {
            "decision_id": decision_id,
            "sequence": self._decision_seq,
            "object_id": obj.object_id,
            "decision_timestamp": record["timestamp"],
            "trigger_event": _public_event(record),
            "match_iou": None if match_iou is None else round(match_iou, 4),
            "input_events_considered": [dict(event) for event in considered],
            "conflicts": conflicts,
            "resolution_rule": rule,
            "rule_order": list(self.config.rule_order),
            "rule_evaluations": evaluations,
            "final_class": obj.resolved_class,
            "final_confidence": obj.resolved_confidence,
            "state_before": state_before,
            "state_after": obj.snapshot(),
            "explanation": self._explain(obj, record, conflicts, rule, considered),
        }
        self._audit.append(entry)
        return entry

    def _explain(
        self,
        obj: HeritageObject,
        record: dict[str, Any],
        conflicts: list[dict[str, Any]],
        rule: str,
        considered: list[dict[str, Any]],
    ) -> str:
        conflict_names = ", ".join(conflict["type"] for conflict in conflicts) or "none"
        return (
            f"Event {record['event_id']} from {record['drone_id']} "
            f"({record['class']} @ {record['confidence']:.3f}) was associated with "
            f"{obj.object_id}. Conflicts: {conflict_names}. "
            f"{len(considered)} competing detection(s) considered; rule '{rule}' "
            f"applied ({RULE_DESCRIPTIONS.get(rule, rule)}) resulting in class "
            f"'{obj.resolved_class}' at confidence {obj.resolved_confidence:.3f}."
        )

    def _append_timeline(
        self, entry: dict[str, Any], obj: HeritageObject, *, kind: str
    ) -> None:
        self._timeline.append(
            {
                "sequence": entry["sequence"],
                "decision_id": entry["decision_id"],
                "timestamp": entry["decision_timestamp"],
                "object_id": obj.object_id,
                "event": kind,
                "class": obj.resolved_class,
                "confidence": obj.resolved_confidence,
                "drone_id": entry["trigger_event"]["drone_id"],
                "conflicts": [conflict["type"] for conflict in entry["conflicts"]],
                "resolution_rule": entry["resolution_rule"],
            }
        )

    # --------------------------------------------------------------- output

    def state(self) -> dict[str, Any]:
        objects = [
            self._objects[object_id].snapshot() for object_id in sorted(self._objects)
        ]
        return {
            "objects": objects,
            "object_count": len(objects),
            "event_count": len(self._log),
            "decision_count": len(self._audit),
            "last_event_timestamp": self._log[-1]["timestamp"] if self._log else None,
            "config": self.config.to_dict(),
            "state_digest": self.state_digest(),
        }

    def object_detail(self, object_id: str) -> dict[str, Any] | None:
        obj = self._objects.get(object_id)
        return obj.detail() if obj else None

    def audit_trail(
        self, *, object_id: str | None = None, conflicts_only: bool = False
    ) -> list[dict[str, Any]]:
        entries = self._audit
        if object_id:
            entries = [e for e in entries if e["object_id"] == object_id]
        if conflicts_only:
            entries = [e for e in entries if e["conflicts"]]
        return [dict(entry) for entry in entries]

    def timeline(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._timeline]

    def late_arrivals(self) -> list[dict[str, Any]]:
        """Events that arrived out of canonical order and forced a re-derivation.

        Arrival order is deliberately kept out of the audit trail so that the
        trail stays byte-identical to a clean replay.
        """
        return [dict(item) for item in self._late_arrivals]

    def state_digest(self) -> str:
        """Stable digest of the reconciled state, handy for determinism checks."""
        payload = "|".join(
            f"{obj['object_id']}:{obj['class']}:{obj['confidence']:.6f}:"
            f"{obj['first_seen']}:{obj['last_seen']}:{obj['detection_count']}"
            for obj in (
                self._objects[object_id].snapshot()
                for object_id in sorted(self._objects)
            )
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def audit_digest(self) -> str:
        payload = "|".join(
            f"{entry['decision_id']}:{entry['object_id']}:{entry['resolution_rule']}:"
            f"{entry['final_class']}:{entry['decision_timestamp']}"
            for entry in self._audit
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def snapshot(self) -> dict[str, Any]:
        return {
            "config": self.config.to_dict(),
            "events": self.event_log,
            "state": self.state(),
            "audit_trail": self.audit_trail(),
            "timeline": self.timeline(),
            "late_arrivals": self.late_arrivals(),
            "audit_digest": self.audit_digest(),
        }

    def load_snapshot(self, payload: dict[str, Any]) -> None:
        """Restore from a persisted snapshot by replaying its event log."""
        if "config" in payload:
            self.config = ReconcilerConfig.from_dict(payload["config"])
        self.reset()
        for record in sorted(payload.get("events", []), key=_canonical_key):
            record = dict(record)
            record.setdefault(
                "dedup_key",
                _dedup_key_of(record),
            )
            if record["dedup_key"] in self._dedup_keys:
                continue
            self._log.append(record)
            self._dedup_keys[record["dedup_key"]] = record["event_id"]
            self._drone_timestamp_keys.setdefault(
                f"{record['drone_id']}|{record['timestamp']}", record["event_id"]
            )
        self._rebuild()


def _public_event(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "event_id": record["event_id"],
        "drone_id": record["drone_id"],
        "timestamp": record["timestamp"],
        "bbox": [round(v, 4) for v in record["bbox"]],
        "class": record["class"],
        "confidence": record["confidence"],
        "metadata": dict(record.get("metadata", {})),
    }


def _dedup_key_of(record: dict[str, Any]) -> str:
    bbox = ",".join(f"{round(v, 4):.4f}" for v in record["bbox"])
    return f"{record['drone_id']}|{record['timestamp']}|{bbox}"


def _shift_record(record: dict[str, Any], offset_seconds: float) -> dict[str, Any]:
    shifted = dict(record)
    moved = _parse_timestamp(record["timestamp"]) + timedelta(seconds=offset_seconds)
    shifted["timestamp"] = canonical_timestamp(moved)
    shifted["event_id"] = _event_id_of(shifted)
    shifted["dedup_key"] = _dedup_key_of(shifted)
    return shifted


def _event_id_of(record: dict[str, Any]) -> str:
    digest = hashlib.sha1(_dedup_key_of(record).encode("utf-8")).hexdigest()
    return f"evt_{digest[:16]}"
