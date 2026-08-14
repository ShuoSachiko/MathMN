#!/usr/bin/env python3
"""Start, inspect, and stop the repository-local Windows development stack."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = REPO_ROOT / ".runtime"
STATE_FILE = RUNTIME_ROOT / "services.json"
LOG_ROOT = RUNTIME_ROOT / "logs"


def is_running(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-Command",
            f"if (Get-Process -Id {pid} -ErrorAction SilentlyContinue) {{ exit 0 }} else {{ exit 1 }}",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    return completed.returncode == 0


def listener_pid(port: int) -> int | None:
    if os.name != "nt":
        return None
    completed = subprocess.run(
        ["netstat.exe", "-ano", "-p", "tcp"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    pattern = re.compile(rf"^\s*TCP\s+\S*:{port}\s+\S+\s+LISTENING\s+(\d+)\s*$")
    for line in completed.stdout.splitlines():
        match = pattern.match(line)
        if match:
            return int(match.group(1))
    return None


def http_ready(url: str) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return 200 <= response.status < 500
    except (OSError, urllib.error.URLError):
        return False


def _creation_flags() -> int:
    if os.name != "nt":
        return 0
    return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW


def launch(name: str, command: list[str], cwd: Path, environment: dict[str, str]) -> int:
    stdout_path = LOG_ROOT / f"{name}.out.log"
    stderr_path = LOG_ROOT / f"{name}.err.log"
    stdout_handle = stdout_path.open("ab")
    stderr_handle = stderr_path.open("ab")
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=stdout_handle,
            stderr=stderr_handle,
            creationflags=_creation_flags(),
            close_fds=True,
        )
    finally:
        stdout_handle.close()
        stderr_handle.close()
    return process.pid


def start() -> int:
    if STATE_FILE.exists():
        print("error: service state exists; run scripts/stop-local.ps1 first", file=sys.stderr)
        return 1

    redis = RUNTIME_ROOT / "redis" / "redis-server.exe"
    python = REPO_ROOT / "backend" / ".venv" / "Scripts" / "python.exe"
    pnpm = RUNTIME_ROOT / "pnpm" / "node_modules" / ".bin" / "pnpm.cmd"
    for required in (redis, python, pnpm):
        if not required.is_file():
            print(f"error: missing {required}; run scripts/setup-local.ps1", file=sys.stderr)
            return 1

    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    redis_data = RUNTIME_ROOT / "redis-data"
    redis_data.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["ENV"] = "DEV"
    environment["REDIS_URL"] = "redis://127.0.0.1:6379/0"

    redis_pid = launch(
        "redis",
        [
            str(redis),
            "--port",
            "6379",
            "--dir",
            str(redis_data),
            "--dbfilename",
            "dump.rdb",
        ],
        redis.parent,
        environment,
    )
    backend_pid = launch(
        "backend",
        [
            str(python),
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
            "--ws-ping-interval",
            "60",
            "--ws-ping-timeout",
            "120",
        ],
        REPO_ROOT / "backend",
        environment,
    )
    frontend_command = [str(pnpm), "exec", "vite", "--host", "127.0.0.1"]
    frontend_pid = launch(
        "frontend", frontend_command, REPO_ROOT / "frontend", environment
    )

    state: dict[str, object] = {
        "Repository": str(REPO_ROOT),
        "StartedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "Redis": redis_pid,
        "Backend": backend_pid,
        "Frontend": frontend_pid,
    }
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    deadline = time.monotonic() + 60
    backend_ready = frontend_ready = False
    while time.monotonic() < deadline:
        backend_ready = backend_ready or http_ready("http://127.0.0.1:8000/docs")
        frontend_ready = frontend_ready or http_ready("http://127.0.0.1:5173")
        if backend_ready and frontend_ready:
            break
        time.sleep(1)

    state["Redis"] = listener_pid(6379) or redis_pid
    state["Backend"] = listener_pid(8000) or backend_pid
    state["Frontend"] = listener_pid(5173) or frontend_pid
    STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")

    print(f"Redis PID: {state['Redis']}")
    print(f"Backend: http://127.0.0.1:8000/docs (ready={backend_ready})")
    print(f"Frontend: http://127.0.0.1:5173 (ready={frontend_ready})")
    print(f"Logs: {LOG_ROOT}")
    return 0 if backend_ready and frontend_ready else 1


def load_state() -> dict[str, object] | None:
    if not STATE_FILE.exists():
        return None
    return json.loads(STATE_FILE.read_text(encoding="utf-8-sig"))


def status() -> int:
    state = load_state()
    if state is None:
        print("No local service state file found.")
        return 1
    for name in ("Redis", "Backend", "Frontend"):
        pid = int(state[name])
        print(f"{name:8} PID={pid:<8} running={is_running(pid)}")
    print(f"Backend HTTP ready={http_ready('http://127.0.0.1:8000/docs')}")
    print(f"Frontend HTTP ready={http_ready('http://127.0.0.1:5173')}")
    return 0


def stop() -> int:
    state = load_state()
    if state is None:
        print("No local service state file found.")
        return 0
    service_ports = {"Frontend": 5173, "Backend": 8000, "Redis": 6379}
    allowed_images = {
        "Frontend": ("node.exe", "cmd.exe"),
        "Backend": ("python.exe",),
        "Redis": ("redis-server.exe",),
    }
    all_stopped = True
    for name in ("Frontend", "Backend", "Redis"):
        pid = listener_pid(service_ports[name]) or int(state[name])
        if not is_running(pid):
            continue
        task = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                f"$p=Get-Process -Id {pid} -ErrorAction SilentlyContinue; if($p){{$p.ProcessName}}",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        ).stdout.lower()
        allowed_names = tuple(image.removesuffix(".exe") for image in allowed_images[name])
        if not any(process_name in task for process_name in allowed_names):
            print(f"WARNING skipped {name} PID {pid}: unexpected process image")
            all_stopped = False
            continue
        completed = subprocess.run(
            ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode == 0:
            print(f"Stopped {name} (PID {pid})")
        else:
            print(f"WARNING could not stop {name} PID {pid}: {completed.stderr.strip()}")
            all_stopped = False
    if all_stopped:
        STATE_FILE.unlink(missing_ok=True)
        return 0
    print("Service state retained because one or more processes could not be stopped.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("start", "status", "stop"))
    args = parser.parse_args()
    return {"start": start, "status": status, "stop": stop}[args.action]()


if __name__ == "__main__":
    raise SystemExit(main())
