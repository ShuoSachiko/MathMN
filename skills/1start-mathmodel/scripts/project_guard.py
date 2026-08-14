#!/usr/bin/env python3
"""Create and verify a project input allowlist and workflow guard files."""

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
from typing import Any, Iterable


SCHEMA_VERSION = 1
OUTPUT_NAMES = (
    "PROBLEM_MANIFEST.json",
    "PROVENANCE.md",
    "DECISION_LOG.md",
    "HANDOFF.json",
    "STAGE_GATES.json",
    "HUMAN_REVIEW.json",
    "CURRENT_VERSIONS.json",
    "VERSION_DECISIONS.jsonl",
    "AI_USAGE_LOG.jsonl",
)
STAGE_IDS = (
    "intake",
    "literature",
    "analysis",
    "coding",
    "drawing",
    "writing",
    "verification",
)
REVIEW_CHECKPOINTS = (
    "intake",
    "literature",
    "contract",
    "model",
    "results",
    "paper",
    "submission",
)
MODES = (
    "live-competition",
    "isolated-benchmark",
    "retrospective-audit",
    "guided-study",
)
TASK_PROVENANCE_VALUES = (
    "private-unreleased",
    "private-parametric",
    "historical-public",
    "current-public",
    "user-provided",
)
RUNTIME_ISOLATION_VALUES = (
    "enforced",
    "declared-only",
    "violated",
    "not-applicable",
)
REVIEW_MODES = ("human-supervised", "autonomous-simulation")
REVIEW_STATUSES = (
    "PENDING",
    "APPROVED",
    "CHANGES_REQUESTED",
    "WAIVED_FOR_SIMULATION",
)
REVIEWER_TYPES = ("human", "controlled-human-review-ui", "simulation-orchestrator")
ROLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class GuardError(ValueError):
    """Raised when a path or manifest violates a guard invariant."""


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _render_json(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_link_like(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    if reparse_flag and attributes & reparse_flag:
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _is_within(path: Path, parent: Path) -> bool:
    try:
        return os.path.commonpath((_norm(path), _norm(parent))) == _norm(parent)
    except ValueError:
        return False


def _assert_link_free(path: Path, stop: Path, *, include_leaf: bool = True) -> None:
    """Reject symlinks, junctions, and other reparse points below ``stop``."""
    if not _is_within(path, stop):
        raise GuardError(f"path escapes project root: {path}")
    relative = path.relative_to(stop)
    current = stop
    if _is_link_like(current):
        raise GuardError(f"project root is a symbolic link or junction: {stop}")
    parts = relative.parts if include_leaf else relative.parts[:-1]
    for part in parts:
        current = current / part
        if current.exists() and _is_link_like(current):
            raise GuardError(f"symbolic links and junctions are not allowed: {current}")


def _project_root(raw_root: str) -> Path:
    root = Path(os.path.abspath(os.path.expanduser(raw_root)))
    if not root.exists() or not root.is_dir():
        raise GuardError(f"project root is not an existing directory: {root}")
    _assert_link_free(root, root)
    resolved = root.resolve(strict=True)
    if _norm(resolved) != _norm(root):
        raise GuardError(f"project root resolves through a link or junction: {root}")
    return root


def _lexical_path(root: Path, raw_path: str) -> Path:
    expanded = Path(os.path.expanduser(raw_path))
    candidate = expanded if expanded.is_absolute() else root / expanded
    candidate = Path(os.path.abspath(candidate))
    if not _is_within(candidate, root):
        raise GuardError(f"path escapes project root: {raw_path}")
    return candidate


def _existing_path(root: Path, raw_path: str) -> Path:
    candidate = _lexical_path(root, raw_path)
    if not candidate.exists():
        raise GuardError(f"input path does not exist: {raw_path}")
    _assert_link_free(candidate, root)
    resolved = candidate.resolve(strict=True)
    if not _is_within(resolved, root) or _norm(resolved) != _norm(candidate):
        raise GuardError(f"input resolves outside the project root: {raw_path}")
    if not (candidate.is_file() or candidate.is_dir()):
        raise GuardError(f"input must be a regular file or directory: {raw_path}")
    return candidate


def _output_directory(root: Path, raw_path: str, *, create: bool) -> Path:
    output_dir = _lexical_path(root, raw_path)
    ancestor = output_dir
    while not ancestor.exists() and ancestor != root:
        ancestor = ancestor.parent
    _assert_link_free(ancestor, root)
    if ancestor.exists() and not ancestor.is_dir():
        raise GuardError(f"output ancestor is not a directory: {ancestor}")
    if create:
        output_dir.mkdir(parents=True, exist_ok=True)
        _assert_link_free(output_dir, root)
    if output_dir.exists() and not output_dir.is_dir():
        raise GuardError(f"output path is not a directory: {output_dir}")
    return output_dir


def _relative(path: Path, root: Path) -> str:
    relative = path.relative_to(root).as_posix()
    return relative or "."


def _parse_inputs(values: Iterable[str]) -> list[tuple[str, str]]:
    parsed: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        if "=" not in value:
            raise GuardError(f"input must use ROLE=PATH syntax: {value!r}")
        role, raw_path = value.split("=", 1)
        role = role.strip()
        raw_path = raw_path.strip()
        if not ROLE_RE.fullmatch(role):
            raise GuardError(
                "role must start with an ASCII letter and contain only "
                f"letters, digits, '.', '_' or '-': {role!r}"
            )
        if not raw_path:
            raise GuardError(f"input path is empty for role {role!r}")
        item = (role, raw_path)
        if item in seen:
            raise GuardError(f"duplicate input declaration: {value!r}")
        seen.add(item)
        parsed.append(item)
    if not parsed:
        raise GuardError("at least one --input ROLE=PATH declaration is required")
    return parsed


def _directory_members(path: Path, root: Path) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    for current_text, dir_names, file_names in os.walk(path, followlinks=False):
        current = Path(current_text)
        _assert_link_free(current, root)
        for name in sorted(dir_names):
            child = current / name
            if _is_link_like(child):
                raise GuardError(
                    f"symbolic links and junctions are not allowed in inputs: {child}"
                )
            if not child.is_dir():
                raise GuardError(f"non-directory entry encountered during walk: {child}")
        for name in sorted(file_names):
            child = current / name
            if _is_link_like(child):
                raise GuardError(
                    f"symbolic links and junctions are not allowed in inputs: {child}"
                )
            if not child.is_file():
                raise GuardError(f"input contains a non-regular file: {child}")
            members.append(
                {
                    "path": _relative(child, root),
                    "size": child.stat().st_size,
                    "sha256": _sha256_file(child),
                }
            )
    members.sort(key=lambda item: item["path"])
    return members


def _input_record(role: str, path: Path, root: Path) -> dict[str, Any]:
    if path.is_file():
        return {
            "role": role,
            "path": _relative(path, root),
            "type": "file",
            "size": path.stat().st_size,
            "sha256": _sha256_file(path),
            "verification": "unverified",
            "verification_actor": None,
            "verification_note": "",
            "verified_at": None,
        }
    members = _directory_members(path, root)
    return {
        "role": role,
        "path": _relative(path, root),
        "type": "directory",
        "size": sum(item["size"] for item in members),
        "sha256": _sha256_bytes(_canonical_json_bytes(members)),
        "files": members,
        "verification": "unverified",
        "verification_actor": None,
        "verification_note": "",
        "verified_at": None,
    }


def _build_input_records(
    root: Path,
    declarations: Iterable[tuple[str, str]],
    output_dir: Path,
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    normalized_seen: set[tuple[str, str]] = set()
    targets = {output_dir / name for name in OUTPUT_NAMES}
    for role, raw_path in declarations:
        path = _existing_path(root, raw_path)
        normalized = (role, _relative(path, root))
        if normalized in normalized_seen:
            raise GuardError(
                f"duplicate normalized input declaration: {role}={normalized[1]}"
            )
        normalized_seen.add(normalized)
        if path.is_dir() and _is_within(output_dir, path):
            raise GuardError(
                "an input directory may not contain the generated guard files: "
                f"{path}"
            )
        if path in targets:
            raise GuardError(f"a generated guard file cannot also be an input: {path}")
        records.append(_input_record(role, path, root))
    records.sort(key=lambda item: (item["role"], item["path"]))
    return records


def _input_hash_material(inputs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ignored = {
        "verification",
        "verification_actor",
        "verification_note",
        "verified_at",
    }
    return [
        {key: value for key, value in item.items() if key not in ignored}
        for item in inputs
    ]


def _problem_manifest(
    inputs: list[dict[str, Any]],
    *,
    project_id: str,
    mode: str,
    task_provenance: str,
    runtime_isolation: str,
    review_mode: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_type": "problem",
        "hash_algorithm": "sha256",
        "project_id": project_id,
        "mode": mode,
        "task_provenance": task_provenance,
        "runtime_isolation": runtime_isolation,
        "review_mode": review_mode,
        "inputs": inputs,
        "root_hash": _sha256_bytes(
            _canonical_json_bytes(_input_hash_material(inputs))
        ),
    }


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _provenance_markdown(
    manifest: dict[str, Any], manifest_sha256: str
) -> str:
    rows = [
        "# Provenance",
        "",
        f"- Problem manifest SHA-256: `{manifest_sha256}`",
        f"- Input root hash: `{manifest['root_hash']}`",
        "",
        "## Declared input allowlist",
        "",
        "| Role | Path | Type | Bytes | SHA-256 | Verification |",
        "| --- | --- | --- | ---: | --- | --- |",
    ]
    for item in manifest["inputs"]:
        rows.append(
            "| {role} | {path} | {type} | {size} | `{sha256}` | {verification} |".format(
                **{key: _markdown_cell(value) for key, value in item.items()}
            )
        )
    rows.extend(
        (
            "",
            "## Additional sources",
            "",
            "Record any source introduced after initialization before using it.",
            "",
            "| Source ID | Location | Purpose | Accessed | Evidence hash |",
            "| --- | --- | --- | --- | --- |",
        )
    )
    return "\n".join(rows) + "\n"


def _decision_log_markdown(manifest_sha256: str) -> str:
    return "\n".join(
        (
            "# Decision Log",
            "",
            f"- Problem manifest SHA-256: `{manifest_sha256}`",
            "",
            "Record material interpretations and workflow decisions in order.",
            "",
            "| Decision ID | Status | Evidence | Affected requirements | Decision |",
            "| --- | --- | --- | --- | --- |",
            "",
        )
    )


def _handoff(manifest: dict[str, Any], manifest_sha256: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "problem_manifest_sha256": manifest_sha256,
        "problem_root_hash": manifest["root_hash"],
        "project_id": manifest["project_id"],
        "review_mode": manifest["review_mode"],
        "requirements": [],
        "decisions": [],
        "assumptions": [],
        "open_risks": [],
        "artifacts": [],
    }


def _stage_gates(
    manifest: dict[str, Any], manifest_sha256: str
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "problem_manifest_sha256": manifest_sha256,
        "problem_root_hash": manifest["root_hash"],
        "project_id": manifest["project_id"],
        "review_mode": manifest["review_mode"],
        "stages": [
            {
                "id": stage_id,
                "required": stage_id not in {"literature", "drawing"},
                "status": "NOT_STARTED",
                "upstream_hashes": {
                    "problem_manifest_sha256": manifest_sha256,
                    "problem_root_hash": manifest["root_hash"],
                },
                "required_artifacts": [],
                "evidence": [],
                "notes": "",
            }
            for stage_id in STAGE_IDS
        ],
    }


def _human_review(
    manifest: dict[str, Any], manifest_sha256: str
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "project_id": manifest["project_id"],
        "review_mode": manifest["review_mode"],
        "problem_manifest_sha256": manifest_sha256,
        "problem_root_hash": manifest["root_hash"],
        "authorship": {
            "type": None,
            "agent_generated": None,
            "note": "Set only when a human or simulation review decision is recorded.",
        },
        "checkpoints": [
            {
                "id": checkpoint,
                "status": "PENDING",
                "approval_id": None,
                "reviewed_by": None,
                "reviewer_type": None,
                "reviewed_at": None,
                "evidence": [],
                "source_id": None,
                "scope": "",
                "comments": "",
                "notes": "",
            }
            for checkpoint in REVIEW_CHECKPOINTS
        ],
    }


def _guard_contents(manifest: dict[str, Any]) -> dict[str, bytes]:
    manifest_bytes = _render_json(manifest)
    manifest_sha256 = _sha256_bytes(manifest_bytes)
    return {
        "PROBLEM_MANIFEST.json": manifest_bytes,
        "PROVENANCE.md": _provenance_markdown(
            manifest, manifest_sha256
        ).encode("utf-8"),
        "DECISION_LOG.md": _decision_log_markdown(manifest_sha256).encode("utf-8"),
        "HANDOFF.json": _render_json(_handoff(manifest, manifest_sha256)),
        "STAGE_GATES.json": _render_json(_stage_gates(manifest, manifest_sha256)),
        "HUMAN_REVIEW.json": _render_json(_human_review(manifest, manifest_sha256)),
        "CURRENT_VERSIONS.json": _render_json(
            {
                "schema_version": SCHEMA_VERSION,
                "project_id": manifest["project_id"],
                "problem_root_hash": manifest["root_hash"],
                "selections": {},
                "selection_hash": _sha256_bytes(_canonical_json_bytes({})),
            }
        ),
        "VERSION_DECISIONS.jsonl": b"",
        "AI_USAGE_LOG.jsonl": b"",
    }


def _write_files(output_dir: Path, contents: dict[str, bytes], overwrite: bool) -> None:
    targets = {name: output_dir / name for name in contents}
    unsafe = [str(path) for path in targets.values() if _is_link_like(path)]
    if unsafe:
        raise GuardError(
            "refusing to replace symbolic-link or junction guard files: "
            + ", ".join(unsafe)
        )
    if not overwrite:
        existing = [str(path) for path in targets.values() if path.exists()]
        if existing:
            raise GuardError(
                "refusing to overwrite existing guard files: " + ", ".join(existing)
            )
    if overwrite:
        temporary: list[tuple[Path, Path]] = []
        try:
            for name, data in contents.items():
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=f".{name}.", dir=output_dir, delete=False
                ) as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                    temporary.append((Path(stream.name), targets[name]))
            for source, target in temporary:
                os.replace(source, target)
        finally:
            for source, _ in temporary:
                source.unlink(missing_ok=True)
        return

    created: list[Path] = []
    try:
        for name, data in contents.items():
            target = targets[name]
            with target.open("xb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            created.append(target)
    except Exception:
        for target in created:
            target.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise GuardError(f"cannot read valid JSON from {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise GuardError(f"JSON document must be an object: {path}")
    return value


def _verify_references(
    output_dir: Path, manifest: dict[str, Any], manifest_sha256: str
) -> None:
    for name in ("PROVENANCE.md", "DECISION_LOG.md"):
        text = (output_dir / name).read_text(encoding="utf-8")
        if manifest_sha256 not in text:
            raise GuardError(f"{name} does not reference the current problem manifest")
    for name in ("HANDOFF.json", "STAGE_GATES.json", "HUMAN_REVIEW.json"):
        value = _read_json(output_dir / name)
        if value.get("schema_version") != SCHEMA_VERSION:
            raise GuardError(f"unsupported schema_version in {name}")
        if value.get("problem_manifest_sha256") != manifest_sha256:
            raise GuardError(f"stale problem_manifest_sha256 in {name}")
        if value.get("problem_root_hash") != manifest.get("root_hash"):
            raise GuardError(f"stale problem_root_hash in {name}")
        if name == "HUMAN_REVIEW.json":
            if value.get("review_mode") != manifest.get("review_mode"):
                raise GuardError("HUMAN_REVIEW.json has a stale review_mode")
            checkpoints = value.get("checkpoints")
            if not isinstance(checkpoints, list) or [
                item.get("id") for item in checkpoints if isinstance(item, dict)
            ] != list(REVIEW_CHECKPOINTS):
                raise GuardError("HUMAN_REVIEW.json checkpoints are invalid")
            if any(
                not isinstance(item, dict) or item.get("status") not in REVIEW_STATUSES
                for item in checkpoints
            ):
                raise GuardError("HUMAN_REVIEW.json contains an invalid review status")
    current = _read_json(output_dir / "CURRENT_VERSIONS.json")
    selections = current.get("selections")
    if current.get("schema_version") != SCHEMA_VERSION:
        raise GuardError("unsupported schema_version in CURRENT_VERSIONS.json")
    if current.get("problem_root_hash") != manifest.get("root_hash"):
        raise GuardError("CURRENT_VERSIONS.json has a stale problem_root_hash")
    if not isinstance(selections, dict) or current.get("selection_hash") != _sha256_bytes(
        _canonical_json_bytes(selections if isinstance(selections, dict) else {})
    ):
        raise GuardError("CURRENT_VERSIONS.json selection_hash is invalid")
    for name in ("VERSION_DECISIONS.jsonl", "AI_USAGE_LOG.jsonl"):
        payload = (output_dir / name).read_bytes()
        if payload and not payload.endswith(b"\n"):
            raise GuardError(f"{name} must end with a newline")


def _clean_label(value: str, label: str, *, maximum: int = 200) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise GuardError(f"{label} must contain 1 to {maximum} characters")
    if any(ord(character) < 32 for character in cleaned):
        raise GuardError(f"{label} must not contain control characters")
    return cleaned


def _validate_manifest_metadata(manifest: dict[str, Any]) -> None:
    _clean_label(str(manifest.get("project_id", "")), "project_id")
    if manifest.get("mode") not in MODES:
        raise GuardError("problem manifest contains an invalid mode")
    if manifest.get("task_provenance") not in TASK_PROVENANCE_VALUES:
        raise GuardError("problem manifest contains an invalid task_provenance")
    if manifest.get("runtime_isolation") not in RUNTIME_ISOLATION_VALUES:
        raise GuardError("problem manifest contains an invalid runtime_isolation")
    if manifest.get("review_mode") not in REVIEW_MODES:
        raise GuardError("problem manifest contains an invalid review_mode")
    if (
        manifest.get("mode") == "live-competition"
        and manifest.get("review_mode") != "human-supervised"
    ):
        raise GuardError("live-competition requires human-supervised review mode")


def _load_validated_guard(
    root: Path,
    output_dir: Path,
    explicit_inputs: Iterable[str] = (),
) -> tuple[dict[str, Any], str]:
    if not output_dir.exists() or not output_dir.is_dir():
        raise GuardError(f"guard output directory does not exist: {output_dir}")
    _assert_link_free(output_dir, root)
    for name in OUTPUT_NAMES:
        path = output_dir / name
        if not path.exists() or not path.is_file() or _is_link_like(path):
            raise GuardError(f"missing or unsafe guard file: {path}")

    manifest_path = output_dir / "PROBLEM_MANIFEST.json"
    manifest = _read_json(manifest_path)
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise GuardError("unsupported problem manifest schema_version")
    if manifest.get("manifest_type") != "problem":
        raise GuardError("PROBLEM_MANIFEST.json has the wrong manifest_type")
    _validate_manifest_metadata(manifest)
    recorded_inputs = manifest.get("inputs")
    if not isinstance(recorded_inputs, list) or not recorded_inputs:
        raise GuardError("problem manifest inputs must be a non-empty list")

    recorded_declarations: list[tuple[str, str]] = []
    for item in recorded_inputs:
        if not isinstance(item, dict):
            raise GuardError("problem manifest contains a non-object input")
        role = item.get("role")
        path = item.get("path")
        if not isinstance(role, str) or not isinstance(path, str):
            raise GuardError("problem manifest input is missing role or path")
        if not ROLE_RE.fullmatch(role):
            raise GuardError(f"problem manifest contains an invalid role: {role!r}")
        if item.get("verification") not in {"unverified", "verified"}:
            raise GuardError(f"invalid verification status for {role}={path}")
        if item.get("verification") == "verified" and (
            not item.get("verification_actor") or not item.get("verified_at")
        ):
            raise GuardError(f"verified input lacks actor or timestamp: {role}={path}")
        recorded_declarations.append((role, path))

    rebuilt = _build_input_records(root, recorded_declarations, output_dir)
    if _input_hash_material(rebuilt) != _input_hash_material(recorded_inputs):
        raise GuardError("declared project inputs changed since initialization")
    expected_root_hash = _sha256_bytes(
        _canonical_json_bytes(_input_hash_material(rebuilt))
    )
    if manifest.get("root_hash") != expected_root_hash:
        raise GuardError("problem manifest root_hash is invalid")

    explicit_values = list(explicit_inputs)
    if explicit_values:
        requested = _build_input_records(
            root, _parse_inputs(explicit_values), output_dir
        )
        if _input_hash_material(requested) != _input_hash_material(recorded_inputs):
            raise GuardError("explicit --input allowlist differs from the manifest")

    manifest_sha256 = _sha256_file(manifest_path)
    _verify_references(output_dir, manifest, manifest_sha256)
    return manifest, manifest_sha256


def _atomic_replace_existing(output_dir: Path, contents: dict[str, bytes]) -> None:
    targets = {name: output_dir / name for name in contents}
    for target in targets.values():
        if not target.is_file() or _is_link_like(target):
            raise GuardError(f"refusing to replace missing or unsafe file: {target}")
    originals = {name: path.read_bytes() for name, path in targets.items()}
    staged: dict[str, Path] = {}
    replaced: list[str] = []
    try:
        for name, data in contents.items():
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{name}.", dir=output_dir, delete=False
            ) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                staged[name] = Path(stream.name)
        for name, target in targets.items():
            os.replace(staged[name], target)
            replaced.append(name)
    except Exception:
        for name in replaced:
            target = targets[name]
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{name}.rollback.", dir=output_dir, delete=False
            ) as stream:
                rollback = Path(stream.name)
                stream.write(originals[name])
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(rollback, target)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def _refresh_provenance(
    existing: str, manifest: dict[str, Any], manifest_sha256: str
) -> str:
    marker = "## Additional sources"
    if marker not in existing:
        raise GuardError("PROVENANCE.md is missing the Additional sources section")
    generated = _provenance_markdown(manifest, manifest_sha256)
    return generated.split(marker, 1)[0] + marker + existing.split(marker, 1)[1]


def _replace_manifest_reference(text: str, old_hash: str, new_hash: str, label: str) -> str:
    if old_hash not in text:
        raise GuardError(f"{label} does not contain the previous manifest hash")
    return text.replace(old_hash, new_hash)


def command_init(args: argparse.Namespace) -> None:
    root = _project_root(args.project_root)
    declarations = _parse_inputs(args.input)
    output_dir = _output_directory(root, args.output_dir, create=False)
    records = _build_input_records(root, declarations, output_dir)
    output_dir = _output_directory(root, args.output_dir, create=True)
    project_id = _clean_label(args.project_id, "project_id")
    review_mode = args.review_mode
    if args.mode == "live-competition":
        if review_mode not in (None, "human-supervised"):
            raise GuardError("live-competition requires human-supervised review mode")
        review_mode = "human-supervised"
    elif review_mode is None:
        raise GuardError("--review-mode is required unless mode is live-competition")
    manifest = _problem_manifest(
        records,
        project_id=project_id,
        mode=args.mode,
        task_provenance=args.task_provenance,
        runtime_isolation=args.runtime_isolation,
        review_mode=review_mode,
    )
    _write_files(output_dir, _guard_contents(manifest), args.overwrite)
    print(f"initialized project guard in {output_dir}")
    print(f"problem root hash: {manifest['root_hash']}")


def command_verify(args: argparse.Namespace) -> None:
    root = _project_root(args.project_root)
    output_dir = _lexical_path(root, args.output_dir)
    manifest, _ = _load_validated_guard(root, output_dir, args.input)
    print(f"verified project guard in {output_dir}")
    print(f"problem root hash: {manifest['root_hash']}")


def command_mark_verified(args: argparse.Namespace) -> None:
    root = _project_root(args.project_root)
    output_dir = _lexical_path(root, args.output_dir)
    manifest, old_manifest_sha256 = _load_validated_guard(root, output_dir)
    requested = _parse_inputs(args.input)
    actor = _clean_label(args.actor, "actor")
    note = args.note.strip()
    recorded = {
        (str(item["role"]), str(item["path"])): item
        for item in manifest["inputs"]
    }
    selected: list[dict[str, Any]] = []
    for role, raw_path in requested:
        path = _existing_path(root, raw_path)
        key = (role, _relative(path, root))
        item = recorded.get(key)
        if item is None:
            raise GuardError(f"input is not in the manifest allowlist: {role}={key[1]}")
        if item.get("verification") == "verified":
            raise GuardError(f"input is already verified: {role}={key[1]}")
        selected.append(item)

    verified_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for item in selected:
        item["verification"] = "verified"
        item["verification_actor"] = actor
        item["verification_note"] = note
        item["verified_at"] = verified_at

    manifest_bytes = _render_json(manifest)
    new_manifest_sha256 = _sha256_bytes(manifest_bytes)
    provenance = _refresh_provenance(
        (output_dir / "PROVENANCE.md").read_text(encoding="utf-8"),
        manifest,
        new_manifest_sha256,
    )
    decision_log = _replace_manifest_reference(
        (output_dir / "DECISION_LOG.md").read_text(encoding="utf-8"),
        old_manifest_sha256,
        new_manifest_sha256,
        "DECISION_LOG.md",
    )
    contents: dict[str, bytes] = {
        "PROBLEM_MANIFEST.json": manifest_bytes,
        "PROVENANCE.md": provenance.encode("utf-8"),
        "DECISION_LOG.md": decision_log.encode("utf-8"),
    }
    for name in ("HANDOFF.json", "STAGE_GATES.json", "HUMAN_REVIEW.json"):
        document = _read_json(output_dir / name)
        document["problem_manifest_sha256"] = new_manifest_sha256
        document["problem_root_hash"] = manifest["root_hash"]
        if "project_id" in document:
            document["project_id"] = manifest["project_id"]
        if "review_mode" in document:
            document["review_mode"] = manifest["review_mode"]
        if name == "STAGE_GATES.json":
            for stage in document.get("stages", []):
                if isinstance(stage, dict):
                    upstream = stage.setdefault("upstream_hashes", {})
                    if isinstance(upstream, dict):
                        upstream["problem_manifest_sha256"] = new_manifest_sha256
                        upstream["problem_root_hash"] = manifest["root_hash"]
        contents[name] = _render_json(document)
    current = _read_json(output_dir / "CURRENT_VERSIONS.json")
    current["problem_root_hash"] = manifest["root_hash"]
    contents["CURRENT_VERSIONS.json"] = _render_json(current)

    _atomic_replace_existing(output_dir, contents)
    _load_validated_guard(root, output_dir)
    print(f"marked {len(selected)} input(s) verified")
    print(f"problem manifest SHA-256: {new_manifest_sha256}")


def command_review(args: argparse.Namespace) -> None:
    root = _project_root(args.project_root)
    output_dir = _lexical_path(root, args.output_dir)
    manifest, manifest_sha256 = _load_validated_guard(root, output_dir)
    if args.status == "WAIVED_FOR_SIMULATION" and manifest["mode"] == "live-competition":
        raise GuardError("live-competition checkpoints cannot be waived for simulation")
    if args.status == "WAIVED_FOR_SIMULATION" and manifest["review_mode"] != "autonomous-simulation":
        raise GuardError(
            "WAIVED_FOR_SIMULATION requires autonomous-simulation review mode"
        )
    if args.status != "WAIVED_FOR_SIMULATION" and args.reviewer_type == "simulation-orchestrator":
        raise GuardError("competition review decisions require a human reviewer")
    reviewer = _clean_label(args.reviewer, "reviewer")
    source_id = _clean_label(args.source_id, "source_id")
    scope = _clean_label(args.scope, "scope", maximum=2000)
    comments = _clean_label(args.comments, "comments", maximum=4000)
    evidence = [_clean_label(item, "evidence", maximum=1000) for item in args.evidence]

    review_path = output_dir / "HUMAN_REVIEW.json"
    review = _read_json(review_path)
    authorship = review.get("authorship")
    if not isinstance(authorship, dict):
        raise GuardError("HUMAN_REVIEW.json authorship is invalid")
    authorship_type = (
        "simulation-orchestrator"
        if args.reviewer_type == "simulation-orchestrator"
        else "human"
    )
    agent_generated = args.reviewer_type == "simulation-orchestrator"
    if authorship.get("type") not in (None, authorship_type) or authorship.get(
        "agent_generated"
    ) not in (None, agent_generated):
        raise GuardError("review authorship conflicts with an earlier checkpoint")
    authorship.update(
        {
            "type": authorship_type,
            "agent_generated": agent_generated,
            "note": "Recorded through project_guard review; decisions are immutable.",
        }
    )
    checkpoints = review.get("checkpoints")
    if not isinstance(checkpoints, list):
        raise GuardError("HUMAN_REVIEW.json checkpoints are invalid")
    checkpoint = next(
        (
            item
            for item in checkpoints
            if isinstance(item, dict) and item.get("id") == args.checkpoint
        ),
        None,
    )
    if checkpoint is None:
        raise GuardError(f"unknown review checkpoint: {args.checkpoint}")
    if checkpoint.get("status") != "PENDING":
        raise GuardError(
            f"review checkpoint is already decided and immutable: {args.checkpoint}"
        )
    reviewed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    approval_material = {
        "checkpoint": args.checkpoint,
        "comments": comments,
        "evidence": evidence,
        "problem_manifest_sha256": manifest_sha256,
        "reviewed_at": reviewed_at,
        "reviewer": reviewer,
        "reviewer_type": args.reviewer_type,
        "scope": scope,
        "source_id": source_id,
        "status": args.status,
    }
    approval_id = "review:" + args.checkpoint + ":" + _sha256_bytes(
        _canonical_json_bytes(approval_material)
    )
    checkpoint.update(
        {
            "approval_id": approval_id,
            "comments": comments,
            "evidence": evidence,
            "reviewed_at": reviewed_at,
            "reviewer": reviewer,
            "reviewed_by": reviewer,
            "reviewer_type": "human" if not agent_generated else "simulation-orchestrator",
            "entered_by": args.reviewer_type,
            "scope": scope,
            "source_id": source_id,
            "status": args.status,
        }
    )
    _atomic_replace_existing(review_path.parent, {review_path.name: _render_json(review)})
    _load_validated_guard(root, output_dir)
    print(f"recorded review checkpoint: {args.checkpoint}={args.status}")
    print(f"approval ID: {approval_id}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Initialize or verify a hash-pinned project input allowlist and "
            "workflow guard files."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create guard files")
    init_parser.add_argument(
        "--project-root", "--root", dest="project_root", default=".", help="project root"
    )
    init_parser.add_argument(
        "--output-dir",
        default="reports",
        help="guard file directory, relative to the project root",
    )
    init_parser.add_argument("--project-id", required=True, help="stable project identifier")
    init_parser.add_argument("--mode", required=True, choices=MODES)
    init_parser.add_argument(
        "--task-provenance", required=True, choices=TASK_PROVENANCE_VALUES
    )
    init_parser.add_argument(
        "--runtime-isolation", required=True, choices=RUNTIME_ISOLATION_VALUES
    )
    init_parser.add_argument(
        "--review-mode",
        choices=REVIEW_MODES,
        help=(
            "review policy; live-competition defaults to and requires "
            "human-supervised"
        ),
    )
    init_parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="ROLE=PATH",
        help="allowed project input; repeat for each file or directory",
    )
    init_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace existing guard files (disabled by default)",
    )
    init_parser.set_defaults(handler=command_init)

    verify_parser = subparsers.add_parser("verify", help="verify guard files")
    verify_parser.add_argument(
        "--project-root", "--root", dest="project_root", default=".", help="project root"
    )
    verify_parser.add_argument(
        "--output-dir",
        default="reports",
        help="guard file directory, relative to the project root",
    )
    verify_parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="ROLE=PATH",
        help="optionally require an exact explicit allowlist",
    )
    verify_parser.set_defaults(handler=command_verify)

    mark_parser = subparsers.add_parser(
        "mark-verified",
        help="atomically mark explicitly named manifest inputs as verified",
    )
    mark_parser.add_argument(
        "--project-root", "--root", dest="project_root", default=".", help="project root"
    )
    mark_parser.add_argument(
        "--output-dir",
        default="reports",
        help="guard file directory, relative to the project root",
    )
    mark_parser.add_argument(
        "--input",
        action="append",
        default=[],
        metavar="ROLE=PATH",
        help="exact allowlisted input to mark; repeat as needed",
    )
    mark_parser.add_argument("--actor", required=True, help="verification actor")
    mark_parser.add_argument("--note", default="", help="verification note")
    mark_parser.set_defaults(handler=command_mark_verified)

    review_parser = subparsers.add_parser(
        "review",
        help="record one immutable human-review checkpoint decision",
    )
    review_parser.add_argument(
        "--project-root", "--root", dest="project_root", default=".", help="project root"
    )
    review_parser.add_argument(
        "--output-dir",
        default="reports",
        help="guard file directory, relative to the project root",
    )
    review_parser.add_argument("--checkpoint", required=True, choices=REVIEW_CHECKPOINTS)
    review_parser.add_argument(
        "--status",
        required=True,
        choices=("APPROVED", "CHANGES_REQUESTED", "WAIVED_FOR_SIMULATION"),
    )
    review_parser.add_argument("--reviewer", required=True, help="human reviewer identity")
    review_parser.add_argument(
        "--reviewer-type", required=True, choices=REVIEWER_TYPES
    )
    review_parser.add_argument(
        "--source-id", required=True, help="external review record or controlled UI ID"
    )
    review_parser.add_argument("--scope", required=True, help="what was actually reviewed")
    review_parser.add_argument(
        "--comments", required=True, help="review rationale or requested changes"
    )
    review_parser.add_argument(
        "--evidence",
        action="append",
        required=True,
        metavar="PATH_OR_HASH",
        help="reviewed artifact path/hash; repeat when needed",
    )
    review_parser.set_defaults(handler=command_review)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.handler(args)
    except (GuardError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
