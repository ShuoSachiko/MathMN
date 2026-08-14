#!/usr/bin/env python3
"""Filter a transparent algorithm registry by a frozen problem profile."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument(
        "--variable", choices=("continuous", "discrete", "mixed"), required=True
    )
    parser.add_argument("--objective", choices=("single", "multi"), required=True)
    parser.add_argument(
        "--constraints", choices=("none", "linear", "nonlinear"), required=True
    )
    parser.add_argument(
        "--differentiable", choices=("yes", "no", "unknown"), required=True
    )
    parser.add_argument("--convex", choices=("yes", "no", "unknown"), required=True)
    parser.add_argument(
        "--evaluation-cost", choices=("low", "medium", "high"), required=True
    )
    parser.add_argument("--domain", default="mathematical modeling")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    registry = json.loads(args.registry.read_text(encoding="utf-8"))
    profile = {
        "variable": args.variable,
        "objective": args.objective,
        "constraints": args.constraints,
        "differentiable": args.differentiable,
        "convex": args.convex,
        "evaluation_cost": args.evaluation_cost,
    }
    mapping = {
        "variable": "variables",
        "objective": "objectives",
        "constraints": "constraints",
        "differentiable": "differentiable",
        "convex": "convex",
        "evaluation_cost": "evaluation_cost",
    }
    selected = []
    for algorithm in registry.get("algorithms", []):
        if all(
            profile[key] in algorithm.get(registry_key, [])
            for key, registry_key in mapping.items()
        ):
            selected.append(
                {key: algorithm[key] for key in ("id", "strength", "risk", "priority")}
            )
    selected.sort(key=lambda item: (-item["priority"], item["id"]))
    research_queries = []
    for item in selected:
        algorithm = item["id"].replace("-", " ")
        research_queries.extend(
            [
                f"{args.domain} {algorithm} constraints benchmark validation",
                f"{args.domain} {algorithm} failure modes parameter sensitivity",
            ]
        )
    document = {
        "schema_version": 1,
        "problem_profile": profile,
        "candidates": selected,
        "selection_status": "PROPOSED",
        "research_queries": research_queries,
        "note": "Candidates require task-specific literature evidence and validation.",
    }
    if args.output.exists():
        print("ERROR: refusing to overwrite candidate file", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
