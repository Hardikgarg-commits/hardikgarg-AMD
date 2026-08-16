"""Simulated multi-drone telemetry stream against a running API.

Streams a fixture dataset to ``POST /events`` the way independent drones would:
interleaved, optionally shuffled (so events arrive out of order) and with
duplicate re-sends, then prints the reconciled state.

    python scripts/simulate_drones.py --fixture 08_mixed_edge_cases --shuffle
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from heritage_reconciler.fixtures import list_fixtures, load_fixture_raw


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--fixture", default="08_mixed_edge_cases", choices=list_fixtures()
    )
    parser.add_argument("--shuffle", action="store_true", help="deliver out of order")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument(
        "--delay", type=float, default=0.01, help="seconds between posts"
    )
    parser.add_argument("--reset", action="store_true", help="reset server state first")
    args = parser.parse_args(argv)

    events = load_fixture_raw(args.fixture)["events"]
    if args.shuffle:
        random.Random(args.seed).shuffle(events)

    if args.reset:
        requests.post(f"{args.url}/reset", timeout=10).raise_for_status()

    accepted = duplicates = rejected = 0
    for event in events:
        response = requests.post(f"{args.url}/events", json=event, timeout=10)
        if response.status_code == 201:
            accepted += 1
            body = response.json()
            print(
                f"201 {body['event_id']} -> {body['object_id']} "
                f"class={body['resolved_class']} "
                f"rule={body['decision']['resolution_rule']}"
                f"{' (late)' if body['late_event'] else ''}"
            )
        elif response.status_code == 409:
            duplicates += 1
            print(f"409 duplicate: {response.json()['detail']['dedup_key']}")
        else:
            rejected += 1
            print(f"{response.status_code} {response.text}")
        time.sleep(args.delay)

    print(f"\naccepted={accepted} duplicates={duplicates} rejected={rejected}")
    print(json.dumps(requests.get(f"{args.url}/state", timeout=10).json(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
