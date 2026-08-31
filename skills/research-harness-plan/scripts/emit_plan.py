#!/usr/bin/env python3
"""Emit a harness experiment/plan.json with a correct plan_hash."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def content_hash(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "plan_hash"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--planned-units", type=int, required=True)
    parser.add_argument("--design-seed", default="42")
    parser.add_argument("--unit-of-analysis", required=True)
    parser.add_argument("--schedule", type=Path, default=None)
    parser.add_argument("--decision-rule", action="append", default=[])
    parser.add_argument("--freeze", action="store_true")
    parser.add_argument("-o", "--output", type=Path, required=True)
    args = parser.parse_args()

    schedule_hash = None
    schedule_path = None
    if args.schedule is not None:
        schedule_path = str(args.schedule)
        if args.schedule.exists():
            schedule_hash = hash_file(args.schedule)

    rules = args.decision_rule or [
        "Interpret compatibility with predicted patterns; do not select a hypothesis automatically."
    ]
    payload = {
        "schema_version": "1.0",
        "planned_units": args.planned_units,
        "design_seed": args.design_seed,
        "unit_of_analysis": args.unit_of_analysis,
        "frozen_before_outcomes": bool(args.freeze),
        "schedule_path": schedule_path,
        "schedule_hash": schedule_hash,
        "confirmatory_analyses": [
            {"analysis_id": f"A{i + 1}", "decision_rule": rule}
            for i, rule in enumerate(rules)
        ],
    }
    payload["plan_hash"] = content_hash(payload)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {args.output} plan_hash={payload['plan_hash']}")


if __name__ == "__main__":
    main()
