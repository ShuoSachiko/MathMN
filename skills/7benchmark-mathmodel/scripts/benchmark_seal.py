#!/usr/bin/env python3
"""Create and verify deterministic, allowlisted benchmark seals.

This module intentionally uses only the Python standard library. It does not
open judge-private material, run a solver, or compare a submission with answers.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import unicodedata
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Iterable, Iterator, Sequence


ALLOWLIST_SCHEMA = "mathmodel-benchmark-allowlist/v1"
MANIFEST_SCHEMA = "mathmodel-benchmark-seal/v1"
ARCHIVE_FORMAT = "zip-stored-canonical-v1"
MANIFEST_MEMBER = "BENCHMARK_SEAL_MANIFEST.json"
HASH_ALGORITHM = "sha256"
HUMAN_REVIEW_STATUSES = {"human-reviewed", "not-reviewed", "simulation-waived"}
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
FIXED_FILE_MODE = 0o100444
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class SealError(RuntimeError):
    """Raised when sealing or verification must fail closed."""


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = stream.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _copy_and_hash(input_stream: BinaryIO, output_stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while True:
        chunk = input_stream.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        output_stream.write(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _is_link_or_reparse(path: Path) -> bool:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise SealError(f"cannot inspect path {path}: {exc}") from exc
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    file_attributes = getattr(metadata, "st_file_attributes", 0)
    return stat.S_ISLNK(metadata.st_mode) or bool(file_attributes & reparse_flag)


def _absolute(path: os.PathLike[str] | str) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _is_within(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath(
            [os.path.normcase(str(path)), os.path.normcase(str(root))]
        )
    except ValueError:
        return False
    return common == os.path.normcase(str(root))


def _validated_root(source: os.PathLike[str] | str) -> Path:
    root = _absolute(source)
    if not root.exists() or not root.is_dir():
        raise SealError(f"source root is not a directory: {root}")
    if _is_link_or_reparse(root):
        raise SealError(f"source root must not be a symbolic link or reparse point: {root}")
    try:
        return root.resolve(strict=True)
    except OSError as exc:
        raise SealError(f"cannot resolve source root {root}: {exc}") from exc


def _normalize_member(raw: object, *, allow_reserved: bool = False) -> str:
    if not isinstance(raw, str) or not raw:
        raise SealError("allowlist paths must be non-empty strings")
    if raw != unicodedata.normalize("NFC", raw):
        raise SealError(f"path must use Unicode NFC normalization: {raw!r}")
    if "\\" in raw:
        raise SealError(f"path must use POSIX separators, not backslashes: {raw!r}")
    if "\x00" in raw or any(ord(character) < 32 for character in raw):
        raise SealError(f"path contains a control character: {raw!r}")
    if ":" in raw:
        raise SealError(f"path must not contain a drive or stream separator: {raw!r}")

    member = PurePosixPath(raw)
    parts = raw.split("/")
    if member.is_absolute() or raw.startswith("/"):
        raise SealError(f"absolute paths are forbidden: {raw!r}")
    if any(part in {"", ".", ".."} for part in parts):
        raise SealError(f"path traversal or empty components are forbidden: {raw!r}")
    normalized = "/".join(parts)
    if not allow_reserved and normalized.casefold() == MANIFEST_MEMBER.casefold():
        raise SealError(f"path is reserved by the seal format: {normalized!r}")
    return normalized


def _load_json(path: Path, label: str) -> tuple[object, bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise SealError(f"cannot read {label} {path}: {exc}") from exc
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SealError(f"invalid UTF-8 JSON in {label} {path}: {exc}") from exc
    return value, raw


def load_allowlist(path: os.PathLike[str] | str) -> list[str]:
    allowlist_path = _absolute(path)
    if not allowlist_path.exists() or not allowlist_path.is_file():
        raise SealError(f"allowlist is missing or not a regular file: {allowlist_path}")
    if _is_link_or_reparse(allowlist_path):
        raise SealError(
            f"allowlist must not be a symbolic link or reparse point: {allowlist_path}"
        )
    document, _ = _load_json(allowlist_path, "allowlist")
    if not isinstance(document, dict):
        raise SealError("allowlist must be a JSON object")
    if set(document) != {"schema", "files"}:
        raise SealError("allowlist must contain exactly 'schema' and 'files'")
    if document["schema"] != ALLOWLIST_SCHEMA:
        raise SealError(f"unsupported allowlist schema: {document['schema']!r}")
    raw_files = document["files"]
    if not isinstance(raw_files, list) or not raw_files:
        raise SealError("allowlist 'files' must be a non-empty JSON array")

    files = [_normalize_member(item) for item in raw_files]
    aliases: dict[str, str] = {}
    for member in files:
        alias = member.casefold()
        if alias in aliases:
            raise SealError(
                f"duplicate or case-colliding allowlist paths: "
                f"{aliases[alias]!r} and {member!r}"
            )
        aliases[alias] = member
    return sorted(files)


def _resolve_member(root: Path, member: str) -> Path:
    path = root.joinpath(*member.split("/"))
    current = root
    for component in member.split("/"):
        current = current / component
        if not current.exists() and not current.is_symlink():
            raise SealError(f"allowlisted file is missing: {member}")
        if _is_link_or_reparse(current):
            raise SealError(f"symbolic links and reparse points are forbidden: {member}")

    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise SealError(f"cannot resolve allowlisted file {member}: {exc}") from exc
    if not _is_within(resolved, root):
        raise SealError(f"allowlisted path escapes source root: {member}")
    try:
        metadata = os.stat(resolved, follow_symlinks=False)
    except OSError as exc:
        raise SealError(f"cannot inspect allowlisted file {member}: {exc}") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise SealError(f"allowlisted path is not a regular file: {member}")
    return resolved


@contextmanager
def _open_member(root: Path, member: str) -> Iterator[BinaryIO]:
    """Open one payload only after link checks, then bind checks to its descriptor."""

    path = _resolve_member(root, member)
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOINHERIT", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SealError(f"cannot safely open payload file {member}: {exc}") from exc

    stream: BinaryIO | None = None
    try:
        opened_metadata = os.fstat(descriptor)
        if not stat.S_ISREG(opened_metadata.st_mode):
            raise SealError(f"opened payload is not a regular file: {member}")

        current_path = _resolve_member(root, member)
        current_metadata = os.stat(current_path, follow_symlinks=False)
        opened_identity = (opened_metadata.st_dev, opened_metadata.st_ino)
        current_identity = (current_metadata.st_dev, current_metadata.st_ino)
        if opened_identity != current_identity:
            raise SealError(f"payload path changed while being opened: {member}")

        stream = os.fdopen(descriptor, "rb", closefd=True)
        descriptor = -1
        yield stream
    finally:
        if stream is not None:
            stream.close()
        if descriptor >= 0:
            os.close(descriptor)


def _scan_source(root: Path) -> set[str]:
    found: set[str] = set()
    try:
        walker = os.walk(root, topdown=True, followlinks=False)
        for current_raw, directory_names, file_names in walker:
            current = Path(current_raw)
            directory_names.sort()
            file_names.sort()

            for name in directory_names:
                directory = current / name
                relative = directory.relative_to(root).as_posix()
                if _is_link_or_reparse(directory):
                    raise SealError(
                        "symbolic link or reparse directory found in source: " + relative
                    )

            for name in file_names:
                path = current / name
                relative = path.relative_to(root).as_posix()
                normalized = _normalize_member(relative)
                if normalized != relative:
                    raise SealError(f"non-canonical source path: {relative!r}")
                if _is_link_or_reparse(path):
                    raise SealError(
                        "symbolic link or reparse file found in source: " + relative
                    )
                metadata = os.stat(path, follow_symlinks=False)
                if not stat.S_ISREG(metadata.st_mode):
                    raise SealError(f"non-regular file found in source: {relative}")
                if normalized in found:
                    raise SealError(f"duplicate canonical source path: {normalized}")
                found.add(normalized)
    except SealError:
        raise
    except OSError as exc:
        raise SealError(f"cannot scan source root {root}: {exc}") from exc
    return found


def _require_exact_source(root: Path, allowed: Sequence[str]) -> None:
    actual = _scan_source(root)
    expected = set(allowed)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing or unexpected:
        details: list[str] = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise SealError("source does not exactly match explicit allowlist: " + "; ".join(details))


def _allowlist_digest(files: Sequence[str]) -> str:
    return _sha256_bytes(
        _canonical_json({"schema": ALLOWLIST_SCHEMA, "files": list(files)})
    )


def _root_digest(
    entries: Sequence[dict[str, object]], human_review: dict[str, object]
) -> str:
    root_material = {
        "files": list(entries),
        "hash_algorithm": HASH_ALGORITHM,
        "human_review": human_review,
        "schema": MANIFEST_SCHEMA,
    }
    return _sha256_bytes(_canonical_json(root_material))


def _collect_entries(root: Path, files: Sequence[str]) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for member in files:
        try:
            with _open_member(root, member) as stream:
                digest, size = _hash_stream(stream)
        except SealError:
            raise
        except OSError as exc:
            raise SealError(f"cannot read payload file {member}: {exc}") from exc
        entries.append({"path": member, "sha256": digest, "size": size})
    return entries


def _normalize_human_review(status: object, record_id: object) -> dict[str, object]:
    if not isinstance(status, str) or status not in HUMAN_REVIEW_STATUSES:
        raise SealError(
            "human review status must be one of: "
            + ", ".join(sorted(HUMAN_REVIEW_STATUSES))
        )
    if record_id is not None and (not isinstance(record_id, str) or not record_id.strip()):
        raise SealError("human review record id must be a non-empty string or null")
    if isinstance(record_id, str):
        if record_id != record_id.strip():
            raise SealError("human review record id must not have surrounding whitespace")
        if any(ord(character) < 32 for character in record_id):
            raise SealError("human review record id must not contain control characters")
        if len(record_id) > 256:
            raise SealError("human review record id must not exceed 256 characters")
    if status == "human-reviewed" and record_id is None:
        raise SealError("human-reviewed status requires an external review record id")
    if status != "human-reviewed" and record_id is not None:
        raise SealError(
            f"{status} must not carry a human review record id or imply approval"
        )
    return {"record_id": record_id, "status": status}


def _build_manifest(
    files: Sequence[str],
    entries: Sequence[dict[str, object]],
    human_review: dict[str, object],
) -> dict[str, object]:
    total_bytes = sum(int(entry["size"]) for entry in entries)
    return {
        "allowlist_sha256": _allowlist_digest(files),
        "archive_format": ARCHIVE_FORMAT,
        "file_count": len(entries),
        "files": list(entries),
        "hash_algorithm": HASH_ALGORITHM,
        "human_review": human_review,
        "root_hash": _root_digest(entries, human_review),
        "schema": MANIFEST_SCHEMA,
        "total_bytes": total_bytes,
    }


def _validate_manifest(document: object) -> dict[str, object]:
    if not isinstance(document, dict):
        raise SealError("manifest must be a JSON object")
    required = {
        "allowlist_sha256",
        "archive_format",
        "file_count",
        "files",
        "hash_algorithm",
        "human_review",
        "root_hash",
        "schema",
        "total_bytes",
    }
    if set(document) != required:
        raise SealError("manifest keys do not match the canonical schema")
    if document["schema"] != MANIFEST_SCHEMA:
        raise SealError(f"unsupported manifest schema: {document['schema']!r}")
    if document["archive_format"] != ARCHIVE_FORMAT:
        raise SealError(f"unsupported archive format: {document['archive_format']!r}")
    if document["hash_algorithm"] != HASH_ALGORITHM:
        raise SealError(f"unsupported hash algorithm: {document['hash_algorithm']!r}")
    human_review_raw = document["human_review"]
    if not isinstance(human_review_raw, dict) or set(human_review_raw) != {
        "record_id",
        "status",
    }:
        raise SealError("manifest human_review needs exactly status and record_id")
    human_review = _normalize_human_review(
        human_review_raw["status"], human_review_raw["record_id"]
    )

    entries = document["files"]
    if not isinstance(entries, list) or not entries:
        raise SealError("manifest 'files' must be a non-empty array")
    canonical_entries: list[dict[str, object]] = []
    paths: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {"path", "sha256", "size"}:
            raise SealError("each manifest file entry needs path, sha256, and size")
        path = _normalize_member(entry["path"])
        digest = entry["sha256"]
        size = entry["size"]
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise SealError(f"invalid SHA-256 for manifest member {path}")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise SealError(f"invalid byte size for manifest member {path}")
        paths.append(path)
        canonical_entries.append({"path": path, "sha256": digest, "size": size})

    if paths != sorted(paths) or len({path.casefold() for path in paths}) != len(paths):
        raise SealError("manifest paths must be sorted and case-insensitively unique")
    if document["file_count"] != len(canonical_entries):
        raise SealError("manifest file_count is inconsistent")
    total_bytes = sum(int(entry["size"]) for entry in canonical_entries)
    if document["total_bytes"] != total_bytes:
        raise SealError("manifest total_bytes is inconsistent")
    if document["allowlist_sha256"] != _allowlist_digest(paths):
        raise SealError("manifest allowlist_sha256 is inconsistent")
    root_hash = document["root_hash"]
    if not isinstance(root_hash, str) or not SHA256_RE.fullmatch(root_hash):
        raise SealError("manifest root_hash is not a canonical SHA-256")
    if root_hash != _root_digest(canonical_entries, human_review):
        raise SealError("manifest root_hash is inconsistent")
    return document


def load_manifest(path: os.PathLike[str] | str) -> tuple[dict[str, object], bytes]:
    manifest_path = _absolute(path)
    if not manifest_path.exists() or not manifest_path.is_file():
        raise SealError(f"manifest is missing or not a regular file: {manifest_path}")
    if _is_link_or_reparse(manifest_path):
        raise SealError(
            f"manifest must not be a symbolic link or reparse point: {manifest_path}"
        )
    document, raw = _load_json(manifest_path, "manifest")
    manifest = _validate_manifest(document)
    canonical = _canonical_json(manifest)
    if raw != canonical:
        raise SealError("manifest bytes are not canonical JSON")
    return manifest, canonical


def _prepare_output(path: os.PathLike[str] | str, root: Path, label: str) -> Path:
    output = _absolute(path)
    parent = output.parent
    if not parent.exists() or not parent.is_dir():
        raise SealError(f"{label} parent directory must already exist: {parent}")
    if _is_link_or_reparse(parent):
        raise SealError(f"{label} parent must not be a symbolic link or reparse point: {parent}")
    try:
        resolved_parent = parent.resolve(strict=True)
    except OSError as exc:
        raise SealError(f"cannot resolve {label} parent {parent}: {exc}") from exc
    candidate = resolved_parent / output.name
    if _is_within(candidate, root):
        raise SealError(f"{label} must be outside the source root: {candidate}")
    if output.exists() and _is_link_or_reparse(output):
        raise SealError(f"{label} must not be a symbolic link or reparse point: {output}")
    return candidate


def _zip_info(name: str, size: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, FIXED_ZIP_TIME)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.external_attr = FIXED_FILE_MODE << 16
    info.file_size = size
    return info


def _write_archive(
    path: Path,
    root: Path,
    files: Sequence[str],
    human_review: dict[str, object],
) -> tuple[dict[str, object], bytes]:
    entries: list[dict[str, object]] = []
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
        archive.comment = b""
        for member in files:
            with _open_member(root, member) as input_stream:
                initial_size = os.fstat(input_stream.fileno()).st_size
                info = _zip_info(member, initial_size)
                force_zip64 = initial_size >= (1 << 31)
                with archive.open(info, "w", force_zip64=force_zip64) as output_stream:
                    digest, size = _copy_and_hash(input_stream, output_stream)
            entries.append({"path": member, "sha256": digest, "size": size})

        manifest_document = _build_manifest(files, entries, human_review)
        manifest_bytes = _canonical_json(manifest_document)
        archive.writestr(_zip_info(MANIFEST_MEMBER, len(manifest_bytes)), manifest_bytes)
    return manifest_document, manifest_bytes


def _check_archive_metadata(info: zipfile.ZipInfo) -> None:
    if info.is_dir():
        raise SealError(f"archive directory entries are forbidden: {info.filename}")
    if info.date_time != FIXED_ZIP_TIME:
        raise SealError(f"archive member has non-canonical timestamp: {info.filename}")
    if info.compress_type != zipfile.ZIP_STORED:
        raise SealError(f"archive member has non-canonical compression: {info.filename}")
    if info.create_system != 3 or info.external_attr != FIXED_FILE_MODE << 16:
        raise SealError(f"archive member has non-canonical file metadata: {info.filename}")


def _verify_archive(
    archive_path: Path,
    manifest: dict[str, object],
    manifest_bytes: bytes,
) -> None:
    if not archive_path.exists() or not archive_path.is_file():
        raise SealError(f"archive is missing or not a regular file: {archive_path}")
    if _is_link_or_reparse(archive_path):
        raise SealError(f"archive must not be a symbolic link or reparse point: {archive_path}")

    expected_entries = list(manifest["files"])
    expected_names = [str(entry["path"]) for entry in expected_entries] + [MANIFEST_MEMBER]
    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            if archive.comment:
                raise SealError("archive comment must be empty")
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if names != expected_names or len(set(names)) != len(names):
                raise SealError("archive members do not exactly match canonical manifest order")
            for info in infos:
                _normalize_member(info.filename, allow_reserved=True)
                _check_archive_metadata(info)

            embedded = archive.read(MANIFEST_MEMBER)
            if embedded != manifest_bytes:
                raise SealError("embedded and external manifests differ")

            for entry, info in zip(expected_entries, infos[:-1]):
                expected_size = int(entry["size"])
                if info.file_size != expected_size:
                    raise SealError(f"archive size mismatch for {entry['path']}")
                with archive.open(info, "r") as stream:
                    digest, size = _hash_stream(stream)
                if size != expected_size or digest != entry["sha256"]:
                    raise SealError(f"archive content mismatch for {entry['path']}")
    except SealError:
        raise
    except (OSError, KeyError, RuntimeError, zipfile.BadZipFile) as exc:
        raise SealError(f"cannot verify archive {archive_path}: {exc}") from exc


def _write_temp(parent: Path, suffix: str) -> Path:
    try:
        descriptor, name = tempfile.mkstemp(prefix=".benchmark-seal-", suffix=suffix, dir=parent)
        os.close(descriptor)
        return Path(name)
    except OSError as exc:
        raise SealError(f"cannot create temporary output in {parent}: {exc}") from exc


def _publish_new(temp_path: Path, final_path: Path) -> None:
    try:
        os.link(temp_path, final_path)
    except FileExistsError as exc:
        raise SealError(f"refusing to overwrite existing output: {final_path}") from exc
    except OSError as exc:
        raise SealError(f"cannot publish output {final_path}: {exc}") from exc


def _rollback_published(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError as exc:
        raise SealError(
            f"seal publication failed and rollback also failed for {path}: {exc}; "
            "quarantine the partial output"
        ) from exc


def seal(
    *,
    source: os.PathLike[str] | str,
    allowlist: os.PathLike[str] | str,
    archive: os.PathLike[str] | str,
    manifest: os.PathLike[str] | str,
    human_review_status: str,
    human_review_record_id: str | None = None,
) -> dict[str, object]:
    """Seal an exact staging root and return its trusted root hash summary."""

    root = _validated_root(source)
    allowlist_path = _absolute(allowlist)
    if not allowlist_path.exists() or not allowlist_path.is_file():
        raise SealError(f"allowlist is missing or not a regular file: {allowlist_path}")
    if _is_link_or_reparse(allowlist_path):
        raise SealError(
            f"allowlist must not be a symbolic link or reparse point: {allowlist_path}"
        )
    try:
        allowlist_resolved = allowlist_path.resolve(strict=True)
    except OSError as exc:
        raise SealError(f"cannot resolve allowlist {allowlist_path}: {exc}") from exc
    if _is_within(allowlist_resolved, root):
        raise SealError("allowlist control file must be outside the source root")
    files = load_allowlist(allowlist_resolved)
    _require_exact_source(root, files)
    human_review = _normalize_human_review(
        human_review_status, human_review_record_id
    )

    archive_path = _prepare_output(archive, root, "archive")
    manifest_path = _prepare_output(manifest, root, "manifest")
    if _same_path(archive_path, manifest_path):
        raise SealError("archive and manifest outputs must be different files")
    if not _same_path(archive_path.parent, manifest_path.parent):
        raise SealError("archive and manifest outputs must share one trusted parent directory")
    existing = [str(path) for path in (archive_path, manifest_path) if path.exists()]
    if existing:
        raise SealError("refusing to overwrite existing output: " + ", ".join(existing))

    archive_temp = _write_temp(archive_path.parent, ".zip")
    manifest_temp = _write_temp(manifest_path.parent, ".json")
    archive_published = False
    try:
        manifest_document, manifest_bytes = _write_archive(
            archive_temp, root, files, human_review
        )
        manifest_temp.write_bytes(manifest_bytes)
        _verify_archive(archive_temp, manifest_document, manifest_bytes)
        _require_exact_source(root, files)
        if _collect_entries(root, files) != manifest_document["files"]:
            raise SealError("source bytes changed during sealing")
        _publish_new(archive_temp, archive_path)
        archive_published = True
        try:
            _publish_new(manifest_temp, manifest_path)
        except SealError:
            _rollback_published(archive_path)
            archive_published = False
            raise
    finally:
        for temporary in (archive_temp, manifest_temp):
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    if not archive_published:
        raise SealError("seal outputs were not published")

    return {
        "archive": str(archive_path),
        "file_count": manifest_document["file_count"],
        "manifest": str(manifest_path),
        "root_hash": manifest_document["root_hash"],
        "human_review": manifest_document["human_review"],
        "total_bytes": manifest_document["total_bytes"],
    }


def verify(
    *,
    archive: os.PathLike[str] | str,
    manifest: os.PathLike[str] | str,
    expected_root_hash: str,
    source: os.PathLike[str] | str | None = None,
) -> dict[str, object]:
    """Verify an archive against an externally trusted root hash."""

    expected = expected_root_hash.strip().lower()
    if not SHA256_RE.fullmatch(expected):
        raise SealError("expected root hash must be exactly 64 hexadecimal characters")
    manifest_document, manifest_bytes = load_manifest(manifest)
    if manifest_document["root_hash"] != expected:
        raise SealError("manifest root hash does not match the externally trusted root hash")

    archive_path = _absolute(archive)
    _verify_archive(archive_path, manifest_document, manifest_bytes)

    if source is not None:
        root = _validated_root(source)
        files = [str(entry["path"]) for entry in manifest_document["files"]]
        _require_exact_source(root, files)
        current_entries = _collect_entries(root, files)
        if current_entries != manifest_document["files"]:
            raise SealError("source bytes changed after sealing")

    return {
        "archive": str(archive_path),
        "file_count": manifest_document["file_count"],
        "root_hash": expected,
        "status": "verified",
        "human_review": manifest_document["human_review"],
        "total_bytes": manifest_document["total_bytes"],
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seal and verify an explicitly allowlisted benchmark submission."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    seal_parser = subparsers.add_parser("seal", help="create a canonical archive and manifest")
    seal_parser.add_argument("--source", required=True, help="staging root containing only allowed files")
    seal_parser.add_argument("--allowlist", required=True, help="JSON file with an exact relative-file allowlist")
    seal_parser.add_argument("--archive", required=True, help="output canonical ZIP outside the source root")
    seal_parser.add_argument("--manifest", required=True, help="output canonical manifest outside the source root")
    seal_parser.add_argument(
        "--human-review-status",
        required=True,
        choices=sorted(HUMAN_REVIEW_STATUSES),
        help="explicit human review state; simulation-waived never means approved",
    )
    seal_parser.add_argument(
        "--human-review-record-id",
        help="external review record id; required only for human-reviewed",
    )

    verify_parser = subparsers.add_parser("verify", help="verify against an externally trusted root hash")
    verify_parser.add_argument("--archive", required=True, help="canonical ZIP to verify")
    verify_parser.add_argument("--manifest", required=True, help="canonical manifest to verify")
    verify_parser.add_argument(
        "--expected-root-hash",
        required=True,
        help="root hash obtained from a trusted channel outside the seal",
    )
    verify_parser.add_argument(
        "--source",
        help="optional original staging root; detects missing, added, or changed files",
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if arguments.command == "seal":
            result = seal(
                source=arguments.source,
                allowlist=arguments.allowlist,
                archive=arguments.archive,
                manifest=arguments.manifest,
                human_review_status=arguments.human_review_status,
                human_review_record_id=arguments.human_review_record_id,
            )
        else:
            result = verify(
                archive=arguments.archive,
                manifest=arguments.manifest,
                expected_root_hash=arguments.expected_root_hash,
                source=arguments.source,
            )
    except SealError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
