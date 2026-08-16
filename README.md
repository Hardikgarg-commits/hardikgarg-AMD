# HeritageLens — Real-Time Heritage Object Detection Reconciliation

Reconciles asynchronous, overlapping heritage-object detections streamed by
multiple drones surveilling a shared zone into a **single deterministic,
replayable and auditable timeline**.

Each drone runs its own YOLOv11-style detector and reports detections over a
simulated telemetry stream. Drones disagree: they see the same object from
different angles, at different times, under different lighting/occlusion, and
sometimes assign different heritage classes. This service reconstructs the
global state of heritage-object presence, detects those conflicts, resolves them
with a fixed rule ladder, and explains every decision.

No database, message queue, cloud service or ML model is involved: only Python,
FastAPI, Streamlit, Plotly and local JSON files.

---

## How it works

```
drone telemetry ──POST /events──▶ append-only event log (canonically ordered)
                                        │
                                        ▼
                      IoU association ──▶ heritage object tracks
                                        │
                                        ▼
                    deterministic rule ladder ──▶ resolved class per object
                                        │
                                        ├─▶ GET /state      (global state)
                                        ├─▶ GET /audit      (decision traces)
                                        └─▶ GET /timeline   (presence timeline)
```

**Everything is derived from the event log.** The log is kept in *canonical
order* — `(timestamp, drone_id, event_id)` — and state, audit trail and timeline
are pure functions of that ordered log. Consequences:

* **Deterministic** — the same set of events always yields the same state and
  the same audit trail, byte for byte (verified via `state_digest` /
  `audit_digest`).
* **Order-independent / late-event safe** — when an event arrives out of order
  the derived state is *rebuilt* from the log rather than patched, so a late
  event produces exactly the result a clean replay would. Arrival order is
  recorded separately (`GET /arrivals/late`) so it never leaks into the audit
  trail.
* **Idempotent** — the idempotency key is `drone_id + timestamp + bbox`; a
  re-sent detection is rejected with `409` and changes nothing.
* **Replayable** — `POST /events/replay` re-derives the world from any event
  list (or from the stored log) with an arbitrary time offset.

### Object identity

A detection joins an existing heritage object when its bbox has
`IoU > 0.7` (configurable) with that object's current box; ties are broken by
the highest IoU, then by `object_id`. Otherwise a new object is created, its id
being a stable hash of the creating detection (`drone_id + timestamp + bbox`).

### Conflict detection

| Conflict type | Meaning |
| --- | --- |
| `multi_drone_same_object` | More than one drone contributed detections to the object |
| `conflicting_class_labels` | Competing detections disagree on the heritage class |
| `class_change_over_time` | The resolved class changed from the previous decision |
| `class_reverted` | The resolved class returned to a class it previously held (drift/flapping signal) |

### Deterministic conflict resolution

Competing detections for one object are ranked by a fixed rule ladder; the first
rule that discriminates wins (`rule_order` in `config/drones.json`):

1. `higher_confidence` — prefer the higher confidence score
2. `newer_timestamp` — prefer the newer detection
3. `higher_drone_reliability` — prefer the more reliable drone (configurable per drone)
4. `higher_historical_class_frequency` — prefer the class seen most often in this object's history
5. `lexicographic_event_id` — final tie-break so the outcome is never ambiguous

Spatial overlap (IoU) governs which detections compete at all, and
`conflict_window_seconds` optionally restricts competition to a recent time
window. No ML/LLM is used anywhere in resolution.

### Audit trail

Every decision produces a trace containing the triggering event, all events
considered, the IoU used to associate them, detected conflicts, the rule ladder
and which rule was decisive (with the compared values), the final class and
confidence, the object state **before and after**, the decision timestamp and a
human-readable explanation.

---

## Setup

