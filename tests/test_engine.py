"""Engine-level tests: association, conflicts, determinism, replay."""

from __future__ import annotations

import itertools
import random
import time

import pytest

from heritage_reconciler.config import ReconcilerConfig
from heritage_reconciler.engine import DuplicateEventError, ReconciliationEngine
from tests.conftest import make_event


def test_duplicate_event_is_rejected_and_leaves_state_unchanged(engine):
    engine.ingest(make_event())
    state_before = engine.state()

    with pytest.raises(DuplicateEventError):
        engine.ingest(make_event())

    assert engine.state() == state_before
    assert len(engine.event_log) == 1
    assert len(engine.audit_trail()) == 1


def test_duplicate_ignores_class_and_confidence_changes(engine):
    """Idempotency key is drone_id + timestamp + bbox only."""
    engine.ingest(make_event(confidence=0.87, class_label="archaeological_site"))
    with pytest.raises(DuplicateEventError):
        engine.ingest(make_event(confidence=0.99, class_label="temple_complex"))
    assert engine.state()["objects"][0]["class"] == "archaeological_site"


def test_same_drone_and_timestamp_different_bbox_is_not_duplicate(engine):
    engine.ingest(make_event(bbox=[0, 0, 100, 100]))
    engine.ingest(make_event(bbox=[500, 500, 600, 600]))
    assert engine.state()["object_count"] == 2


def test_strict_duplicate_mode_rejects_same_drone_and_timestamp(config):
    config.strict_duplicate_drone_timestamp = True
    engine = ReconciliationEngine(config)
    engine.ingest(make_event(bbox=[0, 0, 100, 100]))
    with pytest.raises(DuplicateEventError):
        engine.ingest(make_event(bbox=[500, 500, 600, 600]))


def test_overlapping_detections_merge_into_single_object(engine):
    engine.ingest(make_event(drone_id="drone_001", bbox=[100, 100, 200, 200]))
    engine.ingest(
        make_event(
            drone_id="drone_002",
            timestamp="2025-04-05T12:30:05Z",
            bbox=[104, 98, 201, 199],
        )
    )
    state = engine.state()
    assert state["object_count"] == 1
    assert state["objects"][0]["drone_ids"] == ["drone_001", "drone_002"]
    assert state["objects"][0]["detection_count"] == 2


def test_low_overlap_detections_stay_separate(engine):
    engine.ingest(make_event(bbox=[0, 0, 100, 100]))
    engine.ingest(
        make_event(
            drone_id="drone_002",
            timestamp="2025-04-05T12:30:05Z",
            bbox=[50, 50, 150, 150],
        )
    )
    assert engine.state()["object_count"] == 2


def test_conflicting_class_labels_resolved_by_confidence(engine):
    engine.ingest(make_event(class_label="cave_painting", confidence=0.72))
    result = engine.ingest(
        make_event(
            drone_id="drone_009",
            timestamp="2025-04-05T12:30:05Z",
            bbox=[102, 101, 199, 198],
            class_label="stone_inscription",
            confidence=0.88,
        )
    )
    decision = result["decision"]
    assert decision["final_class"] == "stone_inscription"
    assert decision["resolution_rule"] == "higher_confidence"
    conflict_types = {c["type"] for c in decision["conflicts"]}
    assert {"conflicting_class_labels", "multi_drone_same_object"} <= conflict_types


def test_tie_broken_by_newer_timestamp(engine):
    engine.ingest(make_event(class_label="cave_painting", confidence=0.8))
    result = engine.ingest(
        make_event(
            drone_id="drone_009",
            timestamp="2025-04-05T12:30:05Z",
            bbox=[101, 100, 200, 201],
            class_label="stone_inscription",
            confidence=0.8,
        )
    )
    assert result["decision"]["resolution_rule"] == "newer_timestamp"
    assert result["decision"]["final_class"] == "stone_inscription"


