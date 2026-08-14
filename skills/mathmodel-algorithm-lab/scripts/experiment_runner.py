#!/usr/bin/env python3
"""Run explicit experiment commands concurrently under a named resource budget."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inside(root: Path, raw: str, *, exists: bool = False) -> Path:
    path = Path(raw)
    path = path if path.is_absolute() else root / path
    path = path.resolve(strict=exists)
    path.relative_to(root)
    return path


def resolve_argv(root: Path, argv: list[str]) -> list[str]:
    if not argv or not all(isinstance(item, str) and item for item in argv):
        raise ValueError("argv must be a non-empty string list")
    command = argv[0]
    if command in {"python", "python.exe"}:
        executable = sys.executable
        if len(argv) < 2 or argv[1] in {"-c", "-m", "-"}:
            raise ValueError(
                "Python runs must name an explicit .py file; -c, -m and stdin are disabled"
            )
        script = inside(root, argv[1], exists=True)
        if script.suffix.lower() != ".py" or not script.is_file():
            raise ValueError("Python run target must be a .py file inside project root")
        argv = [argv[0], str(script), *argv[2:]]
    elif command in {"matlab", "octave"}:
        executable = shutil.which(command)
        if not executable:
            raise ValueError(f"runtime is unavailable: {command}")
    else:
        candidate = inside(root, command, exists=True)
        if not candidate.is_file():
            raise ValueError(f"executable is not a file: {command}")
        executable = str(candidate)
    return [str(executable), *argv[1:]]


def run_one(
    root: Path, run: dict[str, Any], timeout: int, log_dir: Path
) -> dict[str, Any]:
    run_id = run["run_id"]
    command = resolve_argv(root, run["argv"])
    started = time.time_ns()
    before = {
        raw: (
            inside(root, raw).stat().st_mtime_ns if inside(root, raw).exists() else None
        )
        for raw in run.get("expected_artifacts", [])
    }
    begin = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "MATHMODEL_RUN_ID": run_id},
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        timed_out = True
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
    elapsed = time.perf_counter() - begin
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / f"{run_id}.stdout.log").write_text(stdout, encoding="utf-8")
    (log_dir / f"{run_id}.stderr.log").write_text(stderr, encoding="utf-8")
    artifacts = []
    for raw in run.get("expected_artifacts", []):
        path = inside(root, raw)
        fresh = (
            path.is_file()
            and path.stat().st_mtime_ns >= started
            and path.stat().st_mtime_ns != before[raw]
        )
        artifacts.append(
            {
                "path": raw,
                "exists": path.is_file(),
                "fresh": fresh,
                "sha256": digest(path) if path.is_file() else None,
            }
        )
    passed = exit_code == 0 and all(item["fresh"] for item in artifacts)
    return {
        "run_id": run_id,
        "argv": run["argv"],
        "exit_code": exit_code,
        "timed_out": timed_out,
        "elapsed_seconds": elapsed,
        "artifacts": artifacts,
        "status": "PASS" if passed else "FAIL",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--profiles", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.project_root.resolve(strict=True)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    profiles = json.loads(args.profiles.read_text(encoding="utf-8"))["profiles"]
    profile = profiles.get(plan.get("profile"))
    runs = plan.get("runs", [])
    if not profile or not isinstance(runs, list):
        print("ERROR: invalid profile or runs", file=sys.stderr)
        return 2
    if len(runs) > profile["max_runs"] or len(
        {run.get("run_id") for run in runs}
    ) != len(runs):
        print("ERROR: run budget exceeded or run IDs are not unique", file=sys.stderr)
        return 2
    workers = max(1, min(profile["max_workers"], os.cpu_count() or 1, len(runs) or 1))
    log_dir = args.output.parent / (args.output.stem + "-logs")
    results = []
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(run_one, root, run, profile["timeout_seconds"], log_dir)
                for run in runs
            ]
            for future in as_completed(futures):
                results.append(future.result())
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    results.sort(key=lambda item: item["run_id"])
    manifest = {
        "schema_version": 1,
        "profile": plan["profile"],
        "workers": workers,
        "resource_budget": profile,
        "plan_sha256": digest(args.plan),
        "runs": results,
        "status": "PASS"
        if all(item["status"] == "PASS" for item in results)
        else "FAIL",
    }
    output = inside(root, str(args.output), exists=False)
    if output.exists():
        print("ERROR: refusing to overwrite manifest", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"status": manifest["status"], "runs": len(results), "workers": workers}
        )
    )
    return 0 if manifest["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