```bash
git clone https://github.com/Hardikgarg-commits/hardikgarg-AMD.git
cd hardikgarg-AMD
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run

### API

```bash
uvicorn heritage_reconciler.api:app --reload --port 8000
# interactive docs: http://127.0.0.1:8000/docs
```

Ingest a detection:

```bash
curl -i -X POST http://127.0.0.1:8000/events -H 'content-type: application/json' -d '{
  "drone_id": "drone_001",
  "timestamp": "2025-04-05T12:30:00Z",
  "bbox": [100, 100, 200, 200],
  "class": "archaeological_site",
  "confidence": 0.87,
  "metadata": {"lighting": "low", "occlusion": "partial"}
}'
```

| Method & path | Purpose |
| --- | --- |
| `POST /events` | Ingest one detection — `201` created, `400` malformed, `409` duplicate |
| `POST /events/replay` | Replay a single event, a list, or the stored log; supports `time_offset_seconds` and `reset_state` |
| `GET /state` | Current global state of heritage objects (+ `state_digest`) |
| `GET /objects/{object_id}` | One object with its full detection and class history |
| `GET /audit` | Audit trail (`object_id`, `conflicts_only`, `limit` filters) |
| `GET /timeline` | Deterministic detection/decision timeline |
| `GET /arrivals/late` | Events that arrived out of order and forced a re-derivation |
| `GET /events` | Raw canonical event log |
| `GET /config` · `PUT /config/drones` | Read / update drone reliability (state is re-derived) |
| `GET /fixtures` · `POST /fixtures/{name}/load` | List and replay bundled fixture datasets |
| `POST /reset` | Clear state (`keep_log=true` keeps the log and re-derives) |

### Simulated multi-drone telemetry stream

```bash
python scripts/simulate_drones.py --fixture 08_mixed_edge_cases --shuffle --reset
```

`--shuffle` delivers the events out of order; the resulting `state_digest`
matches the in-order run.

### Dashboard

```bash
streamlit run dashboard/app.py       # http://localhost:8501
```

Tabs: global state, timeline (Plotly), audit trail with before/after state and
rule evaluations, zone map of reconciled bounding boxes, raw event log with JSON
downloads. The sidebar replays fixtures, applies time offsets, and edits drone
reliability (which re-derives state deterministically).

### CLI audit explorer

```bash
python -m heritage_reconciler.cli fixtures
python -m heritage_reconciler.cli replay 08_mixed_edge_cases --offset 3600
python -m heritage_reconciler.cli state
python -m heritage_reconciler.cli audit --conflicts-only
python -m heritage_reconciler.cli trace obj_71f7b3116466
python -m heritage_reconciler.cli timeline
```

## Test

```bash
pytest                 # 59 tests
```

Coverage of the required scenarios:

| Scenario | Tests |
| --- | --- |
| Duplicate events (`drone_id` + `timestamp` + `bbox`) | `tests/test_engine.py::test_duplicate_event_is_rejected_and_leaves_state_unchanged`, `test_duplicate_ignores_class_and_confidence_changes`, `tests/test_api.py::test_duplicate_event_returns_409_and_does_not_change_state` |
| Late events (older timestamp than current state) | `tests/test_engine.py::test_late_event_is_flagged_and_state_matches_clean_replay`, `tests/test_fixtures.py::test_late_out_of_order_fixture` |
| Conflicting class labels | `tests/test_engine.py::test_conflicting_class_labels_resolved_by_confidence`, tie-break tests for timestamp / reliability / class frequency |
| Multiple drones detecting the same object | `tests/test_engine.py::test_overlapping_detections_merge_into_single_object`, `tests/test_fixtures.py::test_multi_drone_reliability_fixture` |
| Temporal replay of events | `tests/test_engine.py::test_replay_reproduces_state_and_audit_trail`, `test_replay_with_time_offset_shifts_timeline_but_keeps_decisions`, `tests/test_api.py::test_replay_*` |
| State consistency after replay | `tests/test_engine.py::test_state_is_independent_of_arrival_order`, `tests/test_fixtures.py::test_fixture_is_replay_deterministic` (every fixture) |
| Throughput (≥100 events/s) | `tests/test_engine.py::test_ingest_throughput_above_100_events_per_second` |

## Where things live

| Path | Contents |
| --- | --- |
| `heritage_reconciler/engine.py` | Association, conflict detection, rule ladder, audit trail |
| `heritage_reconciler/api.py` | FastAPI app (`/events`, `/events/replay`, `/state`, `/audit`, …) |
| `heritage_reconciler/models.py` | Telemetry schema and idempotency keys |
| `heritage_reconciler/storage.py` | Local JSON store (`data/`) |
| `heritage_reconciler/cli.py` | CLI audit explorer |
| `config/drones.json` | IoU threshold, per-drone reliability, rule order |
| `fixtures/*.json` | 8 fixture datasets, each documenting its edge case |
| `dashboard/app.py` | Streamlit + Plotly dashboard |
| `scripts/simulate_drones.py` | Simulated multi-drone telemetry stream |
| `data/` | Runtime store written by the API/dashboard (`events.json`, `state.json`, `audit_trail.json`, `timeline.json`) |
| `samples/audit_output/` | Committed sample audit and decision-trace output for `08_mixed_edge_cases` |

Set `HERITAGE_DATA_DIR` to relocate the runtime store, `HERITAGE_PERSIST=0` to
run purely in memory.

## Fixture datasets (edge cases)

| Fixture | Edge case |
| --- | --- |
| `01_duplicate_events` | Identical detection re-sent three times → idempotent, two `409`s |
| `02_late_out_of_order` | High-confidence detection arrives *after* newer ones → re-derivation |
| `03_conflicting_classes` | Two drones, same object, different classes → confidence decides |
| `04_multi_drone_reliability_tiebreak` | Three drones, identical confidence *and* timestamp → drone reliability decides |
| `05_class_flap_and_revert` | Model drift flips the class and then reverts → `class_reverted` |
| `06_frequency_tiebreak` | Confidence, timestamp and reliability all tie → historical class frequency decides |
| `07_disjoint_objects` | Non-overlapping boxes (incl. IoU ≈ 0.5 near-miss) stay separate objects |
| `08_mixed_edge_cases` | All of the above interleaved in one stream (4 objects) |
