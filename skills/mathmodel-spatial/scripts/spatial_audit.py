#!/usr/bin/env python3
"""Deterministic audits for spatial modeling inputs and claims."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import sys
from collections import defaultdict, deque
from pathlib import Path
from typing import Any


class SpatialError(ValueError):
    pass


def write_report(path: Path, report: dict[str, Any]) -> int:
    if path.exists():
        raise SpatialError(f"refusing to overwrite report: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    failures = [
        item for item in report.get("findings", []) if item.get("severity") == "FAIL"
    ]
    report["status"] = "FAIL" if failures else "PASS"
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "status": report["status"],
                "output": str(path),
                "failures": len(failures),
            },
            ensure_ascii=False,
        )
    )
    return 1 if failures else 0


def finding(severity: str, code: str, message: str, **details: Any) -> dict[str, Any]:
    return {"severity": severity, "code": code, "message": message, "details": details}


def read_contract(path: Path) -> dict[str, Any]:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SpatialError(f"cannot read spatial contract: {exc}") from exc
    required = {
        "coordinate_system",
        "dimension",
        "axes",
        "unit",
        "distance_metric",
        "tolerance",
    }
    missing = required - set(contract)
    if missing:
        raise SpatialError(f"spatial contract missing: {sorted(missing)}")
    return contract


def command_init(args: argparse.Namespace) -> int:
    axes = args.axis
    if len(axes) != args.dimension or len(set(axes)) != len(axes):
        raise SpatialError("--axis count must equal dimension and axes must be unique")
    if args.coordinate_system == "geographic" and args.dimension != 2:
        raise SpatialError("geographic contracts currently require dimension 2")
    document = {
        "schema_version": 1,
        "coordinate_system": args.coordinate_system,
        "dimension": args.dimension,
        "axes": axes,
        "unit": args.unit,
        "distance_metric": args.distance_metric,
        "tolerance": args.tolerance,
        "crs": args.crs,
        "vertical_datum": args.vertical_datum,
        "origin": args.origin,
        "angle_unit": args.angle_unit,
        "axis_order_verified_by_human": False,
    }
    if args.output.exists():
        raise SpatialError(f"refusing to overwrite contract: {args.output}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(args.output)
    return 0


def read_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            raise SpatialError("CSV has no header")
        return list(reader.fieldnames), list(reader)


def finite(value: str, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise SpatialError(f"{label} is not numeric: {value!r}") from exc
    if not math.isfinite(number):
        raise SpatialError(f"{label} is not finite: {value!r}")
    return number


def load_points(
    path: Path, id_col: str, coords: list[str], weight: str | None = None
) -> tuple[list[str], list[list[float]], list[float], list[dict[str, Any]]]:
    fields, rows = read_rows(path)
    required = {id_col, *coords}
    if weight:
        required.add(weight)
    missing = required - set(fields)
    if missing:
        raise SpatialError(f"CSV missing columns: {sorted(missing)}")
    ids: list[str] = []
    points: list[list[float]] = []
    weights: list[float] = []
    findings: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):
        item_id = (row.get(id_col) or "").strip()
        if not item_id:
            findings.append(finding("FAIL", "EMPTY_ID", f"row {index} has an empty ID"))
            item_id = f"__row_{index}"
        try:
            point = [
                finite(row.get(axis, ""), f"row {index} column {axis}")
                for axis in coords
            ]
            item_weight = finite(
                row.get(weight, "1") if weight else "1", f"row {index} weight"
            )
        except SpatialError as exc:
            findings.append(finding("FAIL", "NON_NUMERIC", str(exc)))
            continue
        if item_weight < 0:
            findings.append(
                finding("FAIL", "NEGATIVE_WEIGHT", f"row {index} has negative weight")
            )
        ids.append(item_id)
        points.append(point)
        weights.append(item_weight)
    return ids, points, weights, findings


def validate_coords(contract: dict[str, Any], coords: list[str]) -> None:
    if coords != contract["axes"]:
        raise SpatialError(
            f"coordinate columns {coords} do not exactly match contract axes {contract['axes']}"
        )


def command_points(args: argparse.Namespace) -> int:
    contract = read_contract(args.contract)
    validate_coords(contract, args.coord)
    ids, points, _, findings = load_points(args.csv, args.id, args.coord)
    duplicates = sorted({value for value in ids if ids.count(value) > 1})
    if duplicates:
        findings.append(
            finding(
                "FAIL", "DUPLICATE_ID", "point IDs are not unique", ids=duplicates[:50]
            )
        )
    coord_groups: dict[tuple[float, ...], list[str]] = defaultdict(list)
    for item_id, point in zip(ids, points):
        coord_groups[tuple(point)].append(item_id)
    repeated = {
        str(key): value for key, value in coord_groups.items() if len(value) > 1
    }
    if repeated:
        findings.append(
            finding(
                "WARN",
                "DUPLICATE_COORDINATE",
                "multiple IDs share coordinates",
                groups=repeated,
            )
        )
    if contract["coordinate_system"] == "geographic":
        for item_id, point in zip(ids, points):
            lon, lat = point
            if not -180 <= lon <= 180 or not -90 <= lat <= 90:
                findings.append(
                    finding(
                        "FAIL",
                        "GEOGRAPHIC_RANGE",
                        f"{item_id} is outside longitude/latitude range",
                        coordinate=point,
                    )
                )
        if contract["distance_metric"] == "euclidean":
            findings.append(
                finding(
                    "FAIL",
                    "GEOGRAPHIC_EUCLIDEAN",
                    "raw geographic angles must not use Euclidean distance",
                )
            )
    bounds = (
        []
        if not points
        else [
            {
                "axis": axis,
                "min": min(p[i] for p in points),
                "max": max(p[i] for p in points),
            }
            for i, axis in enumerate(args.coord)
        ]
    )
    report = {
        "schema_version": 1,
        "audit": "points",
        "source": str(args.csv),
        "contract": str(args.contract),
        "count": len(points),
        "bounds": bounds,
        "findings": findings,
    }
    return write_report(args.output, report)


def read_matrix(path: Path, labels: bool) -> tuple[list[str], list[list[float]]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.reader(stream))
    if not rows:
        raise SpatialError("matrix CSV is empty")
    names: list[str]
    data_rows: list[list[str]]
    if labels:
        names = [cell.strip() for cell in rows[0][1:]]
        data_rows = [row[1:] for row in rows[1:]]
        row_names = [row[0].strip() for row in rows[1:]]
        if names != row_names:
            raise SpatialError("matrix row and column labels differ")
    else:
        data_rows = rows
        names = [str(i) for i in range(len(rows))]
    matrix = [
        [finite(cell, f"matrix[{i},{j}]") for j, cell in enumerate(row)]
        for i, row in enumerate(data_rows)
    ]
    if any(len(row) != len(matrix) for row in matrix):
        raise SpatialError("matrix must be square")
    return names, matrix


def command_distance(args: argparse.Namespace) -> int:
    names, matrix = read_matrix(args.csv, args.labels)
    n = len(matrix)
    findings: list[dict[str, Any]] = []
    negatives = [
        (i, j, matrix[i][j])
        for i in range(n)
        for j in range(n)
        if matrix[i][j] < -args.tolerance
    ]
    diagonal = [
        (i, matrix[i][i]) for i in range(n) if abs(matrix[i][i]) > args.tolerance
    ]
    asymmetry = [
        (i, j, abs(matrix[i][j] - matrix[j][i]))
        for i in range(n)
        for j in range(i + 1, n)
        if abs(matrix[i][j] - matrix[j][i]) > args.tolerance
    ]
    if negatives:
        findings.append(
            finding(
                "FAIL",
                "NEGATIVE_DISTANCE",
                "distance matrix has negative entries",
                examples=negatives[:20],
            )
        )
    if diagonal:
        findings.append(
            finding(
                "FAIL",
                "NONZERO_DIAGONAL",
                "distance diagonal is not zero",
                examples=diagonal[:20],
            )
        )
    if asymmetry:
        findings.append(
            finding(
                "FAIL",
                "ASYMMETRIC_DISTANCE",
                "distance matrix is not symmetric",
                examples=asymmetry[:20],
            )
        )
    if n <= args.full_triangle_limit:
        triples = [(i, j, k) for i in range(n) for j in range(n) for k in range(n)]
    else:
        rng = random.Random(0)
        triples = [
            tuple(rng.randrange(n) for _ in range(3))
            for _ in range(args.triangle_samples)
        ]
    triangle = [
        (i, j, k, matrix[i][k] - matrix[i][j] - matrix[j][k])
        for i, j, k in triples
        if matrix[i][k] > matrix[i][j] + matrix[j][k] + args.tolerance
    ]
    if triangle:
        findings.append(
            finding(
                "FAIL",
                "TRIANGLE_INEQUALITY",
                "distance matrix violates the triangle inequality",
                examples=triangle[:20],
                checked=len(triples),
            )
        )
    report = {
        "schema_version": 1,
        "audit": "distance",
        "source": str(args.csv),
        "size": n,
        "labels": names,
        "triangle_checks": len(triples),
        "findings": findings,
    }
    return write_report(args.output, report)


def command_adjacency(args: argparse.Namespace) -> int:
    names, matrix = read_matrix(args.csv, args.labels)
    n = len(matrix)
    findings: list[dict[str, Any]] = []
    invalid = [
        (i, j, value)
        for i, row in enumerate(matrix)
        for j, value in enumerate(row)
        if value not in (0.0, 1.0)
    ]
    if invalid:
        findings.append(
            finding(
                "FAIL",
                "NON_BINARY_ADJACENCY",
                "adjacency entries must be 0 or 1",
                examples=invalid[:20],
            )
        )
    if not args.allow_self_loops and any(matrix[i][i] != 0 for i in range(n)):
        findings.append(
            finding("FAIL", "SELF_LOOP", "adjacency diagonal contains self loops")
        )
    if not args.directed and any(
        matrix[i][j] != matrix[j][i] for i in range(n) for j in range(i + 1, n)
    ):
        findings.append(
            finding(
                "FAIL",
                "ASYMMETRIC_ADJACENCY",
                "undirected adjacency matrix is not symmetric",
            )
        )
    components: list[list[str]] = []
    if not args.directed and not invalid:
        unseen = set(range(n))
        while unseen:
            start = unseen.pop()
            queue = deque([start])
            component = [start]
            while queue:
                i = queue.popleft()
                for j, value in enumerate(matrix[i]):
                    if value == 1 and j in unseen:
                        unseen.remove(j)
                        queue.append(j)
                        component.append(j)
            components.append([names[i] for i in component])
        if len(components) > 1:
            findings.append(
                finding(
                    "WARN",
                    "DISCONNECTED_GRAPH",
                    "graph has multiple connected components",
                    components=components,
                )
            )
    report = {
        "schema_version": 1,
        "audit": "adjacency",
        "source": str(args.csv),
        "size": n,
        "directed": args.directed,
        "component_count": len(components) if components else None,
        "findings": findings,
    }
    return write_report(args.output, report)


def distance(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def command_trajectory(args: argparse.Namespace) -> int:
    contract = read_contract(args.contract)
    validate_coords(contract, args.coord)
    fields, rows = read_rows(args.csv)
    missing = {args.id, args.time, *args.coord} - set(fields)
    if missing:
        raise SpatialError(f"CSV missing columns: {sorted(missing)}")
    tracks: dict[str, list[tuple[float, list[float], int]]] = defaultdict(list)
    findings: list[dict[str, Any]] = []
    for row_number, row in enumerate(rows, 2):
        try:
            tracks[(row.get(args.id) or "").strip()].append(
                (
                    finite(row.get(args.time, ""), f"row {row_number} time"),
                    [
                        finite(row.get(axis, ""), f"row {row_number} {axis}")
                        for axis in args.coord
                    ],
                    row_number,
                )
            )
        except SpatialError as exc:
            findings.append(finding("FAIL", "INVALID_TRAJECTORY_ROW", str(exc)))
    max_observed = 0.0
    for item_id, samples in tracks.items():
        for (t0, p0, r0), (t1, p1, r1) in zip(samples, samples[1:]):
            dt = t1 - t0
            if dt <= 0:
                findings.append(
                    finding(
                        "FAIL",
                        "NON_INCREASING_TIME",
                        f"trajectory {item_id} time is not strictly increasing",
                        rows=[r0, r1],
                        delta_time=dt,
                    )
                )
                continue
            speed = distance(p0, p1) / dt
            max_observed = max(max_observed, speed)
            if (
                args.max_speed is not None
                and speed > args.max_speed + contract["tolerance"]
            ):
                findings.append(
                    finding(
                        "FAIL",
                        "SPEED_LIMIT",
                        f"trajectory {item_id} exceeds declared speed",
                        rows=[r0, r1],
                        speed=speed,
                        limit=args.max_speed,
                    )
                )
    report = {
        "schema_version": 1,
        "audit": "trajectory",
        "source": str(args.csv),
        "track_count": len(tracks),
        "sample_count": sum(map(len, tracks.values())),
        "max_observed_speed": max_observed,
        "findings": findings,
    }
    return write_report(args.output, report)


def command_coverage(args: argparse.Namespace) -> int:
    contract = read_contract(args.contract)
    validate_coords(contract, args.coord)
    if (
        contract["coordinate_system"] == "geographic"
        or contract["distance_metric"] != "euclidean"
    ):
        raise SpatialError(
            "coverage audit supports only Euclidean cartesian/projected contracts"
        )
    demand_ids, demands, weights, findings = load_points(
        args.demand, args.demand_id, args.coord, args.weight
    )
    facility_ids, facilities, _, facility_findings = load_points(
        args.facility, args.facility_id, args.coord
    )
    findings.extend(facility_findings)
    pairs = len(demands) * len(facilities)
    if pairs > args.max_pairs:
        raise SpatialError(
            f"coverage requires {pairs} pairs, above --max-pairs={args.max_pairs}"
        )
    cover_counts = [
        sum(
            distance(point, site) <= args.radius + contract["tolerance"]
            for site in facilities
        )
        for point in demands
    ]
    total_weight = sum(weights)
    covered_weight = sum(
        weight for weight, count in zip(weights, cover_counts) if count > 0
    )
    uncovered = [
        item_id for item_id, count in zip(demand_ids, cover_counts) if count == 0
    ]
    if uncovered:
        findings.append(
            finding(
                "WARN",
                "UNCOVERED_DEMAND",
                "some demand points are uncovered",
                ids=uncovered[:200],
                count=len(uncovered),
            )
        )
    report = {
        "schema_version": 1,
        "audit": "coverage",
        "demand_source": str(args.demand),
        "facility_source": str(args.facility),
        "facility_ids": facility_ids,
        "radius": args.radius,
        "pair_checks": pairs,
        "demand_count": len(demands),
        "covered_count": sum(count > 0 for count in cover_counts),
        "weighted_coverage": covered_weight / total_weight
        if total_weight > 0
        else None,
        "min_redundancy": min(cover_counts, default=0),
        "max_redundancy": max(cover_counts, default=0),
        "uncovered_ids": uncovered,
        "scope": "discrete demand points only",
        "findings": findings,
    }
    return write_report(args.output, report)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-contract")
    init.add_argument("--output", type=Path, required=True)
    init.add_argument(
        "--coordinate-system",
        choices=("cartesian", "projected", "geographic"),
        required=True,
    )
    init.add_argument("--dimension", type=int, choices=(2, 3), required=True)
    init.add_argument("--axis", action="append", default=[], required=True)
    init.add_argument("--unit", required=True)
    init.add_argument("--distance-metric", required=True)
    init.add_argument("--tolerance", type=float, default=1e-9)
    init.add_argument("--crs", default="unknown")
    init.add_argument("--vertical-datum", default="unknown")
    init.add_argument("--origin", default="unknown")
    init.add_argument(
        "--angle-unit",
        choices=("degree", "radian", "not-applicable"),
        default="not-applicable",
    )
    init.set_defaults(handler=command_init)
    points = commands.add_parser("points")
    points.add_argument("--contract", type=Path, required=True)
    points.add_argument("--csv", type=Path, required=True)
    points.add_argument("--id", required=True)
    points.add_argument("--coord", action="append", default=[], required=True)
    points.add_argument("--output", type=Path, required=True)
    points.set_defaults(handler=command_points)
    dist = commands.add_parser("distance")
    dist.add_argument("--csv", type=Path, required=True)
    dist.add_argument("--labels", action="store_true")
    dist.add_argument("--tolerance", type=float, default=1e-9)
    dist.add_argument("--full-triangle-limit", type=int, default=120)
    dist.add_argument("--triangle-samples", type=int, default=100000)
    dist.add_argument("--output", type=Path, required=True)
    dist.set_defaults(handler=command_distance)
    adj = commands.add_parser("adjacency")
    adj.add_argument("--csv", type=Path, required=True)
    adj.add_argument("--labels", action="store_true")
    adj.add_argument("--directed", action="store_true")
    adj.add_argument("--allow-self-loops", action="store_true")
    adj.add_argument("--output", type=Path, required=True)
    adj.set_defaults(handler=command_adjacency)
    trajectory = commands.add_parser("trajectory")
    trajectory.add_argument("--contract", type=Path, required=True)
    trajectory.add_argument("--csv", type=Path, required=True)
    trajectory.add_argument("--id", required=True)
    trajectory.add_argument("--time", required=True)
    trajectory.add_argument("--coord", action="append", default=[], required=True)
    trajectory.add_argument("--max-speed", type=float)
    trajectory.add_argument("--output", type=Path, required=True)
    trajectory.set_defaults(handler=command_trajectory)
    coverage = commands.add_parser("coverage")
    coverage.add_argument("--contract", type=Path, required=True)
    coverage.add_argument("--demand", type=Path, required=True)
    coverage.add_argument("--facility", type=Path, required=True)
    coverage.add_argument("--demand-id", required=True)
    coverage.add_argument("--facility-id", required=True)
    coverage.add_argument("--coord", action="append", default=[], required=True)
    coverage.add_argument("--radius", type=float, required=True)
    coverage.add_argument("--weight")
    coverage.add_argument("--max-pairs", type=int, default=5_000_000)
    coverage.add_argument("--output", type=Path, required=True)
    coverage.set_defaults(handler=command_coverage)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except (SpatialError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
