#!/usr/bin/env python3
"""Vectorized feasibility-first particle swarm optimizer and synthetic benchmarks."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Callable

try:
    import numpy as np
except ModuleNotFoundError:
    print(
        "ERROR: PSO requires NumPy. Use the repository Python environment "
        "(backend/.venv/Scripts/python.exe on Windows) or install the declared project dependencies.",
        file=sys.stderr,
    )
    raise SystemExit(3)

Objective = Callable[[np.ndarray], np.ndarray | tuple[np.ndarray, np.ndarray]]


def sphere(x: np.ndarray) -> np.ndarray:
    return np.sum(x * x, axis=1)


def rastrigin(x: np.ndarray) -> np.ndarray:
    return 10 * x.shape[1] + np.sum(x * x - 10 * np.cos(2 * np.pi * x), axis=1)


def rosenbrock(x: np.ndarray) -> np.ndarray:
    return np.sum(100 * (x[:, 1:] - x[:, :-1] ** 2) ** 2 + (1 - x[:, :-1]) ** 2, axis=1)


BENCHMARKS: dict[str, Objective] = {
    "sphere": sphere,
    "rastrigin": rastrigin,
    "rosenbrock": rosenbrock,
}


def load_objective(path: Path, function_name: str) -> Objective:
    spec = importlib.util.spec_from_file_location("mathmodel_objective", path)
    if spec is None or spec.loader is None:
        raise ValueError(f"cannot load objective module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    function = getattr(module, function_name, None)
    if not callable(function):
        raise ValueError(f"objective module has no callable {function_name}")
    return function


def evaluate(
    function: Objective, positions: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    raw = function(positions)
    if isinstance(raw, tuple):
        objective, violation = raw
    else:
        objective, violation = raw, np.zeros(positions.shape[0])
    objective = np.asarray(objective, dtype=float).reshape(-1)
    violation = np.maximum(np.asarray(violation, dtype=float).reshape(-1), 0)
    if len(objective) != positions.shape[0] or len(violation) != positions.shape[0]:
        raise ValueError("objective must return one value and violation per particle")
    if not np.all(np.isfinite(objective)) or not np.all(np.isfinite(violation)):
        raise ValueError("objective returned non-finite values")
    return objective, violation


def better(
    obj_a: np.ndarray,
    vio_a: np.ndarray,
    obj_b: np.ndarray,
    vio_b: np.ndarray,
    tolerance: float,
) -> np.ndarray:
    feasible_a = vio_a <= tolerance
    feasible_b = vio_b <= tolerance
    return (
        (feasible_a & ~feasible_b)
        | (feasible_a & feasible_b & (obj_a < obj_b))
        | (~feasible_a & ~feasible_b & (vio_a < vio_b))
    )


def optimize(
    function: Objective,
    lower: np.ndarray,
    upper: np.ndarray,
    *,
    particles: int,
    iterations: int,
    seed: int,
    inertia: float,
    cognitive: float,
    social: float,
    velocity_fraction: float,
    tolerance: float,
) -> dict[str, object]:
    if np.any(upper <= lower):
        raise ValueError("every upper bound must exceed its lower bound")
    rng = np.random.default_rng(seed)
    dimension = len(lower)
    span = upper - lower
    position = rng.uniform(lower, upper, size=(particles, dimension))
    velocity = rng.uniform(
        -velocity_fraction * span, velocity_fraction * span, size=(particles, dimension)
    )
    p_obj, p_vio = evaluate(function, position)
    p_position = position.copy()
    best_index = min(
        range(particles), key=lambda i: (p_vio[i] > tolerance, p_vio[i], p_obj[i])
    )
    g_position = p_position[best_index].copy()
    g_obj = float(p_obj[best_index])
    g_vio = float(p_vio[best_index])
    history = [{"iteration": 0, "objective": g_obj, "violation": g_vio}]
    for iteration in range(1, iterations + 1):
        r1 = rng.random((particles, dimension))
        r2 = rng.random((particles, dimension))
        velocity = (
            inertia * velocity
            + cognitive * r1 * (p_position - position)
            + social * r2 * (g_position - position)
        )
        vmax = velocity_fraction * span
        velocity = np.clip(velocity, -vmax, vmax)
        position = position + velocity
        below = position < lower
        above = position > upper
        position = np.clip(position, lower, upper)
        velocity[below | above] *= -0.5
        objective, violation = evaluate(function, position)
        mask = better(objective, violation, p_obj, p_vio, tolerance)
        p_position[mask] = position[mask]
        p_obj[mask] = objective[mask]
        p_vio[mask] = violation[mask]
        best_index = min(
            range(particles), key=lambda i: (p_vio[i] > tolerance, p_vio[i], p_obj[i])
        )
        if bool(
            better(
                np.array([p_obj[best_index]]),
                np.array([p_vio[best_index]]),
                np.array([g_obj]),
                np.array([g_vio]),
                tolerance,
            )[0]
        ):
            g_position = p_position[best_index].copy()
            g_obj = float(p_obj[best_index])
            g_vio = float(p_vio[best_index])
        history.append({"iteration": iteration, "objective": g_obj, "violation": g_vio})
    return {
        "best_position": g_position.tolist(),
        "objective": g_obj,
        "violation": g_vio,
        "feasible": g_vio <= tolerance,
        "evaluations": particles * (iterations + 1),
        "history": history,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--benchmark", choices=sorted(BENCHMARKS))
    source.add_argument("--objective-module", type=Path)
    parser.add_argument("--objective-function", default="evaluate")
    parser.add_argument("--dimension", type=int, required=True)
    parser.add_argument("--lower", type=float, action="append", required=True)
    parser.add_argument("--upper", type=float, action="append", required=True)
    parser.add_argument("--particles", type=int, default=40)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--inertia", type=float, default=0.7298)
    parser.add_argument("--cognitive", type=float, default=1.49618)
    parser.add_argument("--social", type=float, default=1.49618)
    parser.add_argument("--velocity-fraction", type=float, default=0.2)
    parser.add_argument("--feasibility-tolerance", type=float, default=1e-9)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.dimension < 1 or args.particles < 2 or args.iterations < 1:
        print("ERROR: invalid dimension, particles, or iterations", file=sys.stderr)
        return 2
    lower_values = args.lower * args.dimension if len(args.lower) == 1 else args.lower
    upper_values = args.upper * args.dimension if len(args.upper) == 1 else args.upper
    if len(lower_values) != args.dimension or len(upper_values) != args.dimension:
        print(
            "ERROR: bounds must be scalar or repeated once per dimension",
            file=sys.stderr,
        )
        return 2
    try:
        function = (
            BENCHMARKS[args.benchmark]
            if args.benchmark
            else load_objective(
                args.objective_module.resolve(strict=True), args.objective_function
            )
        )
        result = optimize(
            function,
            np.array(lower_values),
            np.array(upper_values),
            particles=args.particles,
            iterations=args.iterations,
            seed=args.seed,
            inertia=args.inertia,
            cognitive=args.cognitive,
            social=args.social,
            velocity_fraction=args.velocity_fraction,
            tolerance=args.feasibility_tolerance,
        )
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    result.update(
        {
            "schema_version": 1,
            "algorithm": "global-best-pso",
            "claim_strength": "best-found-in-this-run",
            "benchmark": args.benchmark,
            "seed": args.seed,
            "dimension": args.dimension,
            "particles": args.particles,
            "iterations": args.iterations,
            "bounds": {"lower": lower_values, "upper": upper_values},
            "parameters": {
                "inertia": args.inertia,
                "cognitive": args.cognitive,
                "social": args.social,
                "velocity_fraction": args.velocity_fraction,
                "feasibility_tolerance": args.feasibility_tolerance,
            },
        }
    )
    if args.output.exists():
        print("ERROR: refusing to overwrite output", file=sys.stderr)
        return 2
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "objective": result["objective"],
                "violation": result["violation"],
                "feasible": result["feasible"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