def test_tie_broken_by_drone_reliability(engine):
    """Same timestamp and confidence: the more reliable drone wins."""
    engine.ingest(
        make_event(drone_id="drone_001", class_label="fort_wall", confidence=0.8)
    )
    result = engine.ingest(
        make_event(
            drone_id="drone_009",
            bbox=[101, 99, 199, 201],
            class_label="burial_mound",
            confidence=0.8,
        )
    )
    assert result["decision"]["resolution_rule"] == "higher_drone_reliability"
    assert result["decision"]["final_class"] == "fort_wall"


def test_tie_broken_by_historical_class_frequency(engine):
    for index, class_label in enumerate(["rock_shelter", "rock_shelter"]):
        engine.ingest(
            make_event(
                drone_id="drone_005",
                timestamp=f"2025-04-05T12:30:0{index}Z",
                bbox=[100 + index, 100, 200, 200 + index],
                class_label=class_label,
                confidence=0.75,
            )
        )
    engine.ingest(
        make_event(
            drone_id="drone_005",
            timestamp="2025-04-05T12:30:02Z",
            bbox=[101, 101, 201, 201],
            class_label="petroglyph",
            confidence=0.75,
        )
    )
    engine.ingest(
        make_event(
            drone_id="drone_005",
            timestamp="2025-04-05T12:30:02Z",
            bbox=[99, 100, 201, 200],
            class_label="rock_shelter",
            confidence=0.75,
        )
    )
    result = {"decision": engine.audit_trail()[-1]}
    assert result["decision"]["resolution_rule"] == "higher_historical_class_frequency"
    assert result["decision"]["final_class"] == "rock_shelter"


def test_late_event_is_flagged_and_state_matches_clean_replay(engine):
    newer = make_event(
        drone_id="drone_002",
        timestamp="2025-04-05T12:31:00Z",
        class_label="rock_shelter",
        confidence=0.55,
    )
    older = make_event(
        drone_id="drone_002",
        timestamp="2025-04-05T12:30:30Z",
        bbox=[102, 98, 201, 199],
        class_label="temple_complex",
        confidence=0.93,
    )
    engine.ingest(newer)
    result = engine.ingest(older)

    assert result["late_event"] is True
    assert engine.state()["objects"][0]["class"] == "temple_complex"

    ordered = ReconciliationEngine(engine.config)
    ordered.ingest(older)
    ordered.ingest(newer)
    assert ordered.state_digest() == engine.state_digest()
    assert ordered.audit_digest() == engine.audit_digest()


def test_class_change_and_revert_are_recorded(engine):
    engine.ingest(
        make_event(drone_id="drone_007", class_label="temple_complex", confidence=0.70)
    )
    engine.ingest(
        make_event(
            drone_id="drone_008",
            timestamp="2025-04-05T12:30:10Z",
            bbox=[102, 101, 201, 199],
            class_label="mosque_ruin",
            confidence=0.82,
        )
    )
    result = engine.ingest(
        make_event(
            drone_id="drone_007",
            timestamp="2025-04-05T12:30:20Z",
            bbox=[101, 99, 200, 201],
            class_label="temple_complex",
            confidence=0.94,
        )
    )
    conflict_types = [c["type"] for c in result["decision"]["conflicts"]]
    assert "class_change_over_time" in conflict_types
    assert "class_reverted" in conflict_types

    object_id = result["decision"]["object_id"]
    history = [
        entry["class"] for entry in engine.object_detail(object_id)["class_history"]
    ]
    assert history == ["temple_complex", "mosque_ruin", "temple_complex"]


def test_state_is_independent_of_arrival_order(config):
    events = [
        make_event(
            drone_id=f"drone_00{index % 3 + 1}",
            timestamp=f"2025-04-05T12:3{index}:00Z",
            bbox=[100 + index, 100, 200 + index, 200],
            class_label=["temple_complex", "mosque_ruin", "fort_wall"][index % 3],
            confidence=0.6 + index / 100,
        )
        for index in range(6)
    ]
    digests = set()
    for permutation in itertools.islice(itertools.permutations(events), 12):
        engine = ReconciliationEngine(ReconcilerConfig.from_dict(config.to_dict()))
        for event in permutation:
            engine.ingest(event)
        digests.add((engine.state_digest(), engine.audit_digest()))
    assert len(digests) == 1


