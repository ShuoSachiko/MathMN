#!/usr/bin/env python3
"""Aggregate auditable cross-seed statistics from an experiment manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root: Path, raw: str, *, existing: bool = True) -> Path:
    path = Path(raw)
    path = path if path.is_absolute() else root / path
    path = path.resolve(strict=existing)
    path.relative_to(root)
    return path


def percentile(values: list[float], proportion: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = proportion * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--feasibility-tolerance", type=float, default=1e-8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve(strict=True)
    manifest_path = inside(root, str(args.manifest))
    output = inside(root, str(args.output), existing=False)
    if output.exists():
        print("ERROR: refusing to overwrite aggregate output", file=sys.stderr)
        return 2
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    groups: dict[str, dict[str, Any]] = {}
    for run in manifest.get("runs", []):
        record: dict[str, Any] | None = None
        for artifact in run.get("artifacts", []):
            if not artifact.get("fresh") or not str(artifact.get("path", "")).endswith(
                ".json"
            ):
                continue
            candidate = inside(root, str(artifact["path"]))
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(value, dict) and isinstance(
                value.get("objective"), (int, float)
            ):
                record = value
                break
        algorithm = str(record.get("algorithm", "unknown")) if record else "unparsed"
        group = groups.setdefault(
            algorithm,
            {
                "run_count": 0,
                "pass_count": 0,
                "objectives": [],
                "violations": [],
                "elapsed": [],
            },
        )
        group["run_count"] += 1
        group["pass_count"] += int(run.get("status") == "PASS")
        group["elapsed"].append(float(run.get("elapsed_seconds", 0)))
        if record:
            group["objectives"].append(float(record["objective"]))
            group["violations"].append(float(record.get("violation", 0)))
    summaries = []
    for algorithm, group in sorted(groups.items()):
        objectives = group.pop("objectives")
        violations = group.pop("violations")
        elapsed = group.pop("elapsed")
        feasible = sum(value <= args.feasibility_tolerance for value in violations)
        summaries.append(
            {
                "algorithm": algorithm,
                **group,
                "numeric_result_count": len(objectives),
                "feasible_count": feasible,
                "feasible_rate": feasible / len(violations) if violations else None,
                "objective_best": min(objectives) if objectives else None,
                "objective_median": statistics.median(objectives)
                if objectives
                else None,
                "objective_q25": percentile(objectives, 0.25),
                "objective_q75": percentile(objectives, 0.75),
                "elapsed_median_seconds": statistics.median(elapsed)
                if elapsed
                else None,
            }
        )
    report = {
        "schema_version": 1,
        "kind": "EXPERIMENT_AGGREGATE",
        "manifest_sha256": digest(manifest_path),
        "feasibility_tolerance": args.feasibility_tolerance,
        "algorithms": summaries,
        "claim_strength": "descriptive-statistics-for-recorded-runs",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output.relative_to(root).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
