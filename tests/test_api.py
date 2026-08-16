"""HTTP contract tests for the FastAPI ingestion, replay and query endpoints."""

from __future__ import annotations

import json

EVENT = {
    "drone_id": "drone_001",
    "timestamp": "2025-04-05T12:30:00Z",
    "bbox": [100, 100, 200, 200],
    "class": "archaeological_site",
    "confidence": 0.87,
    "metadata": {"lighting": "low", "occlusion": "partial"},
}


def test_post_event_returns_201(api_client):
    response = api_client.post("/events", json=EVENT)
    assert response.status_code == 201
    body = response.json()
    assert body["resolved_class"] == "archaeological_site"
    assert body["object_id"].startswith("obj_")
    assert body["decision"]["resolution_rule"] == "initial_detection"


def test_duplicate_event_returns_409_and_does_not_change_state(api_client):
    assert api_client.post("/events", json=EVENT).status_code == 201
    state_before = api_client.get("/state").json()

    response = api_client.post("/events", json=EVENT)
    assert response.status_code == 409
    assert response.json()["detail"]["reason"].startswith("same drone_id")
    assert api_client.get("/state").json() == state_before


def test_malformed_event_returns_400(api_client):
    for payload in (
        {**EVENT, "bbox": [1, 2, 3]},
        {**EVENT, "bbox": [200, 200, 100, 100]},
        {**EVENT, "confidence": 1.4},
        {**EVENT, "timestamp": "not-a-timestamp"},
        {k: v for k, v in EVENT.items() if k != "drone_id"},
    ):
        response = api_client.post("/events", json=payload)
        assert response.status_code == 400, payload
        assert response.json()["detail"] == "malformed input"


def test_state_endpoint_reports_conflict_resolution(api_client):
    api_client.post("/events", json=EVENT)
    api_client.post(
        "/events",
        json={
            **EVENT,
            "drone_id": "drone_005",
            "timestamp": "2025-04-05T12:30:04Z",
            "bbox": [102, 99, 201, 199],
            "class": "temple_complex",
            "confidence": 0.95,
        },
    )
    state = api_client.get("/state").json()
    assert state["object_count"] == 1
    assert state["objects"][0]["class"] == "temple_complex"
    assert state["objects"][0]["drone_ids"] == ["drone_001", "drone_005"]

    audit = api_client.get("/audit", params={"conflicts_only": True}).json()
    assert audit["count"] == 1
    entry = audit["entries"][0]
    assert entry["resolution_rule"] == "higher_confidence"
    assert entry["state_before"]["class"] == "archaeological_site"


def test_replay_endpoint_with_event_list_is_deterministic(api_client):
    events = [
        EVENT,
        {
            **EVENT,
            "drone_id": "drone_002",
            "timestamp": "2025-04-05T12:30:02Z",
            "bbox": [101, 101, 199, 199],
            "class": "temple_complex",
            "confidence": 0.9,
        },
    ]
    first = api_client.post("/events/replay", json={"events": events}).json()
    second = api_client.post("/events/replay", json={"events": events}).json()
    assert first["status"] == "replayed"
    assert first["state_digest"] == second["state_digest"]
    assert first["replayed_events"] == 2

    audit_digest = api_client.get("/audit").json()["audit_digest"]
    api_client.post("/events/replay", json=list(reversed(events)))
    assert api_client.get("/audit").json()["audit_digest"] == audit_digest


def test_replay_endpoint_accepts_single_event_body(api_client):
    response = api_client.post("/events/replay", json=EVENT)
    assert response.status_code == 200
    assert response.json()["replayed_events"] == 1
    assert api_client.get("/state").json()["object_count"] == 1


def test_replay_of_stored_log_reproduces_state(api_client):
    api_client.post("/events", json=EVENT)
    api_client.post(
        "/events",
        json={
            **EVENT,
            "drone_id": "drone_003",
            "timestamp": "2025-04-05T12:30:06Z",
            "bbox": [99, 102, 200, 201],
            "class": "fort_wall",
            "confidence": 0.66,
        },
    )
    before_state = api_client.get("/state").json()
    before_audit = api_client.get("/audit").json()

    replay = api_client.post("/events/replay", json=None).json()
    assert replay["replayed_events"] == 2
    assert api_client.get("/state").json() == before_state
    assert api_client.get("/audit").json() == before_audit


def test_replay_with_time_offset(api_client):
    api_client.post("/events", json=EVENT)
    api_client.post("/events/replay", json={"time_offset_seconds": 60})
    assert api_client.get("/events").json()["events"][0]["timestamp"] == (
        "2025-04-05T12:31:00Z"
    )


def test_malformed_replay_body_returns_400(api_client):
    response = api_client.post("/events/replay", json={"events": [{"drone_id": "d"}]})
    assert response.status_code == 400


def test_object_detail_and_timeline_endpoints(api_client):
    object_id = api_client.post("/events", json=EVENT).json()["object_id"]
    detail = api_client.get(f"/objects/{object_id}").json()
    assert detail["object_id"] == object_id
    assert len(detail["events"]) == 1
    assert api_client.get("/objects/obj_missing").status_code == 404

    timeline = api_client.get("/timeline").json()["timeline"]
    assert timeline[0]["event"] == "object_created"


def test_drone_reliability_update_rebuilds_state(api_client):
    api_client.post(
        "/events", json={**EVENT, "drone_id": "drone_a", "class": "fort_wall"}
    )
    api_client.post(
        "/events",
        json={
            **EVENT,
            "drone_id": "drone_b",
            "bbox": [101, 99, 199, 201],
            "class": "stepwell",
        },
    )
    assert api_client.get("/state").json()["objects"][0]["class"] == "fort_wall"

    response = api_client.put(
        "/config/drones",
        json={"drones": [{"drone_id": "drone_b", "reliability": 1.0}]},
    )
    assert response.status_code == 200
    assert response.json()["drone_reliability"]["drone_b"] == 1.0
    assert api_client.get("/state").json()["objects"][0]["class"] == "stepwell"


def test_fixture_endpoints_and_persistence(api_client, tmp_path):
    fixtures = api_client.get("/fixtures").json()["fixtures"]
    assert "08_mixed_edge_cases" in fixtures

    summary = api_client.post("/fixtures/08_mixed_edge_cases/load").json()
    assert summary["skipped_duplicates"] == 1
    assert summary["objects"] == 4

    state_file = tmp_path / "data" / "state.json"
    audit_file = tmp_path / "data" / "audit_trail.json"
    assert state_file.exists() and audit_file.exists()
    persisted = json.loads(state_file.read_text())
    assert persisted["state_digest"] == summary["state_digest"]
    assert json.loads(audit_file.read_text())

    assert api_client.post("/fixtures/nope/load").status_code == 404


def test_reset_endpoint(api_client):
    api_client.post("/events", json=EVENT)
    assert api_client.post("/reset").json()["events"] == 0
    assert api_client.get("/state").json()["object_count"] == 0