def test_replay_reproduces_state_and_audit_trail(engine):
    events = [
        make_event(
            drone_id="drone_003",
            timestamp=f"2025-04-05T12:30:0{index}Z",
            bbox=[100, 100 + index, 200, 200 + index],
            class_label="stepwell" if index % 2 else "fort_wall",
            confidence=0.5 + index / 20,
        )
        for index in range(5)
    ]
    for event in events:
        engine.ingest(event)
    before = (engine.state_digest(), engine.audit_digest(), engine.state()["objects"])

    summary = engine.replay()
    assert summary["replayed_events"] == len(engine.event_log)
    assert (
        engine.state_digest(),
        engine.audit_digest(),
        engine.state()["objects"],
    ) == before

    shuffled = list(events)
    random.Random(7).shuffle(shuffled)
    fresh = ReconciliationEngine(engine.config)
    fresh.replay(shuffled)
    assert fresh.state_digest() == before[0]
    assert fresh.audit_digest() == before[1]


def test_replay_with_time_offset_shifts_timeline_but_keeps_decisions(engine):
    for index in range(3):
        engine.ingest(
            make_event(
                drone_id="drone_004",
                timestamp=f"2025-04-05T12:30:0{index}Z",
                bbox=[100, 100, 200 + index, 200],
                class_label="petroglyph",
                confidence=0.6,
            )
        )
    classes_before = [obj["class"] for obj in engine.state()["objects"]]
    rules_before = [entry["resolution_rule"] for entry in engine.audit_trail()]

    engine.replay(time_offset_seconds=3600)

    assert [obj["class"] for obj in engine.state()["objects"]] == classes_before
    assert [entry["resolution_rule"] for entry in engine.audit_trail()] == rules_before
    assert engine.event_log[0]["timestamp"].startswith("2025-04-05T13:30:0")


def test_replay_skips_duplicates(engine):
    event = make_event()
    engine.replay([event, event, event], reset_state=True)
    assert len(engine.event_log) == 1
    assert engine.state()["object_count"] == 1


def test_audit_entry_contains_full_decision_trace(engine):
    engine.ingest(make_event(class_label="cave_painting", confidence=0.6))
    engine.ingest(
        make_event(
            drone_id="drone_002",
            timestamp="2025-04-05T12:30:02Z",
            bbox=[101, 100, 199, 200],
            class_label="stone_inscription",
            confidence=0.9,
        )
    )
    entry = engine.audit_trail()[-1]
    assert entry["state_before"]["class"] == "cave_painting"
    assert entry["state_after"]["class"] == "stone_inscription"
    assert len(entry["input_events_considered"]) == 2
    assert entry["rule_order"][0] == "higher_confidence"
    assert entry["rule_evaluations"][0]["decisive"] is True
    assert entry["decision_timestamp"] == "2025-04-05T12:30:02Z"
    assert "higher confidence" in entry["explanation"]


def test_ingest_throughput_above_100_events_per_second(engine):
    events = [
        make_event(
            drone_id=f"drone_{index % 9 + 1:03d}",
            timestamp=f"2025-04-05T12:{index // 60 % 60:02d}:{index % 60:02d}Z",
            bbox=[index * 5, index * 5, index * 5 + 100, index * 5 + 100],
            class_label="temple_complex",
            confidence=0.5 + (index % 40) / 100,
        )
        for index in range(600)
    ]
    started = time.perf_counter()
    for event in events:
        engine.ingest(event)
    elapsed = time.perf_counter() - started
    assert len(engine.event_log) == 600
    assert elapsed < 6.0, f"ingested 600 events in {elapsed:.2f}s (<100 events/s)"
