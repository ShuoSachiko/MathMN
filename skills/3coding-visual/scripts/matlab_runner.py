#!/usr/bin/env python3
"""Detect and run MATLAB/Octave scripts with project-local state and logs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

MATLAB_COMPLETION_MARKER = "__MATHMODELAGENT_SCRIPT_COMPLETED__"


def _windows_matlab_candidates() -> list[Path]:
    roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
    candidates: list[Path] = []
    for root in filter(None, roots):
        matlab_root = Path(root) / "MATLAB"
        if matlab_root.is_dir():
            candidates.extend(matlab_root.glob("R*/bin/matlab.exe"))
    return sorted(candidates, reverse=True)


def find_runtime(preference: str = "auto") -> tuple[str, Path] | None:
    """Return the requested runtime name and executable path."""
    choices = [preference] if preference != "auto" else ["matlab", "octave"]
    for name in choices:
        env_name = f"{name.upper()}_EXECUTABLE"
        configured = os.environ.get(env_name)
        if configured and Path(configured).is_file():
            return name, Path(configured).resolve()

        located = shutil.which(name)
        if located:
            return name, Path(located).resolve()

        if name == "matlab" and os.name == "nt":
            candidates = _windows_matlab_candidates()
            if candidates:
                return name, candidates[0].resolve()
    return None


def _matlab_expression(script: Path) -> str:
    escaped = script.as_posix().replace("'", "''")
    return f"run('{escaped}'); fprintf('{MATLAB_COMPLETION_MARKER}\\n')"


def _octave_expression(script: Path) -> str:
    escaped = script.as_posix().replace("'", "''")
    return (
        "try; "
        f"run('{escaped}'); "
        f"fprintf('{MATLAB_COMPLETION_MARKER}\\n'); "
        "catch err; disp(err.message); "
        "for k = 1:numel(err.stack), "
        "disp([err.stack(k).file ':' num2str(err.stack(k).line)]); end; "
        "exit(1); end; exit(0);"
    )


def run_script(
    runtime_name: str,
    executable: Path,
    script: Path,
    project_root: Path,
    timeout: int,
    preferences: Path,
) -> subprocess.CompletedProcess[str]:
    """Execute a script without invoking a shell."""
    environment = os.environ.copy()
    preferences.mkdir(parents=True, exist_ok=True)
    environment["MATLAB_PREFDIR"] = str(preferences)

    if runtime_name == "matlab":
        command = [
            str(executable),
            "-noFigureWindows",
            "-batch",
            _matlab_expression(script),
        ]
    else:
        command = [
            str(executable),
            "--quiet",
            "--no-gui",
            "--no-init-file",
            "--eval",
            _octave_expression(script),
        ]

    return subprocess.run(
        command,
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detect MATLAB/Octave or run a reproducible .m script."
    )
    parser.add_argument("script", nargs="?", type=Path, help="Path to a .m file")
    parser.add_argument(
        "--runtime", choices=("auto", "matlab", "octave"), default="auto"
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=int, default=1800)
    parser.add_argument("--log", type=Path, help="Optional UTF-8 execution log")
    parser.add_argument(
        "--expected-artifact",
        action="append",
        default=[],
        type=Path,
        help="Artifact that must be created or refreshed by this run; repeat as needed",
    )
    parser.add_argument(
        "--require-result",
        action="store_true",
        help="Require at least one stdout line beginning with 'RESULT '",
    )
    parser.add_argument(
        "--check", action="store_true", help="Only report runtime availability as JSON"
    )
    return parser


def _resolve_project_path(path: Path, project_root: Path) -> Path:
    resolved = path.resolve() if path.is_absolute() else (project_root / path).resolve()
    try:
        resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"path escapes --project-root: {path}") from exc
    return resolved


def _write_log(path: Path | None, project_root: Path, content: str) -> None:
    if path is None:
        return
    log_path = _resolve_project_path(path, project_root)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(content, encoding="utf-8")


def main() -> int:
    args = build_parser().parse_args()
    runtime = find_runtime(args.runtime)
    if args.check:
        payload = {
            "available": runtime is not None,
            "runtime": runtime[0] if runtime else None,
            "executable": str(runtime[1]) if runtime else None,
        }
        print(json.dumps(payload, ensure_ascii=False))
        return 0 if runtime else 1

    if args.script is None:
        print("error: a .m script is required unless --check is used", file=sys.stderr)
        return 2
    if runtime is None:
        print(
            "error: neither MATLAB nor GNU Octave is available; run $doctor for setup guidance",
            file=sys.stderr,
        )
        return 127

    project_root = args.project_root.resolve()
    script = args.script.resolve()
    if script.suffix.lower() != ".m" or not script.is_file():
        print(f"error: MATLAB script not found: {script}", file=sys.stderr)
        return 2
    try:
        script.relative_to(project_root)
        expected_artifacts = [
            _resolve_project_path(path, project_root) for path in args.expected_artifact
        ]
        if args.log:
            _resolve_project_path(args.log, project_root)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    started_ns = time.time_ns()
    preference_root = project_root / ".runtime" / "matlab-prefs"
    preference_root.mkdir(parents=True, exist_ok=True)
    preferences = Path(tempfile.mkdtemp(prefix="run-", dir=preference_root))
    try:
        completed = run_script(
            runtime_name=runtime[0],
            executable=runtime[1],
            script=script,
            project_root=project_root,
            timeout=args.timeout,
            preferences=preferences,
        )
    except subprocess.TimeoutExpired as exc:
        partial_stdout = exc.stdout or ""
        partial_stderr = exc.stderr or ""
        if isinstance(partial_stdout, bytes):
            partial_stdout = partial_stdout.decode("utf-8", errors="replace")
        if isinstance(partial_stderr, bytes):
            partial_stderr = partial_stderr.decode("utf-8", errors="replace")
        partial = partial_stdout
        if partial_stderr:
            partial += ("\n" if partial else "") + partial_stderr
        _write_log(args.log, project_root, partial)
        print(f"error: execution exceeded {args.timeout} seconds", file=sys.stderr)
        return 124

    combined = completed.stdout
    if completed.stderr:
        combined += ("\n" if combined else "") + completed.stderr
    _write_log(args.log, project_root, combined)

    marker_count = completed.stdout.count(MATLAB_COMPLETION_MARKER)
    display_output = combined.replace(MATLAB_COMPLETION_MARKER, "")
    if display_output:
        print(
            display_output,
            end="" if display_output.endswith("\n") else "\n",
        )

    failures: list[str] = []
    if completed.returncode != 0:
        failures.append(f"runtime exited with code {completed.returncode}")
    if marker_count != 1:
        failures.append(f"completion marker count is {marker_count}, expected exactly 1")
    if args.require_result and not any(
        line.startswith("RESULT ") for line in completed.stdout.splitlines()
    ):
        failures.append("stdout contains no line beginning with 'RESULT '")
    stale = [
        path
        for path in expected_artifacts
        if not path.is_file() or path.stat().st_mtime_ns < started_ns
    ]
    if stale:
        failures.append(
            "expected artifacts were not created or refreshed: "
            + ", ".join(str(path.relative_to(project_root)) for path in stale)
        )
    if failures:
        for failure in failures:
            print(f"error: {failure}", file=sys.stderr)
        return completed.returncode if completed.returncode != 0 else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
