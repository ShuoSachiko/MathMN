#!/usr/bin/env python3
"""Issue and verify hash-pinned handoffs for math-modeling agents."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
ALLOWED_STATUSES = {"PROPOSED", "NEEDS_REVIEW", "REJECTED", "UNVERIFIED"}
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
STAGES = {
    "intake",
    "literature",
    "analysis",
    "coding",
    "drawing",
    "writing",
    "verification",
}


class ProtocolError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def render(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(flag and getattr(info, "st_file_attributes", 0) & flag)


def root_path(raw: str) -> Path:
    root = Path(raw).resolve(strict=True)
    if not root.is_dir() or is_link(root):
        raise ProtocolError("project root must be a real directory, not a link")
    return root


def project_path(root: Path, raw: str, *, must_exist: bool = True) -> Path:
    candidate = Path(raw)
    candidate = candidate if candidate.is_absolute() else root / candidate
    candidate = candidate.resolve(strict=must_exist)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ProtocolError(f"path escapes project root: {raw}") from exc
    current = root
    for part in candidate.relative_to(root).parts:
        current /= part
        if current.exists() and is_link(current):
            raise ProtocolError(f"links are not allowed in protocol paths: {current}")
    return candidate


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot read JSON {path}: {exc}") from exc


def write_new(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or is_link(path):
        raise ProtocolError(f"refusing to overwrite protocol artifact: {path}")
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def clean(value: str, label: str) -> str:
    value = value.strip()
    if not TOKEN_RE.fullmatch(value):
        raise ProtocolError(f"invalid {label}: {value!r}")
    return value


def reports_dir(root: Path) -> Path:
    return root / "reports" / "agents"


def append_event(root: Path, event: dict[str, Any]) -> None:
    target = reports_dir(root) / "AGENT_EVENTS.jsonl"
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def load_problem(root: Path) -> tuple[dict[str, Any], str]:
    path = root / "reports" / "PROBLEM_MANIFEST.json"
    document = read_json(path)
    if not isinstance(document, dict) or not document.get("root_hash"):
        raise ProtocolError("reports/PROBLEM_MANIFEST.json is missing or invalid")
    return document, digest_file(path)


def command_init(args: argparse.Namespace) -> None:
    root = root_path(args.project_root)
    manifest, manifest_sha = load_problem(root)
    base = reports_dir(root)
    state_path = base / "ORCHESTRATION.json"
    state = {
        "schema_version": SCHEMA_VERSION,
        "project_id": manifest.get("project_id"),
        "problem_root_hash": manifest["root_hash"],
        "problem_manifest_sha256": manifest_sha,
        "review_mode": manifest.get("review_mode"),
        "policy": {
            "ai_can_approve_gate": False,
            "single_writer_for_shared_artifacts": True,
            "unresolved_disagreement": "UNVERIFIED",
        },
    }
    write_new(state_path, render(state))
    for name in ("packets", "results", "reviews"):
        (base / name).mkdir(parents=True, exist_ok=True)
    append_event(
        root,
        {"event": "initialized", "at": now(), "state_sha256": digest_file(state_path)},
    )
    print(state_path.relative_to(root).as_posix())


def parse_input(root: Path, value: str) -> dict[str, Any]:
    if "=" not in value:
        raise ProtocolError("--input must use NAME=PATH")
    name, raw = value.split("=", 1)
    name = clean(name, "input name")
    path = project_path(root, raw)
    if not path.is_file():
        raise ProtocolError(f"agent inputs must be regular files: {raw}")
    return {
        "name": name,
        "path": path.relative_to(root).as_posix(),
        "sha256": digest_file(path),
        "bytes": path.stat().st_size,
    }


def command_issue(args: argparse.Namespace) -> None:
    root = root_path(args.project_root)
    state = read_json(reports_dir(root) / "ORCHESTRATION.json")
    manifest, manifest_sha = load_problem(root)
    if (
        state.get("problem_root_hash") != manifest.get("root_hash")
        or state.get("problem_manifest_sha256") != manifest_sha
    ):
        raise ProtocolError(
            "orchestration state is stale; initialize a new isolated project state"
        )
    task_id = clean(args.task_id, "task id")
    role = clean(args.role, "role")
    provider = clean(args.provider, "provider")
    model = clean(args.model, "model")
    objective = args.objective.strip()
    if not objective:
        raise ProtocolError("objective must not be empty")
    req_ids = [clean(value, "ReqID") for value in args.req_id]
    if not req_ids:
        raise ProtocolError("at least one --req-id is required")
    inputs = [parse_input(root, item) for item in args.input]
    if len({item["name"] for item in inputs}) != len(inputs):
        raise ProtocolError("input names must be unique")
    packet = {
        "schema_version": SCHEMA_VERSION,
        "kind": "CONTEXT_PACKET",
        "task_id": task_id,
        "role": role,
        "stage": args.stage,
        "objective": objective,
        "req_ids": sorted(set(req_ids)),
        "provider": provider,
        "model": model,
        "issued_at": now(),
        "problem_root_hash": manifest["root_hash"],
        "problem_manifest_sha256": manifest_sha,
        "inputs": inputs,
        "constraints": {
            "read_only_inputs": True,
            "may_expand_input_allowlist": False,
            "may_approve_stage_gate": False,
            "may_claim_human_review": False,
            "result_statuses": sorted(ALLOWED_STATUSES),
        },
        "output_contract": "AGENT_RESULT/v1",
    }
    target = reports_dir(root) / "packets" / f"{task_id}.json"
    write_new(target, render(packet))
    append_event(
        root,
        {
            "event": "issued",
            "at": now(),
            "task_id": task_id,
            "packet_sha256": digest_file(target),
            "provider": provider,
            "model": model,
            "role": role,
        },
    )
    print(target.relative_to(root).as_posix())
    print(f"packet_sha256={digest_file(target)}")


def validate_claim(claim: Any, index: int) -> None:
    if not isinstance(claim, dict):
        raise ProtocolError(f"claim {index} must be an object")
    for key in ("claim_id", "text", "evidence", "confidence", "limitations"):
        if key not in claim:
            raise ProtocolError(f"claim {index} is missing {key}")
    clean(str(claim["claim_id"]), "claim id")
    if not str(claim["text"]).strip():
        raise ProtocolError(f"claim {index} text is empty")
    if not isinstance(claim["evidence"], list) or not all(
        isinstance(x, str) and x.strip() for x in claim["evidence"]
    ):
        raise ProtocolError(
            f"claim {index} evidence must be a list of non-empty locations"
        )
    confidence = claim["confidence"]
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, (int, float))
        or not 0 <= confidence <= 1
    ):
        raise ProtocolError(f"claim {index} confidence must be between 0 and 1")
    if not isinstance(claim["limitations"], list):
        raise ProtocolError(f"claim {index} limitations must be a list")


def check_packet_inputs(root: Path, packet: dict[str, Any]) -> None:
    for item in packet.get("inputs", []):
        path = project_path(root, str(item.get("path", "")))
        if digest_file(path) != item.get("sha256"):
            raise ProtocolError(f"packet input changed: {item.get('path')}")


def command_submit(args: argparse.Namespace) -> None:
    root = root_path(args.project_root)
    packet_path = project_path(root, args.packet)
    result_path = project_path(root, args.result)
    packet = read_json(packet_path)
    result = read_json(result_path)
    if not isinstance(packet, dict) or packet.get("kind") != "CONTEXT_PACKET":
        raise ProtocolError("invalid context packet")
    if not isinstance(result, dict):
        raise ProtocolError("result must be an object")
    check_packet_inputs(root, packet)
    expected_hash = digest_file(packet_path)
    exact = ("task_id", "role", "provider", "model")
    for key in exact:
        if result.get(key) != packet.get(key):
            raise ProtocolError(f"result {key} does not match packet")
    if result.get("packet_sha256") != expected_hash:
        raise ProtocolError("result packet_sha256 does not match the issued packet")
    if result.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("unsupported result schema_version")
    if result.get("status") not in ALLOWED_STATUSES:
        raise ProtocolError("result status cannot approve a gate")
    if sorted(set(result.get("req_ids", []))) != packet.get("req_ids"):
        raise ProtocolError("result ReqIDs must exactly match the packet")
    if not str(result.get("summary", "")).strip():
        raise ProtocolError("result summary is empty")
    claims = result.get("claims")
    if not isinstance(claims, list):
        raise ProtocolError("result claims must be a list")
    for index, claim in enumerate(claims):
        validate_claim(claim, index)
    for key in ("artifacts", "open_questions", "recommended_checks"):
        if not isinstance(result.get(key), list):
            raise ProtocolError(f"result {key} must be a list")
    result["kind"] = "AGENT_RESULT"
    result["submitted_at"] = now()
    result["problem_root_hash"] = packet["problem_root_hash"]
    target = reports_dir(root) / "results" / f"{packet['task_id']}.json"
    write_new(target, render(result))
    append_event(
        root,
        {
            "event": "submitted",
            "at": now(),
            "task_id": packet["task_id"],
            "packet_sha256": expected_hash,
            "result_sha256": digest_file(target),
            "status": result["status"],
        },
    )
    print(target.relative_to(root).as_posix())


def command_disagree(args: argparse.Namespace) -> None:
    root = root_path(args.project_root)
    left_path = project_path(root, args.left)
    right_path = project_path(root, args.right)
    left = read_json(left_path)
    right = read_json(right_path)
    if left.get("kind") != "AGENT_RESULT" or right.get("kind") != "AGENT_RESULT":
        raise ProtocolError("both sides must be sealed AGENT_RESULT files")
    left_claims = {item["claim_id"]: item for item in left.get("claims", [])}
    right_claims = {item["claim_id"]: item for item in right.get("claims", [])}
    report = {
        "schema_version": SCHEMA_VERSION,
        "kind": "DISAGREEMENT_REPORT",
        "created_at": now(),
        "left": {
            "path": left_path.relative_to(root).as_posix(),
            "sha256": digest_file(left_path),
            "provider": left.get("provider"),
            "model": left.get("model"),
        },
        "right": {
            "path": right_path.relative_to(root).as_posix(),
            "sha256": digest_file(right_path),
            "provider": right.get("provider"),
            "model": right.get("model"),
        },
        "same_provider_model": (left.get("provider"), left.get("model"))
        == (right.get("provider"), right.get("model")),
        "summary_equal": left.get("summary") == right.get("summary"),
        "only_left_claim_ids": sorted(set(left_claims) - set(right_claims)),
        "only_right_claim_ids": sorted(set(right_claims) - set(left_claims)),
        "differing_shared_claims": sorted(
            key
            for key in set(left_claims) & set(right_claims)
            if canonical(left_claims[key]) != canonical(right_claims[key])
        ),
        "resolution": "UNVERIFIED",
        "required_action": "Run a deterministic discriminating check or obtain a recorded human decision.",
    }
    target = project_path(root, args.output, must_exist=False)
    write_new(target, render(report))
    append_event(
        root,
        {
            "event": "disagreement",
            "at": now(),
            "left": left.get("task_id"),
            "right": right.get("task_id"),
            "report_sha256": digest_file(target),
        },
    )
    print(target.relative_to(root).as_posix())


def command_verify(args: argparse.Namespace) -> None:
    root = root_path(args.project_root)
    base = reports_dir(root)
    state = read_json(base / "ORCHESTRATION.json")
    manifest, manifest_sha = load_problem(root)
    failures: list[str] = []
    if (
        state.get("problem_root_hash") != manifest.get("root_hash")
        or state.get("problem_manifest_sha256") != manifest_sha
    ):
        failures.append("orchestration state is stale")
    packet_by_task: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in sorted((base / "packets").glob("*.json")):
        packet = read_json(path)
        task_id = packet.get("task_id")
        if task_id in packet_by_task:
            failures.append(f"duplicate packet task_id: {task_id}")
        packet_by_task[str(task_id)] = (path, packet)
        try:
            check_packet_inputs(root, packet)
        except ProtocolError as exc:
            failures.append(str(exc))
    for path in sorted((base / "results").glob("*.json")):
        result = read_json(path)
        item = packet_by_task.get(str(result.get("task_id")))
        if item is None:
            failures.append(f"result has no packet: {path.name}")
            continue
        packet_path, packet = item
        if result.get("packet_sha256") != digest_file(packet_path):
            failures.append(f"result packet hash mismatch: {path.name}")
        if result.get("status") not in ALLOWED_STATUSES:
            failures.append(f"result contains forbidden gate status: {path.name}")
        if result.get("problem_root_hash") != packet.get("problem_root_hash"):
            failures.append(f"result problem hash mismatch: {path.name}")
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        raise ProtocolError(f"protocol verification found {len(failures)} failure(s)")
    print(
        f"PASS: verified {len(packet_by_task)} packet(s) and {len(list((base / 'results').glob('*.json')))} result(s)"
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--project-root", default=".")
    init.set_defaults(handler=command_init)
    issue = commands.add_parser("issue")
    issue.add_argument("--project-root", default=".")
    issue.add_argument("--task-id", required=True)
    issue.add_argument("--role", required=True)
    issue.add_argument("--stage", choices=sorted(STAGES), required=True)
    issue.add_argument("--objective", required=True)
    issue.add_argument("--req-id", action="append", default=[])
    issue.add_argument("--input", action="append", default=[])
    issue.add_argument("--provider", required=True)
    issue.add_argument("--model", required=True)
    issue.set_defaults(handler=command_issue)
    submit = commands.add_parser("submit")
    submit.add_argument("--project-root", default=".")
    submit.add_argument("--packet", required=True)
    submit.add_argument("--result", required=True)
    submit.set_defaults(handler=command_submit)
    disagree = commands.add_parser("disagree")
    disagree.add_argument("--project-root", default=".")
    disagree.add_argument("--left", required=True)
    disagree.add_argument("--right", required=True)
    disagree.add_argument("--output", required=True)
    disagree.set_defaults(handler=command_disagree)
    verify = commands.add_parser("verify")
    verify.add_argument("--project-root", default=".")
    verify.set_defaults(handler=command_verify)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        args.handler(args)
    except (ProtocolError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
