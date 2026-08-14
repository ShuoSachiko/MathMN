#!/usr/bin/env python3
"""Track the 72-hour competition budget and flag schedule drift early.

`plan.md` records the total budget and per-stage allocation once; `mark`
updates progress after each stage; `status` compares planned allocation,
actual spend, and wall-clock remaining time. Warnings are advisory (never
waive contract, reproduction, or human checkpoints) but should trigger the
degradation strategy recorded in plan.md.

Storage: reports/TIME_BUDGET.json (append-safe: mark overwrites only the
touched stage fields and updates updated_at).

Exit codes: 0 = on schedule, 1 = warnings (behind/over budget), 2 = usage or
state error.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

STATE_REL = Path("reports") / "TIME_BUDGET.json"


def _load(root: Path) -> dict:
    path = root / STATE_REL
    if not path.is_file():
        print("ERROR: no TIME_BUDGET.json; run `init` first", file=sys.stderr)
        raise SystemExit(2)
    return json.loads(path.read_text(encoding="utf-8"))


def _save(root: Path, state: dict) -> None:
    path = root / STATE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def cmd_init(args) -> int:
    root: Path = args.root
    path = root / STATE_REL
    if path.exists():
        print("ERROR: refusing to overwrite existing TIME_BUDGET.json", file=sys.stderr)
        return 2
    stages: dict[str, dict] = {}
    for token in args.stage_budget or []:
        if "=" not in token:
            print(f"ERROR: bad --stage-budget token {token!r}", file=sys.stderr)
            return 2
        name, hours = token.split("=", 1)
        try:
            budget = float(hours)
        except ValueError:
            print(f"ERROR: bad budget for stage {name!r}", file=sys.stderr)
            return 2
        stages[name] = {
            "budget_hours": budget,
            "spent_hours": 0.0,
            "percent": 0,
            "note": "",
            "updated_at": "",
        }
    deadline = datetime.fromisoformat(args.deadline)
    state = {
        "schema_version": 1,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "deadline": deadline.isoformat(timespec="seconds"),
        "budget_hours": args.budget_hours,
        "stages": stages,
    }
    _save(root, state)
    print(path)
    return 0


def cmd_mark(args) -> int:
    root: Path = args.root
    state = _load(root)
    if args.stage not in state["stages"]:
        print(
            f"ERROR: unknown stage {args.stage!r}; known: {sorted(state['stages'])}",
            file=sys.stderr,
        )
        return 2
    if args.percent < 0 or args.percent > 100:
        print("ERROR: --percent must be within [0, 100]", file=sys.stderr)
        return 2
    stage = state["stages"][args.stage]
    stage["spent_hours"] = round(args.spent_hours, 2)
    stage["percent"] = args.percent
    stage["note"] = args.note or stage.get("note", "")
    stage["updated_at"] = datetime.now().isoformat(timespec="seconds")
    _save(root, state)
    print(f"marked {args.stage}: {args.percent}% at {stage['spent_hours']}h")
    return 0


def cmd_status(args) -> int:
    root: Path = args.root
    state = _load(root)
    warnings: list[str] = []
    total_spent = sum(s["spent_hours"] for s in state["stages"].values())
    total_percent = sum(s["percent"] for s in state["stages"].values())
    print(f"budget: {state['budget_hours']}h | spent: {total_spent:.1f}h | total progress: {total_percent}%")

    try:
        deadline = datetime.fromisoformat(state["deadline"])
        remaining_wall = deadline - datetime.now()
    except ValueError:
        remaining_wall = timedelta(0)
    remaining_budget = state["budget_hours"] - total_spent
    print(f"deadline: {state['deadline']} | wall remaining: {remaining_wall} | budget remaining: {remaining_budget:.1f}h")

    if remaining_budget <= 0:
        warnings.append("over budget: spent hours exceed the declared budget")
    if remaining_wall.total_seconds() <= 0:
        warnings.append("deadline already passed")
    elif remaining_wall.total_seconds() / 3600 < remaining_budget:
        warnings.append(
            "wall clock is tighter than the remaining budget; "
            "apply the degradation strategy from plan.md now"
        )

    for name, stage in sorted(state["stages"].items()):
        budget = stage.get("budget_hours") or 1e-9
        expected = stage["spent_hours"] / budget * 100 if budget else 0
        flag = ""
        if stage["percent"] < expected - 10:
            flag = "  <-- BEHIND (progress below spend ratio)"
            warnings.append(f"stage {name}: behind schedule")
        print(
            f"  {name}: {stage['percent']}% | {stage['spent_hours']:.1f}h/"
            f"{stage['budget_hours']}h{flag}"
        )

    for warning in warnings:
        print(f"WARN: {warning}")
    if warnings:
        print("schedule drift detected; consult plan.md degradation strategy")
        return 1
    print("on schedule")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    init_parser = sub.add_parser("init", help="create TIME_BUDGET.json")
    init_parser.add_argument("--root", type=Path, default=Path("."))
    init_parser.add_argument("--deadline", required=True, help="ISO datetime, e.g. 2026-09-10T18:00")
    init_parser.add_argument("--budget-hours", type=float, required=True)
    init_parser.add_argument(
        "--stage-budget", action="append", metavar="NAME=HOURS",
        help="repeatable, e.g. --stage-budget analysis=10",
    )
    init_parser.set_defaults(func=cmd_init)

    mark_parser = sub.add_parser("mark", help="update one stage")
    mark_parser.add_argument("--root", type=Path, default=Path("."))
    mark_parser.add_argument("--stage", required=True)
    mark_parser.add_argument("--spent-hours", type=float, required=True)
    mark_parser.add_argument("--percent", type=int, required=True)
    mark_parser.add_argument("--note", default="")
    mark_parser.set_defaults(func=cmd_mark)

    status_parser = sub.add_parser("status", help="compare spend/progress/deadline")
    status_parser.add_argument("--root", type=Path, default=Path("."))
    status_parser.set_defaults(func=cmd_status)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
