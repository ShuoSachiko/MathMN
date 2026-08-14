#!/usr/bin/env python3
"""Probe MATLAB and the optional MathWorks Agentic Toolkit without changing it."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from matlab_runner import find_runtime

MARKER = "__MATHMODEL_AGENTIC_STATUS__"


def parse_probe(stdout: str) -> dict[str, object]:
    line = next((item for item in stdout.splitlines() if item.startswith(MARKER)), None)
    if line is None:
        raise ValueError("MATLAB probe marker is missing")
    parts = line[len(MARKER) :].strip().split("|")
    if len(parts) != 4:
        raise ValueError("MATLAB probe marker is malformed")
    return {
        "version": parts[0],
        "release": parts[1],
        "setup_function_available": parts[2] == "1",
        "share_session_function_available": parts[3] == "1",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--require-toolkit", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    mcp_server = (
        repo
        / ".runtime"
        / "matlab-agentic-toolkit"
        / "bin"
        / "matlab-mcp-server-windows-x64.exe"
    )
    toolkit_source = repo / ".runtime" / "matlab-agentic-toolkit" / "skills-catalog"
    codex_config = repo / ".codex" / "config.toml"
    runtime = find_runtime("matlab")
    document: dict[str, object] = {
        "schema_version": 1,
        "matlab_available": runtime is not None,
        "runtime": "matlab" if runtime else None,
        "executable": str(runtime[1]) if runtime else None,
        "agentic_toolkit": None,
        "mcp_server_available": mcp_server.is_file(),
        "skills_source_available": toolkit_source.is_dir(),
        "codex_project_config_available": codex_config.is_file(),
        "expected_mcp_tools": [
            "evaluate_matlab_code",
            "run_matlab_file",
            "run_matlab_test_file",
            "check_matlab_code",
            "detect_matlab_toolboxes",
        ],
    }
    exit_code = 0
    if runtime:
        expression = (
            "fprintf('" + MARKER + "%s|%s|%d|%d\\n',version,version('-release'),"
            "exist('setupAgenticToolkit','file')==2,exist('shareMATLABSession','file')==2);"
        )
        with tempfile.TemporaryDirectory(prefix="mathmodel-matlab-probe-") as prefs:
            completed = subprocess.run(
                [str(runtime[1]), "-noFigureWindows", "-batch", expression],
                capture_output=True,
                text=True,
                timeout=args.timeout,
                check=False,
                env={**__import__("os").environ, "MATLAB_PREFDIR": prefs},
            )
        document["probe_exit_code"] = completed.returncode
        try:
            probe = parse_probe(completed.stdout)
            document.update(probe)
            document["installer_available"] = bool(probe["setup_function_available"])
            document["share_session_available"] = bool(
                probe["share_session_function_available"]
            )
        except ValueError as exc:
            document["probe_error"] = str(exc)
            exit_code = 1
    else:
        exit_code = 1
    document["agentic_toolkit"] = bool(
        runtime
        and mcp_server.is_file()
        and toolkit_source.is_dir()
        and codex_config.is_file()
    )
    if args.require_toolkit and not document["agentic_toolkit"]:
        exit_code = 1
    rendered = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        if args.output.exists():
            print("ERROR: refusing to overwrite output", file=sys.stderr)
            return 2
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
