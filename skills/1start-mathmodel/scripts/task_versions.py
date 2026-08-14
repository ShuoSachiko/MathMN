#!/usr/bin/env python3
"""Content-addressed, append-only task version snapshots and selections."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterable


SCHEMA_VERSION = 1
STORE_NAME = ".task_versions"
CURRENT_PATH = Path("reports/CURRENT_VERSIONS.json")
DECISIONS_PATH = Path("reports/VERSION_DECISIONS.jsonl")
PROBLEM_MANIFEST_PATH = Path("reports/PROBLEM_MANIFEST.json")
ROLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class VersionError(ValueError):
    """Raised when a version operation violates an integrity invariant."""


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _sha256_file(path: Path) -> tuple[str, int]:
    with path.open("rb") as stream:
        return _hash_stream(stream)


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


def _assert_link_free(path: Path, root: Path) -> None:
    if not _is_within(path, root):
        raise VersionError(f"path escapes project root: {path}")
    current = root
    if _is_link_like(current):
        raise VersionError(f"project root is a symbolic link or junction: {root}")
    for part in path.relative_to(root).parts:
        current = current / part
        if current.exists() and _is_link_like(current):
            raise VersionError(
                f"symbolic links, junctions, and reparse points are forbidden: {current}"
            )


def _project_root(raw_root: str) -> Path:
    root = Path(os.path.abspath(os.path.expanduser(raw_root)))
    if not root.exists() or not root.is_dir():
        raise VersionError(f"project root is not an existing directory: {root}")
    _assert_link_free(root, root)
    resolved = root.resolve(strict=True)
    if _norm(resolved) != _norm(root):
        raise VersionError(f"project root resolves through a link or junction: {root}")
    return root


def _lexical_path(root: Path, raw_path: str) -> Path:
    expanded = Path(os.path.expanduser(raw_path))
    candidate = expanded if expanded.is_absolute() else root / expanded
    candidate = Path(os.path.abspath(candidate))
    if not _is_within(candidate, root):
        raise VersionError(f"path escapes project root: {raw_path}")
    return candidate


def _existing_path(root: Path, raw_path: str) -> Path:
    path = _lexical_path(root, raw_path)
    if not path.exists():
        raise VersionError(f"snapshot path does not exist: {raw_path}")
    _assert_link_free(path, root)
    resolved = path.resolve(strict=True)
    if _norm(resolved) != _norm(path) or not _is_within(resolved, root):
        raise VersionError(f"snapshot path resolves outside the project root: {raw_path}")
    if not (path.is_file() or path.is_dir()):
        raise VersionError(f"snapshot path is not a regular file or directory: {raw_path}")
    return path


def _relative(path: Path, root: Path) -> str:
    value = path.relative_to(root).as_posix()
    return value or "."


def _clean_text(value: str, label: str, *, maximum: int = 4096) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise VersionError(f"{label} must contain 1 to {maximum} characters")
    if "\x00" in cleaned or any(ord(character) < 32 and character not in "\t" for character in cleaned):
        raise VersionError(f"{label} contains a forbidden control character")
    return cleaned


def _clean_id(value: str, label: str) -> str:
    if not ID_RE.fullmatch(value):
        raise VersionError(
            f"{label} must use 1-128 ASCII letters, digits, '.', '_' or '-'"
        )
    return value


def _parse_paths(values: Iterable[str]) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        if "=" not in value:
            raise VersionError(f"snapshot path must use ROLE=PATH syntax: {value!r}")
        role, raw_path = value.split("=", 1)
        role = role.strip()
        raw_path = raw_path.strip()
        if not ROLE_RE.fullmatch(role):
            raise VersionError(f"invalid snapshot role: {role!r}")
        if not raw_path:
            raise VersionError(f"empty snapshot path for role {role!r}")
        item = (role, raw_path)
        if item in seen:
            raise VersionError(f"duplicate snapshot declaration: {value!r}")
        seen.add(item)
        declarations.append(item)
    if not declarations:
        raise VersionError("at least one explicit --path ROLE=PATH is required")
    return declarations


def _control_paths(root: Path) -> tuple[Path, ...]:
    return (
        root / STORE_NAME,
        root / CURRENT_PATH,
        root / DECISIONS_PATH,
    )


def _reject_control_overlap(path: Path, root: Path) -> None:
    for control in _control_paths(root):
        if _norm(path) == _norm(control) or (
            control.name == STORE_NAME and _is_within(path, control)
        ):
            raise VersionError(f"version control state cannot be snapshotted: {path}")
        if path.is_dir() and _is_within(control, path):
            raise VersionError(
                "a snapshot directory may not contain version control state: "
                f"{path}"
            )


def _enumerate_snapshot_files(
    root: Path, declarations: Iterable[tuple[str, str]]
) -> tuple[list[dict[str, str]], list[tuple[str, Path]]]:
    sources: list[dict[str, str]] = []
    files: list[tuple[str, Path]] = []
    seen_sources: set[tuple[str, str]] = set()
    seen_files: set[tuple[str, str]] = set()
    for role, raw_path in declarations:
        path = _existing_path(root, raw_path)
        _reject_control_overlap(path, root)
        relative = _relative(path, root)
        source_key = (role, relative)
        if source_key in seen_sources:
            raise VersionError(f"duplicate normalized snapshot source: {role}={relative}")
        seen_sources.add(source_key)
        sources.append(
            {"role": role, "path": relative, "type": "file" if path.is_file() else "directory"}
        )
        candidates: list[Path] = []
        if path.is_file():
            candidates.append(path)
        else:
            for current_text, directory_names, file_names in os.walk(
                path, followlinks=False
            ):
                current = Path(current_text)
                _assert_link_free(current, root)
                for name in sorted(directory_names):
                    child = current / name
                    if _is_link_like(child):
                        raise VersionError(
                            f"symbolic link or junction in snapshot directory: {child}"
                        )
                    _reject_control_overlap(child, root)
                for name in sorted(file_names):
                    child = current / name
                    if _is_link_like(child) or not child.is_file():
                        raise VersionError(f"non-regular snapshot file: {child}")
                    _reject_control_overlap(child, root)
                    candidates.append(child)
        for candidate in candidates:
            key = (role, _relative(candidate, root))
            if key in seen_files:
                raise VersionError(
                    f"overlapping declarations duplicate snapshot file: {role}={key[1]}"
                )
            seen_files.add(key)
            files.append((role, candidate))
    if not files:
        raise VersionError("explicit snapshot declarations contain no regular files")
    sources.sort(key=lambda item: (item["role"], item["path"]))
    files.sort(key=lambda item: (item[0], _relative(item[1], root)))
    return sources, files


def _store_root(root: Path, *, create: bool) -> Path:
    store = root / STORE_NAME
    if create:
        store.mkdir(exist_ok=True)
    if not store.exists() or not store.is_dir() or _is_link_like(store):
        raise VersionError(f"version store is missing or unsafe: {store}")
    _assert_link_free(store, root)
    return store


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    return all(getattr(left, field, None) == getattr(right, field, None) for field in fields)


def _store_object(source: Path, root: Path, store: Path) -> tuple[str, int]:
    objects = store / "objects"
    objects.mkdir(exist_ok=True)
    before = os.stat(source, follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode) or _is_link_like(source):
        raise VersionError(f"snapshot source became unsafe: {source}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".object-", dir=objects)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
            opened = os.fstat(input_stream.fileno())
            if not _same_identity(before, opened):
                raise VersionError(f"snapshot source changed before reading: {source}")
            digest = hashlib.sha256()
            size = 0
            for chunk in iter(lambda: input_stream.read(1024 * 1024), b""):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            output.flush()
            os.fsync(output.fileno())
            after_open = os.fstat(input_stream.fileno())
        after_path = os.stat(source, follow_symlinks=False)
        if not _same_identity(opened, after_open) or not _same_identity(opened, after_path):
            raise VersionError(f"snapshot source changed while reading: {source}")
        sha256 = digest.hexdigest()
        object_parent = objects / sha256[:2]
        object_parent.mkdir(exist_ok=True)
        target = object_parent / sha256
        if target.exists():
            if _is_link_like(target) or not target.is_file():
                raise VersionError(f"unsafe object already exists: {target}")
            existing_hash, existing_size = _sha256_file(target)
            if existing_hash != sha256 or existing_size != size:
                raise VersionError(f"content-addressed object is corrupt: {target}")
        else:
            try:
                os.link(temporary, target)
            except FileExistsError:
                existing_hash, existing_size = _sha256_file(target)
                if existing_hash != sha256 or existing_size != size:
                    raise VersionError(f"content-addressed object race is corrupt: {target}")
        return sha256, size
    finally:
        temporary.unlink(missing_ok=True)


def _version_payload(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "version_hash"}


def _version_path(store: Path, task_id: str, version_hash: str) -> Path:
    return store / "tasks" / task_id / "versions" / f"{version_hash}.json"


def _normalize_recorded_path(raw: object) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise VersionError(f"invalid stored relative path: {raw!r}")
    if raw.startswith("/") or ":" in raw:
        raise VersionError(f"absolute or stream-like stored path is forbidden: {raw!r}")
    parts = raw.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise VersionError(f"stored path traversal is forbidden: {raw!r}")
    return "/".join(parts)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    if not path.exists() or not path.is_file() or _is_link_like(path):
        raise VersionError(f"{label} is missing or unsafe: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise VersionError(f"cannot read valid JSON from {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise VersionError(f"{label} must be a JSON object: {path}")
    return value


def _load_version(root: Path, task_id: str, version_hash: str) -> dict[str, Any]:
    task_id = _clean_id(task_id, "task_id")
    if not SHA256_RE.fullmatch(version_hash):
        raise VersionError("version hash must be 64 lowercase hexadecimal characters")
    store = _store_root(root, create=False)
    path = _version_path(store, task_id, version_hash)
    _assert_link_free(path, root)
    document = _load_json(path, "version")
    if document.get("schema_version") != SCHEMA_VERSION:
        raise VersionError(f"unsupported version schema in {path}")
    if document.get("task_id") != task_id or document.get("version_hash") != version_hash:
        raise VersionError(f"version identity mismatch in {path}")
    if _sha256_bytes(_canonical_json_bytes(_version_payload(document))) != version_hash:
        raise VersionError(f"version metadata hash mismatch in {path}")
    _clean_id(str(document.get("branch", "")), "branch")
    parent = document.get("parent")
    if parent is not None and (not isinstance(parent, str) or not SHA256_RE.fullmatch(parent)):
        raise VersionError(f"version has an invalid parent hash: {path}")
    files = document.get("files")
    if not isinstance(files, list) or not files:
        raise VersionError(f"version has no file records: {path}")
    normalized_files: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"role", "path", "size", "sha256"}:
            raise VersionError(f"version contains an invalid file record: {path}")
        role = item.get("role")
        digest = item.get("sha256")
        size = item.get("size")
        if not isinstance(role, str) or not ROLE_RE.fullmatch(role):
            raise VersionError(f"version contains an invalid file role: {path}")
        stored_path = _normalize_recorded_path(item.get("path"))
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise VersionError(f"version contains an invalid object digest: {path}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise VersionError(f"version contains an invalid file size: {path}")
        normalized_files.append(
            {"role": role, "path": stored_path, "size": size, "sha256": digest}
        )
    keys = [(item["role"], item["path"]) for item in normalized_files]
    if keys != sorted(keys) or len(keys) != len(set(keys)):
        raise VersionError(f"version file records are not sorted and unique: {path}")
    if document.get("content_root_hash") != _sha256_bytes(
        _canonical_json_bytes(normalized_files)
    ):
        raise VersionError(f"version content_root_hash is invalid: {path}")
    return document


def _write_exclusive(path: Path, data: bytes, root: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _assert_link_free(path.parent, root)
    try:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError as exc:
        raise VersionError(f"refusing to overwrite immutable version metadata: {path}") from exc


def command_snapshot(args: argparse.Namespace) -> dict[str, Any]:
    root = _project_root(args.project_root)
    task_id = _clean_id(args.task_id, "task_id")
    branch = _clean_id(args.branch, "branch")
    actor = _clean_text(args.actor, "actor", maximum=256)
    message = _clean_text(args.message, "message")
    declarations = _parse_paths(args.path)
    sources, source_files = _enumerate_snapshot_files(root, declarations)
    store = _store_root(root, create=True)
    parent = args.parent
    if parent is not None:
        _load_version(root, task_id, parent)

    files: list[dict[str, Any]] = []
    for role, source in source_files:
        digest, size = _store_object(source, root, store)
        files.append(
            {
                "role": role,
                "path": _relative(source, root),
                "size": size,
                "sha256": digest,
            }
        )
    files.sort(key=lambda item: (item["role"], item["path"]))
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "branch": branch,
        "parent": parent,
        "actor": actor,
        "message": message,
        "timestamp": _utc_now(),
        "sources": sources,
        "files": files,
        "content_root_hash": _sha256_bytes(_canonical_json_bytes(files)),
    }
    version_hash = _sha256_bytes(_canonical_json_bytes(payload))
    document = dict(payload)
    document["version_hash"] = version_hash
    version_path = _version_path(store, task_id, version_hash)
    _write_exclusive(version_path, _render_json(document), root)
    return {
        "branch": branch,
        "content_root_hash": document["content_root_hash"],
        "file_count": len(files),
        "parent": parent,
        "task_id": task_id,
        "version_hash": version_hash,
    }


def _all_versions(root: Path, task_filter: str | None) -> list[dict[str, Any]]:
    store = root / STORE_NAME
    if not store.exists():
        return []
    store = _store_root(root, create=False)
    tasks_root = store / "tasks"
    if not tasks_root.exists():
        return []
    if _is_link_like(tasks_root) or not tasks_root.is_dir():
        raise VersionError(f"unsafe task metadata directory: {tasks_root}")
    task_ids = [task_filter] if task_filter else sorted(path.name for path in tasks_root.iterdir())
    versions: list[dict[str, Any]] = []
    for task_id in task_ids:
        if task_id is None:
            continue
        _clean_id(task_id, "task_id")
        directory = tasks_root / task_id / "versions"
        if not directory.exists():
            continue
        _assert_link_free(directory, root)
        for path in sorted(directory.glob("*.json")):
            version_hash = path.stem
            versions.append(_load_version(root, task_id, version_hash))
    versions.sort(key=lambda item: (item["task_id"], item["timestamp"], item["version_hash"]))
    return versions


def command_list(args: argparse.Namespace) -> dict[str, Any]:
    root = _project_root(args.project_root)
    task_id = _clean_id(args.task_id, "task_id") if args.task_id else None
    branch = _clean_id(args.branch, "branch") if args.branch else None
    versions = _all_versions(root, task_id)
    if branch:
        versions = [item for item in versions if item.get("branch") == branch]
    summaries = [
        {
            "actor": item["actor"],
            "branch": item["branch"],
            "content_root_hash": item["content_root_hash"],
            "file_count": len(item["files"]),
            "message": item["message"],
            "parent": item["parent"],
            "task_id": item["task_id"],
            "timestamp": item["timestamp"],
            "version_hash": item["version_hash"],
        }
        for item in versions
    ]
    return {"count": len(summaries), "versions": summaries}


def command_diff(args: argparse.Namespace) -> dict[str, Any]:
    root = _project_root(args.project_root)
    task_id = _clean_id(args.task_id, "task_id")
    left = _load_version(root, task_id, args.from_version)
    right = _load_version(root, task_id, args.to_version)
    left_files = {(item["role"], item["path"]): item for item in left["files"]}
    right_files = {(item["role"], item["path"]): item for item in right["files"]}
    left_keys = set(left_files)
    right_keys = set(right_files)
    changed = []
    for key in sorted(left_keys & right_keys):
        if left_files[key] != right_files[key]:
            changed.append({"role": key[0], "path": key[1], "from": left_files[key], "to": right_files[key]})
    return {
        "task_id": task_id,
        "from_version": args.from_version,
        "to_version": args.to_version,
        "added": [right_files[key] for key in sorted(right_keys - left_keys)],
        "removed": [left_files[key] for key in sorted(left_keys - right_keys)],
        "changed": changed,
        "unchanged_count": sum(
            left_files[key] == right_files[key] for key in left_keys & right_keys
        ),
    }


def _safe_object(store: Path, digest: str, expected_size: int) -> Path:
    if not SHA256_RE.fullmatch(digest):
        raise VersionError(f"invalid object digest in version: {digest!r}")
    path = store / "objects" / digest[:2] / digest
    if not path.exists() or not path.is_file() or _is_link_like(path):
        raise VersionError(f"version object is missing or unsafe: {path}")
    actual_hash, actual_size = _sha256_file(path)
    if actual_hash != digest or actual_size != expected_size:
        raise VersionError(f"version object is corrupt: {path}")
    return path


def command_materialize(args: argparse.Namespace) -> dict[str, Any]:
    root = _project_root(args.project_root)
    task_id = _clean_id(args.task_id, "task_id")
    version = _load_version(root, task_id, args.version)
    raw_destination = args.destination or f"materialized/{task_id}-{args.version[:12]}"
    destination = _lexical_path(root, raw_destination)
    if destination.exists():
        raise VersionError(f"materialize destination already exists: {destination}")
    if _is_within(destination, root / STORE_NAME):
        raise VersionError("materialize destination cannot be inside the version store")
    ancestor = destination.parent
    while not ancestor.exists() and ancestor != root:
        ancestor = ancestor.parent
    _assert_link_free(ancestor, root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _assert_link_free(destination.parent, root)
    store = _store_root(root, create=False)
    objects = [
        (
            item,
            _safe_object(store, str(item["sha256"]), int(item["size"])),
        )
        for item in version["files"]
    ]
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        for item, object_path in objects:
            stored_path = _normalize_recorded_path(item["path"])
            target = temporary.joinpath(*stored_path.split("/"))
            if not _is_within(target, temporary):
                raise VersionError(f"stored path escapes materialization root: {item['path']}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with object_path.open("rb") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
        os.rename(temporary, destination)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return {
        "destination": str(destination),
        "file_count": len(objects),
        "task_id": task_id,
        "version_hash": args.version,
    }


def _load_current(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION, "selections": {}, "selection_hash": _sha256_bytes(_canonical_json_bytes({}))}
    document = _load_json(path, "current version selections")
    if document.get("schema_version") != SCHEMA_VERSION or not isinstance(document.get("selections"), dict):
        raise VersionError("CURRENT_VERSIONS.json has an unsupported schema")
    expected = _sha256_bytes(_canonical_json_bytes(document["selections"]))
    if document.get("selection_hash") != expected:
        raise VersionError("CURRENT_VERSIONS.json selection_hash is invalid")
    return document


def _decision_payload(document: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in document.items() if key != "event_hash"}


def _load_decisions(path: Path) -> tuple[bytes, list[dict[str, Any]]]:
    if not path.exists():
        return b"", []
    if not path.is_file() or _is_link_like(path):
        raise VersionError(f"version decision log is unsafe: {path}")
    data = path.read_bytes()
    if data and not data.endswith(b"\n"):
        raise VersionError("VERSION_DECISIONS.jsonl must end with a newline")
    events: list[dict[str, Any]] = []
    previous_hash: str | None = None
    for index, line in enumerate(data.splitlines(), 1):
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VersionError(f"invalid VERSION_DECISIONS.jsonl line {index}: {exc}") from exc
        if not isinstance(value, dict):
            raise VersionError(f"VERSION_DECISIONS.jsonl line {index} is not an object")
        if value.get("seq") != index or value.get("action") != "select":
            raise VersionError(f"invalid decision sequence or action on line {index}")
        if value.get("previous_event_hash") != previous_hash:
            raise VersionError(f"broken decision hash chain on line {index}")
        event_hash = value.get("event_hash")
        if not isinstance(event_hash, str) or not SHA256_RE.fullmatch(event_hash):
            raise VersionError(f"invalid decision event_hash on line {index}")
        if _sha256_bytes(_canonical_json_bytes(_decision_payload(value))) != event_hash:
            raise VersionError(f"decision event_hash mismatch on line {index}")
        previous_hash = event_hash
        events.append(value)
    return data, events


def _replace_transaction(root: Path, replacements: dict[Path, bytes]) -> None:
    staged: dict[Path, Path] = {}
    originals: dict[Path, bytes | None] = {}
    replaced: list[Path] = []
    try:
        for target, data in replacements.items():
            if target.exists() and (not target.is_file() or _is_link_like(target)):
                raise VersionError(f"refusing to replace unsafe control file: {target}")
            originals[target] = target.read_bytes() if target.exists() else None
            with tempfile.NamedTemporaryFile(
                mode="wb", prefix=f".{target.name}.", dir=target.parent, delete=False
            ) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
                staged[target] = Path(stream.name)
        for target, temporary in staged.items():
            os.replace(temporary, target)
            replaced.append(target)
    except Exception:
        for target in replaced:
            previous = originals[target]
            if previous is None:
                target.unlink(missing_ok=True)
            else:
                with tempfile.NamedTemporaryFile(
                    mode="wb", prefix=f".{target.name}.rollback.", dir=target.parent, delete=False
                ) as stream:
                    rollback = Path(stream.name)
                    stream.write(previous)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(rollback, target)
        raise
    finally:
        for temporary in staged.values():
            temporary.unlink(missing_ok=True)


def command_select(args: argparse.Namespace) -> dict[str, Any]:
    root = _project_root(args.project_root)
    task_id = _clean_id(args.task_id, "task_id")
    actor = _clean_text(args.actor, "actor", maximum=256)
    message = _clean_text(args.message, "message")
    version = _load_version(root, task_id, args.version)
    problem_manifest = _load_json(root / PROBLEM_MANIFEST_PATH, "problem manifest")
    review_mode = problem_manifest.get("review_mode")
    if review_mode == "human-supervised":
        if not args.human_review_ref:
            raise VersionError("human-supervised selection requires --human-review-ref")
        review_reference = _clean_text(args.human_review_ref, "human_review_ref")
        human_review = _load_json(root / "reports/HUMAN_REVIEW.json", "human review")
        if human_review.get("problem_root_hash") != problem_manifest.get("root_hash"):
            raise VersionError("HUMAN_REVIEW.json is stale for the current problem manifest")
        checkpoints = human_review.get("checkpoints")
        if not isinstance(checkpoints, list):
            raise VersionError("HUMAN_REVIEW.json checkpoints are invalid")
        matched = next(
            (
                item
                for item in checkpoints
                if isinstance(item, dict)
                and item.get("approval_id") == review_reference
                and item.get("status") == "APPROVED"
                and item.get("reviewer_type") in {"human", "controlled-human-review-ui"}
            ),
            None,
        )
        if matched is None:
            raise VersionError(
                "--human-review-ref must match an APPROVED human checkpoint approval_id"
            )
        waiver = None
    elif review_mode == "autonomous-simulation":
        if not args.simulation_waiver:
            raise VersionError("autonomous-simulation selection requires --simulation-waiver")
        waiver = _clean_text(args.simulation_waiver, "simulation_waiver")
        review_reference = None
    else:
        raise VersionError("problem manifest has an unsupported review_mode")

    reports = root / "reports"
    if not reports.exists() or not reports.is_dir() or _is_link_like(reports):
        raise VersionError(f"reports directory is missing or unsafe: {reports}")
    current_path = root / CURRENT_PATH
    decisions_path = root / DECISIONS_PATH
    current = _load_current(current_path)
    decisions, events = _load_decisions(decisions_path)
    selected_at = _utc_now()
    previous = current["selections"].get(task_id)
    selection = {
        "actor": actor,
        "branch": version["branch"],
        "human_review_ref": review_reference,
        "message": message,
        "review_mode": review_mode,
        "selected_at": selected_at,
        "simulation_waiver": waiver,
        "task_id": task_id,
        "version_hash": args.version,
    }
    current["selections"][task_id] = selection
    selection_hash = _sha256_bytes(_canonical_json_bytes(current["selections"]))
    current["selection_hash"] = selection_hash
    decision_payload = {
        "action": "select",
        "actor": actor,
        "branch": version["branch"],
        "human_review_ref": review_reference,
        "message": message,
        "previous_version_hash": previous.get("version_hash") if isinstance(previous, dict) else None,
        "previous_event_hash": events[-1]["event_hash"] if events else None,
        "review_mode": review_mode,
        "selected_at": selected_at,
        "selected_version_hash": args.version,
        "selection_hash": selection_hash,
        "seq": len(events) + 1,
        "simulation_waiver": waiver,
        "task_id": task_id,
    }
    decision = dict(decision_payload)
    decision["event_hash"] = _sha256_bytes(
        _canonical_json_bytes(decision_payload)
    )
    decision_line = _canonical_json_bytes(decision) + b"\n"
    _replace_transaction(
        root,
        {
            current_path: _render_json(current),
            decisions_path: decisions + decision_line,
        },
    )
    return {
        "branch": version["branch"],
        "selection_hash": selection_hash,
        "task_id": task_id,
        "version_hash": args.version,
    }


def _add_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-root", "--root", dest="project_root", default=".", help="project root"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create and select immutable, content-addressed task versions."
    )
    commands = parser.add_subparsers(dest="command", required=True)

    snapshot = commands.add_parser("snapshot", help="create an immutable task version")
    _add_root(snapshot)
    snapshot.add_argument("--task-id", required=True)
    snapshot.add_argument("--branch", default="main")
    snapshot.add_argument("--parent", help="parent version hash")
    snapshot.add_argument("--actor", required=True)
    snapshot.add_argument("--message", required=True)
    snapshot.add_argument(
        "--path", "--input", "--artifact", dest="path", action="append", default=[], metavar="ROLE=PATH"
    )
    snapshot.set_defaults(handler=command_snapshot)

    listing = commands.add_parser("list", help="list immutable task versions")
    _add_root(listing)
    listing.add_argument("--task-id")
    listing.add_argument("--branch")
    listing.set_defaults(handler=command_list)

    diff = commands.add_parser("diff", help="compare two task versions")
    _add_root(diff)
    diff.add_argument("--task-id", required=True)
    diff.add_argument("--from-version", required=True)
    diff.add_argument("--to-version", required=True)
    diff.set_defaults(handler=command_diff)

    materialize = commands.add_parser("materialize", help="restore a version into a new directory")
    _add_root(materialize)
    materialize.add_argument("--task-id", required=True)
    materialize.add_argument("--version", required=True)
    materialize.add_argument("--destination", help="new destination directory")
    materialize.set_defaults(handler=command_materialize)

    select = commands.add_parser("select", help="select a version for downstream gate pinning")
    _add_root(select)
    select.add_argument("--task-id", required=True)
    select.add_argument("--version", required=True)
    select.add_argument("--actor", required=True)
    select.add_argument("--message", required=True)
    select.add_argument("--human-review-ref")
    select.add_argument("--simulation-waiver")
    select.set_defaults(handler=command_select)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
    except (VersionError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
