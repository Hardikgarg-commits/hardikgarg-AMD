"""Tests for the local audit-exploration CLI."""

from __future__ import annotations

import json

from heritage_reconciler.cli import main


def _run(capsys, *argv: str) -> str:
    assert main(list(argv)) == 0
    return capsys.readouterr().out


def test_cli_replay_state_audit_and_trace(tmp_path, capsys):
    data_dir = ["--data-dir", str(tmp_path)]

    summary = json.loads(_run(capsys, *data_dir, "replay", "08_mixed_edge_cases"))
    assert summary["objects"] == 4
    assert (tmp_path / "audit_trail.json").exists()

    state = json.loads(_run(capsys, *data_dir, "state"))
    assert state["object_count"] == 4

    audit_text = _run(capsys, *data_dir, "audit", "--conflicts-only")
    assert "rule=" in audit_text

    object_id = state["objects"][0]["object_id"]
    trace = json.loads(_run(capsys, *data_dir, "trace", object_id))
    assert trace["object"]["object_id"] == object_id
    assert trace["decisions"]

    timeline = json.loads(_run(capsys, *data_dir, "timeline"))
    assert timeline[0]["event"] == "object_created"

    assert main([*data_dir, "trace", "obj_unknown"]) == 1

    _run(capsys, *data_dir, "reset")
    assert not (tmp_path / "state.json").exists()


def test_cli_replay_with_offset_persists_shifted_events(tmp_path, capsys):
    data_dir = ["--data-dir", str(tmp_path)]
    _run(capsys, *data_dir, "replay", "03_conflicting_classes", "--offset", "60")
    events = json.loads((tmp_path / "events.json").read_text())
    assert events[0]["timestamp"] == "2025-04-05T13:01:00Z"


def test_cli_fixtures_listing(capsys):
    output = _run(capsys, "fixtures")
    assert "08_mixed_edge_cases" in output
    assert "events=" in output
