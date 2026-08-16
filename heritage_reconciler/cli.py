"""CLI for replaying fixtures and exploring the audit trail locally.

Examples
--------
    python -m heritage_reconciler.cli fixtures
    python -m heritage_reconciler.cli replay 08_mixed_edge_cases --offset 3600
    python -m heritage_reconciler.cli state
    python -m heritage_reconciler.cli audit --conflicts-only
    python -m heritage_reconciler.cli trace obj_1a2b3c4d5e6f
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ReconcilerConfig
from .engine import ReconciliationEngine
from .fixtures import list_fixtures, load_fixture, load_fixture_raw
from .storage import JsonStore


def _engine(store: JsonStore) -> ReconciliationEngine:
    engine = ReconciliationEngine(ReconcilerConfig.load())
    store.load_into(engine)
    return engine


def _print(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def cmd_fixtures(_args: argparse.Namespace, _store: JsonStore) -> int:
    for name in list_fixtures():
        payload = load_fixture_raw(name)
        print(f"{name}: {payload.get('description', '')}")
        print(f"  events={len(payload['events'])}")
    return 0


def cmd_replay(args: argparse.Namespace, store: JsonStore) -> int:
    engine = ReconciliationEngine(ReconcilerConfig.load())
    if not args.fixture:
        store.load_into(engine)
        summary = engine.replay(time_offset_seconds=args.offset)
    else:
        events = load_fixture(args.fixture)
        summary = engine.replay(
            events, time_offset_seconds=args.offset, reset_state=not args.append
        )
    store.persist(engine)
    _print(summary)
    return 0


def cmd_state(_args: argparse.Namespace, store: JsonStore) -> int:
    _print(_engine(store).state())
    return 0


def cmd_audit(args: argparse.Namespace, store: JsonStore) -> int:
    engine = _engine(store)
    entries = engine.audit_trail(
        object_id=args.object_id, conflicts_only=args.conflicts_only
    )
    if args.limit:
        entries = entries[-args.limit :]
    if args.json:
        _print(entries)
        return 0
    for entry in entries:
        conflicts = ", ".join(c["type"] for c in entry["conflicts"]) or "none"
        print(
            f"[{entry['decision_id']}] {entry['decision_timestamp']} "
            f"{entry['object_id']} rule={entry['resolution_rule']} "
            f"class={entry['final_class']} conflicts={conflicts}"
        )
        print(f"    {entry['explanation']}")
    return 0


def cmd_trace(args: argparse.Namespace, store: JsonStore) -> int:
    engine = _engine(store)
    detail = engine.object_detail(args.object_id)
    if detail is None:
        print(f"unknown object_id: {args.object_id}", file=sys.stderr)
        return 1
    _print(
        {
            "object": detail,
            "decisions": engine.audit_trail(object_id=args.object_id),
        }
    )
    return 0


def cmd_timeline(_args: argparse.Namespace, store: JsonStore) -> int:
    _print(_engine(store).timeline())
    return 0


def cmd_reset(_args: argparse.Namespace, store: JsonStore) -> int:
    store.clear()
    print("cleared persisted state, audit trail and event log")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="heritage-reconciler",
        description="Replay heritage detection telemetry and explore audit traces.",
    )
    parser.add_argument(
        "--data-dir", type=Path, default=None, help="directory of the JSON store"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("fixtures", help="list bundled fixture datasets")

    replay = sub.add_parser("replay", help="replay a fixture or the stored event log")
    replay.add_argument("fixture", nargs="?", help="fixture name (omit to replay log)")
    replay.add_argument("--offset", type=float, default=0.0, help="time offset seconds")
    replay.add_argument(
        "--append", action="store_true", help="keep existing state instead of resetting"
    )

    sub.add_parser("state", help="print the reconciled global state")

    audit = sub.add_parser("audit", help="print the audit trail")
    audit.add_argument("--object-id", default=None)
    audit.add_argument("--conflicts-only", action="store_true")
    audit.add_argument("--limit", type=int, default=None)
    audit.add_argument("--json", action="store_true")

    trace = sub.add_parser("trace", help="print full decision trace for one object")
    trace.add_argument("object_id")

    sub.add_parser("timeline", help="print the deterministic detection timeline")
    sub.add_parser("reset", help="delete persisted JSON store files")
    return parser


COMMANDS = {
    "fixtures": cmd_fixtures,
    "replay": cmd_replay,
    "state": cmd_state,
    "audit": cmd_audit,
    "trace": cmd_trace,
    "timeline": cmd_timeline,
    "reset": cmd_reset,
}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    store = JsonStore(args.data_dir)
    return COMMANDS[args.command](args, store)


if __name__ == "__main__":
    raise SystemExit(main())
