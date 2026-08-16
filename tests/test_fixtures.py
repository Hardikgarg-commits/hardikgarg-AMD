"""Tests asserting each bundled fixture exercises its documented edge case."""

from __future__ import annotations

import pytest

from heritage_reconciler.engine import DuplicateEventError, ReconciliationEngine
from heritage_reconciler.fixtures import list_fixtures, load_fixture, load_fixture_raw


def _ingest_all(engine: ReconciliationEngine, name: str) -> int:
    duplicates = 0
    for event in load_fixture(name):
        try:
            engine.ingest(event)
        except DuplicateEventError:
            duplicates += 1
    return duplicates


def test_at_least_five_fixtures_are_shipped():
    assert len(list_fixtures()) >= 5


@pytest.mark.parametrize("name", list_fixtures())
def test_fixture_is_replay_deterministic(name, config):
    engine = ReconciliationEngine(config)
    _ingest_all(engine, name)
    state_digest, audit_digest = engine.state_digest(), engine.audit_digest()

    engine.replay()
    assert (engine.state_digest(), engine.audit_digest()) == (
        state_digest,
        audit_digest,
    )

    fresh = ReconciliationEngine(config)
    fresh.replay(load_fixture(name))
    assert (fresh.state_digest(), fresh.audit_digest()) == (state_digest, audit_digest)


@pytest.mark.parametrize("name", list_fixtures())
def test_fixture_documents_itself(name):
    payload = load_fixture_raw(name)
    assert payload["description"]
    assert payload["events"]


def test_duplicate_fixture(config):
    engine = ReconciliationEngine(config)
    duplicates = _ingest_all(engine, "01_duplicate_events")
    assert duplicates == 2
    assert engine.state()["object_count"] == 1
    assert engine.state()["objects"][0]["detection_count"] == 1


def test_late_out_of_order_fixture(config):
    engine = ReconciliationEngine(config)
    _ingest_all(engine, "02_late_out_of_order")
    state = engine.state()
    assert state["object_count"] == 1
    assert state["objects"][0]["class"] == "temple_complex"
    assert len(engine.late_arrivals()) == 2


def test_conflicting_classes_fixture(config):
    engine = ReconciliationEngine(config)
    _ingest_all(engine, "03_conflicting_classes")
    assert engine.state()["objects"][0]["class"] == "stone_inscription"
    last = engine.audit_trail()[-1]
    assert last["resolution_rule"] == "higher_confidence"
    assert {c["type"] for c in last["conflicts"]} >= {
        "conflicting_class_labels",
        "multi_drone_same_object",
    }


def test_multi_drone_reliability_fixture(config):
    engine = ReconciliationEngine(config)
    _ingest_all(engine, "04_multi_drone_reliability_tiebreak")
    state = engine.state()
    assert state["object_count"] == 1
    assert state["objects"][0]["class"] == "fort_wall"
    assert state["objects"][0]["drone_ids"] == ["drone_001", "drone_005", "drone_006"]
    assert engine.audit_trail()[-1]["resolution_rule"] == "higher_drone_reliability"


def test_class_flap_and_revert_fixture(config):
    engine = ReconciliationEngine(config)
    _ingest_all(engine, "05_class_flap_and_revert")
    object_id = engine.state()["objects"][0]["object_id"]
    history = [
        entry["class"] for entry in engine.object_detail(object_id)["class_history"]
    ]
    assert history == ["temple_complex", "mosque_ruin", "temple_complex"]
    assert any(
        c["type"] == "class_reverted"
        for entry in engine.audit_trail()
        for c in entry["conflicts"]
    )


def test_frequency_tiebreak_fixture(config):
    engine = ReconciliationEngine(config)
    _ingest_all(engine, "06_frequency_tiebreak")
    assert engine.state()["objects"][0]["class"] == "rock_shelter"
    assert (
        engine.audit_trail()[-1]["resolution_rule"]
        == "higher_historical_class_frequency"
    )


def test_disjoint_objects_fixture(config):
    engine = ReconciliationEngine(config)
    _ingest_all(engine, "07_disjoint_objects")
    assert engine.state()["object_count"] == 3
    assert all(not entry["conflicts"] for entry in engine.audit_trail())


def test_mixed_edge_cases_fixture(config):
    engine = ReconciliationEngine(config)
    duplicates = _ingest_all(engine, "08_mixed_edge_cases")
    assert duplicates == 1
    state = engine.state()
    assert state["object_count"] == 4

    conflict_types = {
        conflict["type"]
        for entry in engine.audit_trail()
        for conflict in entry["conflicts"]
    }
    assert {
        "multi_drone_same_object",
        "conflicting_class_labels",
        "class_change_over_time",
    } <= conflict_types
    assert engine.late_arrivals()
