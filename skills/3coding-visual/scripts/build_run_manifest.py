#!/usr/bin/env python3
"""Build a deterministic SHA-256 manifest for declared run artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = 1
ROLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


class ManifestError(ValueError):
    """Raised when an input or output violates the manifest contract."""


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


def _assert_link_free(path: Path, root: Path, *, include_leaf: bool = True) -> None:
    if not _is_within(path, root):
        raise ManifestError(f"path escapes project root: {path}")
    relative = path.relative_to(root)
    current = root
    if _is_link_like(current):
        raise ManifestError(f"project root is a symbolic link or junction: {root}")
    parts = relative.parts if include_leaf else relative.parts[:-1]
    for part in parts:
        current = current / part
        if current.exists() and _is_link_like(current):
            raise ManifestError(
                f"symbolic links and junctions are not allowed: {current}"
            )


def _project_root(raw_root: str) -> Path:
    root = Path(os.path.abspath(os.path.expanduser(raw_root)))
    if not root.exists() or not root.is_dir():
        raise ManifestError(f"project root is not an existing directory: {root}")
    _assert_link_free(root, root)
    resolved = root.resolve(strict=True)
    if _norm(resolved) != _norm(root):
        raise ManifestError(f"project root resolves through a link or junction: {root}")
    return root


def _lexical_path(root: Path, raw_path: str) -> Path:
    expanded = Path(os.path.expanduser(raw_path))
    candidate = expanded if expanded.is_absolute() else root / expanded
    candidate = Path(os.path.abspath(candidate))
    if not _is_within(candidate, root):
        raise ManifestError(f"path escapes project root: {raw_path}")
    return candidate


def _existing_path(root: Path, raw_path: str) -> Path:
    path = _lexical_path(root, raw_path)
    if not path.exists():
        raise ManifestError(f"declared input does not exist: {raw_path}")
    _assert_link_free(path, root)
    resolved = path.resolve(strict=True)
    if not _is_within(resolved, root) or _norm(resolved) != _norm(path):
        raise ManifestError(f"input resolves outside the project root: {raw_path}")
    if not (path.is_file() or path.is_dir()):
        raise ManifestError(f"input must be a regular file or directory: {raw_path}")
    return path


def _output_path(root: Path, raw_path: str, *, create_parent: bool) -> Path:
    output = _lexical_path(root, raw_path)
    ancestor = output.parent
    while not ancestor.exists() and ancestor != root:
        ancestor = ancestor.parent
    _assert_link_free(ancestor, root)
    if ancestor.exists() and not ancestor.is_dir():
        raise ManifestError(f"output ancestor is not a directory: {ancestor}")
    if create_parent:
        output.parent.mkdir(parents=True, exist_ok=True)
        _assert_link_free(output.parent, root)
    if output.exists() and (not output.is_file() or _is_link_like(output)):
        raise ManifestError(f"output is not a safe regular file: {output}")
    return output


def _relative(path: Path, root: Path) -> str:
    value = path.relative_to(root).as_posix()
    return value or "."


def _parse_inputs(values: Iterable[str]) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        if "=" not in value:
            raise ManifestError(f"input must use ROLE=PATH syntax: {value!r}")
        role, raw_path = value.split("=", 1)
        role = role.strip()
        raw_path = raw_path.strip()
        if not ROLE_RE.fullmatch(role):
            raise ManifestError(
                "role must start with an ASCII letter and contain only "
                f"letters, digits, '.', '_' or '-': {role!r}"
            )
        if not raw_path:
            raise ManifestError(f"input path is empty for role {role!r}")
        item = (role, raw_path)
        if item in seen:
            raise ManifestError(f"duplicate input declaration: {value!r}")
        seen.add(item)
        declarations.append(item)
    if not declarations:
        raise ManifestError("at least one --input ROLE=PATH declaration is required")
    return declarations


def _file_record(role: str, path: Path, root: Path) -> dict[str, Any]:
    return {
        "role": role,
        "path": _relative(path, root),
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _directory_files(
    role: str, path: Path, root: Path, output: Path
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for current_text, dir_names, file_names in os.walk(path, followlinks=False):
        current = Path(current_text)
        _assert_link_free(current, root)
        for name in sorted(dir_names):
            child = current / name
            if _is_link_like(child):
                raise ManifestError(
                    f"symbolic links and junctions are not allowed in inputs: {child}"
                )
            if not child.is_dir():
                raise ManifestError(f"non-directory entry encountered during walk: {child}")
        for name in sorted(file_names):
            child = current / name
            if _norm(child) == _norm(output):
                continue
            if _is_link_like(child):
                raise ManifestError(
                    f"symbolic links and junctions are not allowed in inputs: {child}"
                )
            if not child.is_file():
                raise ManifestError(f"input contains a non-regular file: {child}")
            records.append(_file_record(role, child, root))
    return records


def _build_manifest(
    root: Path,
    output: Path,
    declarations: Iterable[tuple[str, str]],
) -> dict[str, Any]:
    sources: list[dict[str, str]] = []
    files: list[dict[str, Any]] = []
    seen_sources: set[tuple[str, str]] = set()
    seen_files: set[tuple[str, str]] = set()

    for role, raw_path in declarations:
        path = _existing_path(root, raw_path)
        relative = _relative(path, root)
        source_key = (role, relative)
        if source_key in seen_sources:
            raise ManifestError(
                f"duplicate normalized input declaration: {role}={relative}"
            )
        seen_sources.add(source_key)
        kind = "file" if path.is_file() else "directory"
        sources.append({"role": role, "path": relative, "type": kind})

        if path.is_file():
            if _norm(path) == _norm(output):
                raise ManifestError("the output manifest cannot also be an input")
            candidates = [_file_record(role, path, root)]
        else:
            candidates = _directory_files(role, path, root, output)

        for record in candidates:
            key = (record["role"], record["path"])
            if key in seen_files:
                raise ManifestError(
                    "overlapping declarations produced a duplicate file entry: "
                    f"{record['role']}={record['path']}"
                )
            seen_files.add(key)
            files.append(record)

    sources.sort(key=lambda item: (item["role"], item["path"]))
    files.sort(key=lambda item: (item["role"], item["path"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_type": "run",
        "hash_algorithm": "sha256",
        "sources": sources,
        "files": files,
    }


def _clean_metadata(value: str, label: str, *, maximum: int = 4096) -> str:
    cleaned = value.strip()
    if not cleaned or len(cleaned) > maximum:
        raise ManifestError(f"{label} must contain 1 to {maximum} characters")
    if any(character in "\x00\r\n" for character in cleaned):
        raise ManifestError(f"{label} must be a single line without NUL bytes")
    return cleaned


def _attach_run_metadata(
    manifest: dict[str, Any], commands: Iterable[str], runtime: str | None
) -> dict[str, Any]:
    command_list = [
        _clean_metadata(command, "command") for command in commands
    ]
    runtime_value = (
        _clean_metadata(runtime, "runtime") if runtime is not None else None
    )
    manifest["commands"] = command_list
    manifest["runtime"] = runtime_value
    root_material = {
        "commands": command_list,
        "files": manifest["files"],
        "runtime": runtime_value,
        "sources": manifest["sources"],
    }
    manifest["root_hash"] = hashlib.sha256(
        _canonical_json_bytes(root_material)
    ).hexdigest()
    return manifest


def _write_manifest(output: Path, data: bytes, overwrite: bool) -> None:
    if output.exists() and not overwrite:
        raise ManifestError(f"refusing to overwrite existing manifest: {output}")
    if not overwrite:
        with output.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        return

    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{output.name}.", dir=output.parent, delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Hash explicitly declared run files or directories into a sorted, "
            "deterministic JSON manifest."
        )
    )
    parser.add_argument(
        "--project-root", "--root", dest="project_root", default=".", help="project root"
    )
    parser.add_argument(
        "--artifact",
        "--input",
        dest="artifact",
        action="append",
        default=[],
        metavar="ROLE=PATH",
        help="declared run input; repeat for each file or directory",
    )
    parser.add_argument(
        "--command",
        action="append",
        default=[],
        help="actual reproduction command; repeat to preserve execution order",
    )
    parser.add_argument("--runtime", help="actual runtime and version")
    parser.add_argument(
        "--output",
        default="RUN_MANIFEST.json",
        help="output JSON path, relative to the project root",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace an existing manifest (disabled by default)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        root = _project_root(args.project_root)
        output = _output_path(root, args.output, create_parent=False)
        declarations = _parse_inputs(args.artifact)
        manifest = _attach_run_metadata(
            _build_manifest(root, output, declarations), args.command, args.runtime
        )
        output = _output_path(root, args.output, create_parent=True)
        _write_manifest(output, _render_json(manifest), args.overwrite)
    except (ManifestError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"wrote run manifest: {output}")
    print(f"root hash: {manifest['root_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
