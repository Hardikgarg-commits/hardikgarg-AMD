"""Streamlit dashboard for exploring reconciled state and audit traces.

Run with::

    streamlit run dashboard/app.py

The dashboard works fully offline: it drives an in-process
:class:`~heritage_reconciler.engine.ReconciliationEngine` over the JSON store in
``data/``, so no API server is required (though the API writes to the same
store, in which case the dashboard shows live results after a rerun).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from heritage_reconciler.config import ReconcilerConfig
from heritage_reconciler.engine import ReconciliationEngine
from heritage_reconciler.fixtures import (
    list_fixtures,
    load_fixture,
    load_fixture_raw,
)
from heritage_reconciler.storage import JsonStore

st.set_page_config(page_title="HeritageLens Reconciliation", layout="wide")


@st.cache_resource
def _store_and_engine() -> tuple[JsonStore, ReconciliationEngine]:
    store = JsonStore()
    engine = ReconciliationEngine(ReconcilerConfig.load())
    store.load_into(engine)
    return store, engine


store, engine = _store_and_engine()

st.title("HeritageLens — Multi-Drone Detection Reconciliation")
st.caption(
    "Deterministic reconciliation of overlapping heritage object detections, "
    "with a full audit trail of every conflict resolution decision."
)

with st.sidebar:
    st.header("Event source")
    fixtures = list_fixtures()
    fixture = st.selectbox("Fixture dataset", fixtures, index=len(fixtures) - 1)
    st.caption(load_fixture_raw(fixture).get("description", ""))
    offset = st.number_input("Replay time offset (seconds)", value=0.0, step=60.0)
    append = st.checkbox("Append to current state", value=False)

    if st.button("Replay fixture", type="primary"):
        summary = engine.replay(
            load_fixture(fixture),
            time_offset_seconds=float(offset),
            reset_state=not append,
        )
        store.persist(engine)
        st.success(
            f"Replayed {summary['replayed_events']} events "
            f"({summary['skipped_duplicates']} duplicates skipped)"
        )

    if st.button("Re-derive from stored log"):
        engine.replay()
        store.persist(engine)
        st.success("State and audit trail rebuilt from the event log")

    if st.button("Reset store"):
        engine.reset()
        store.clear()
        st.warning("Cleared events, state and audit trail")

    st.header("Drone reliability")
    st.caption("Tie-breaker when confidence and timestamp match.")
    drones = sorted(
        {event["drone_id"] for event in engine.event_log}
        | set(engine.config.drone_reliability)
    )
    changed = False
    for drone_id in drones:
        value = st.slider(
            drone_id,
            0.0,
            1.0,
            float(engine.config.reliability_of(drone_id)),
            0.05,
            key=f"rel_{drone_id}",
        )
        if value != engine.config.reliability_of(drone_id):
            engine.config.drone_reliability[drone_id] = value
            changed = True
    if changed:
        engine.replay()
        store.persist(engine)
        st.info("Reliability changed — state re-derived deterministically")

state = engine.state()
audit = engine.audit_trail()
timeline = engine.timeline()

col1, col2, col3, col4 = st.columns(4)
col1.metric("Heritage objects", state["object_count"])
col2.metric("Events ingested", state["event_count"])
col3.metric("Decisions", state["decision_count"])
col4.metric("Conflicting decisions", sum(1 for e in audit if e["conflicts"]))
st.caption(
    f"State digest `{state['state_digest'][:16]}` · audit digest `{engine.audit_digest()[:16]}`"
)

tab_state, tab_timeline, tab_audit, tab_map, tab_events = st.tabs(
    ["Global state", "Timeline", "Audit trail", "Zone map", "Raw events"]
)

with tab_state:
    if state["objects"]:
        st.dataframe(
            [
                {
                    "object_id": obj["object_id"],
                    "class": obj["class"],
                    "confidence": obj["confidence"],
                    "drones": ", ".join(obj["drone_ids"]),
                    "detections": obj["detection_count"],
                    "first_seen": obj["first_seen"],
                    "last_seen": obj["last_seen"],
                    "class_frequency": json.dumps(obj["class_frequency"]),
                }
                for obj in state["objects"]
            ],
            width="stretch",
            hide_index=True,
        )
        st.plotly_chart(
            px.bar(
                {
                    "object_id": [obj["object_id"] for obj in state["objects"]],
                    "confidence": [obj["confidence"] for obj in state["objects"]],
                    "class": [obj["class"] for obj in state["objects"]],
                },
                x="object_id",
                y="confidence",
                color="class",
                title="Resolved class and confidence per heritage object",
            ),
            width="stretch",
        )
    else:
        st.info("No state yet — replay a fixture from the sidebar.")

with tab_timeline:
    if timeline:
        rows = [
            {**item, "conflicts": ", ".join(item["conflicts"])} for item in timeline
        ]
        columns = {key: [row[key] for row in rows] for key in rows[0]}
        st.plotly_chart(
            px.scatter(
                columns,
                x="timestamp",
                y="object_id",
                color="class",
                symbol="event",
                hover_data=["drone_id", "resolution_rule", "conflicts", "confidence"],
                title="Deterministic detection timeline",
            ),
            width="stretch",
        )
        st.dataframe(rows, width="stretch", hide_index=True)
        late = engine.late_arrivals()
        if late:
            st.subheader("Late arrivals (re-derivations)")
            st.dataframe(late, width="stretch", hide_index=True)
    else:
        st.info("No timeline yet — replay a fixture from the sidebar.")

with tab_audit:
    object_ids = ["(all)"] + [obj["object_id"] for obj in state["objects"]]
    selected = st.selectbox("Object", object_ids)
    conflicts_only = st.checkbox("Conflicting decisions only", value=False)
    entries = engine.audit_trail(
        object_id=None if selected == "(all)" else selected,
        conflicts_only=conflicts_only,
    )
    st.caption(f"{len(entries)} decision(s)")
    for entry in reversed(entries):
        conflicts = ", ".join(c["type"] for c in entry["conflicts"]) or "none"
        header = (
            f"{entry['decision_id']} · {entry['decision_timestamp']} · "
            f"{entry['object_id']} · rule={entry['resolution_rule']} · "
            f"conflicts={conflicts}"
        )
        with st.expander(header):
            st.write(entry["explanation"])
            left, right = st.columns(2)
            left.subheader("State before")
            left.json(entry["state_before"] or {"note": "object did not exist"})
            right.subheader("State after")
            right.json(entry["state_after"])
            st.subheader("Events considered")
            st.dataframe(
                [
                    {**event, "metadata": json.dumps(event["metadata"])}
                    for event in entry["input_events_considered"]
                ],
                width="stretch",
                hide_index=True,
            )
            if entry["rule_evaluations"]:
                st.subheader("Rule evaluation order")
                st.dataframe(
                    [
                        {
                            key: value if key == "rule" else json.dumps(value)
                            for key, value in evaluation.items()
                        }
                        for evaluation in entry["rule_evaluations"]
                    ],
                    width="stretch",
                    hide_index=True,
                )

with tab_map:
    if state["objects"]:
        figure = go.Figure()
        for obj in state["objects"]:
            x_min, y_min, x_max, y_max = obj["bbox"]
            figure.add_shape(
                type="rect",
                x0=x_min,
                y0=y_min,
                x1=x_max,
                y1=y_max,
                line={"width": 2},
            )
            figure.add_trace(
                go.Scatter(
                    x=[(x_min + x_max) / 2],
                    y=[(y_min + y_max) / 2],
                    mode="markers+text",
                    text=[f"{obj['class']} ({obj['confidence']:.2f})"],
                    textposition="top center",
                    name=obj["object_id"],
                )
            )
        figure.update_layout(
            title="Reconciled objects in the shared surveillance zone",
            xaxis_title="x (px)",
            yaxis_title="y (px)",
            yaxis={"autorange": "reversed"},
            height=600,
        )
        st.plotly_chart(figure, width="stretch")
    else:
        st.info("No objects to draw yet.")

with tab_events:
    if engine.event_log:
        st.dataframe(
            [
                {**event, "metadata": json.dumps(event.get("metadata", {}))}
                for event in engine.event_log
            ],
            width="stretch",
            hide_index=True,
        )
        st.download_button(
            "Download event log (JSON)",
            json.dumps(engine.event_log, indent=2),
            file_name="events.json",
            mime="application/json",
        )
        st.download_button(
            "Download audit trail (JSON)",
            json.dumps(audit, indent=2),
            file_name="audit_trail.json",
            mime="application/json",
        )
    else:
        st.info("No events ingested yet.")
