#!/usr/bin/env python3
"""Detect local compute capacity without installing or changing drivers."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
from pathlib import Path


def nvidia() -> dict[str, object]:
    executable = shutil.which("nvidia-smi")
    if not executable:
        return {"available": False, "gpus": []}
    completed = subprocess.run(
        [
            executable,
            "--query-gpu=name,memory.total,driver_version",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    gpus = []
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            parts = [part.strip() for part in line.split(",")]
            if len(parts) == 3:
                gpus.append(
                    {"name": parts[0], "memory_mib": int(parts[1]), "driver": parts[2]}
                )
    return {"available": bool(gpus), "gpus": gpus, "exit_code": completed.returncode}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = {
        "schema_version": 1,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "logical_cpus": os.cpu_count(),
        "nvidia": nvidia(),
        "matlab_on_path": bool(shutil.which("matlab")),
        "octave_on_path": bool(shutil.which("octave")),
        "note": "Detection does not prove that a workload can use the GPU.",
    }
    if args.output.exists():
        raise SystemExit("refusing to overwrite output")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
