"""FastAPI application exposing the reconciliation engine."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from .config import ReconcilerConfig
from .engine import DuplicateEventError, ReconciliationEngine
from .fixtures import FIXTURES_DIR, list_fixtures, load_fixture
from .models import DetectionEvent, ReliabilityConfigUpdate, ReplayRequest
from .storage import JsonStore

engine = ReconciliationEngine(ReconcilerConfig.load())
store = JsonStore(os.environ.get("HERITAGE_DATA_DIR"))
PERSIST = os.environ.get("HERITAGE_PERSIST", "1") != "0"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if PERSIST and os.environ.get("HERITAGE_LOAD_ON_START", "1") != "0":
        store.load_into(engine)
    yield


app = FastAPI(
    lifespan=lifespan,
    title="HeritageLens Reconciliation API",
    version="1.0.0",
    description=(
        "Reconciles asynchronous, overlapping heritage object detections coming "
        "from multiple drones into a deterministic, auditable timeline."
    ),
)


def _persist() -> None:
    if PERSIST:
        store.persist(engine)


@app.exception_handler(RequestValidationError)
async def _validation_handler(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Malformed telemetry is a 400, not FastAPI's default 422."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"detail": "malformed input", "errors": _stringify(exc.errors())},
    )


def _stringify(errors: Any) -> Any:
    if isinstance(errors, list):
        return [_stringify(item) for item in errors]
    if isinstance(errors, dict):
        return {key: _stringify(value) for key, value in errors.items()}
    if isinstance(errors, (str, int, float, bool)) or errors is None:
        return errors
    return str(errors)


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "events": len(engine.event_log)}


@app.post("/events", status_code=status.HTTP_201_CREATED)
def post_event(event: DetectionEvent) -> dict[str, Any]:
    """Ingest a single detection event."""
    try:
        result = engine.ingest(event)
    except DuplicateEventError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "duplicate event",
                "reason": exc.reason,
                "event_id": exc.event_id,
                "dedup_key": exc.dedup_key,
            },
        ) from exc
    _persist()
    return {
        "status": "accepted",
        "event_id": result["event_id"],
        "late_event": result["late_event"],
        "object_id": result["decision"]["object_id"],
        "resolved_class": result["decision"]["final_class"],
        "decision": result["decision"],
    }


@app.post("/events/replay", status_code=status.HTTP_200_OK)
def post_replay(payload: Annotated[Any, Body()] = None) -> dict[str, Any]:
    """Replay events in canonical order.

    Accepts a single event object, a list of events, or a
    ``{"events": [...], "time_offset_seconds": 0, "reset_state": true}`` body.
    An empty body replays the events already stored in the log.
    """
    try:
        request = _parse_replay_payload(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "malformed input", "errors": _stringify(exc.errors())},
        ) from exc

    summary = engine.replay(
        request.events,
        time_offset_seconds=request.time_offset_seconds,
        reset_state=request.reset_state,
    )
    _persist()
    return {"status": "replayed", **summary}


def _parse_replay_payload(payload: Any) -> ReplayRequest:
    if payload is None:
        return ReplayRequest()
    if isinstance(payload, list):
        return ReplayRequest(events=[DetectionEvent(**item) for item in payload])
    if isinstance(payload, dict):
        if "drone_id" in payload:
            # A bare detection event, per the documented /events body.
            body = dict(payload)
            offset = float(body.pop("time_offset_seconds", 0.0) or 0.0)
            reset = bool(body.pop("reset_state", False))
            return ReplayRequest(
                events=[DetectionEvent(**body)],
                time_offset_seconds=offset,
                reset_state=reset,
            )
        # ``events`` omitted: replay the stored log, optionally time-shifted.
        return ReplayRequest(**payload)
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail={"message": "malformed input", "errors": ["unsupported body type"]},
    )


@app.get("/state")
def get_state() -> dict[str, Any]:
    return engine.state()


@app.get("/objects/{object_id}")
def get_object(object_id: str) -> dict[str, Any]:
    detail = engine.object_detail(object_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="unknown object_id")
    return detail


@app.get("/audit")
def get_audit(
    object_id: str | None = None,
    conflicts_only: bool = False,
    limit: int | None = Query(default=None, ge=1),
) -> dict[str, Any]:
    entries = engine.audit_trail(object_id=object_id, conflicts_only=conflicts_only)
    if limit is not None:
        entries = entries[-limit:]
    return {
        "entries": entries,
        "count": len(entries),
        "audit_digest": engine.audit_digest(),
    }


@app.get("/timeline")
def get_timeline() -> dict[str, Any]:
    return {"timeline": engine.timeline()}


@app.get("/arrivals/late")
def get_late_arrivals() -> dict[str, Any]:
    """Events that arrived out of order and triggered a state re-derivation."""
    arrivals = engine.late_arrivals()
    return {"late_arrivals": arrivals, "count": len(arrivals)}


@app.get("/events")
def get_events() -> dict[str, Any]:
    return {"events": engine.event_log, "count": len(engine.event_log)}


@app.get("/config")
def get_config() -> dict[str, Any]:
    return engine.config.to_dict()


@app.put("/config/drones")
def put_drone_config(update: ReliabilityConfigUpdate) -> dict[str, Any]:
    """Update per-drone reliability signals and re-derive state deterministically."""
    for drone in update.drones:
        engine.config.drone_reliability[drone.drone_id] = drone.reliability
    if update.default_reliability is not None:
        engine.config.default_reliability = update.default_reliability
    engine.replay()
    _persist()
    return engine.config.to_dict()


@app.get("/fixtures")
def get_fixtures() -> dict[str, Any]:
    return {"fixtures": list_fixtures(), "directory": str(FIXTURES_DIR)}


@app.post("/fixtures/{name}/load")
def post_load_fixture(name: str, reset_state: bool = True) -> dict[str, Any]:
    try:
        events = load_fixture(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="unknown fixture") from exc
    summary = engine.replay(events, reset_state=reset_state)
    _persist()
    return {"status": "loaded", "fixture": name, **summary}


@app.post("/reset")
def post_reset(keep_log: bool = False) -> dict[str, Any]:
    engine.reset(keep_log=keep_log)
    if keep_log:
        engine.replay()
    _persist()
    return {"status": "reset", "events": len(engine.event_log)}


def create_app(
    config: ReconcilerConfig | None = None, data_dir: Path | str | None = None
) -> FastAPI:
    """Rewire the module-level engine/store (used by tests and CLI runners)."""
    global engine, store
    engine = ReconciliationEngine(config or ReconcilerConfig.load())
    store = JsonStore(data_dir)
    return app
