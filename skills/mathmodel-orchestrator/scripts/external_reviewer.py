#!/usr/bin/env python3
"""Call an optional OpenAI-compatible model with a sealed context packet."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def endpoint(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/chat/completions"):
        return value
    if value.endswith("/v1"):
        return value + "/chat/completions"
    return value + "/v1/chat/completions"


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_packet_documents(
    root: Path, packet: dict[str, Any], max_bytes: int
) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    total = 0
    for item in packet.get("inputs", []):
        path = (root / str(item.get("path", ""))).resolve(strict=True)
        path.relative_to(root)
        if sha256(path) != item.get("sha256"):
            raise ValueError(f"packet input changed: {item.get('path')}")
        data = path.read_bytes()
        total += len(data)
        if total > max_bytes:
            raise ValueError(f"packet text exceeds --max-input-bytes={max_bytes}")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"external reviewer accepts UTF-8 text inputs only: {item.get('path')}"
            ) from exc
        documents.append(
            {
                "name": str(item.get("name")),
                "path": str(item.get("path")),
                "sha256": str(item.get("sha256")),
                "content": content,
            }
        )
    return documents


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--max-input-bytes", type=int, default=500000)
    parser.add_argument("--check-config", action="store_true")
    parser.add_argument("--allow-live-external-review", action="store_true")
    args = parser.parse_args()

    parsed = urllib.parse.urlparse(endpoint(args.base_url))
    if parsed.scheme != "https" or not parsed.netloc:
        print("ERROR: reviewer endpoint must be an absolute HTTPS URL", file=sys.stderr)
        return 2
    api_key = os.environ.get("MATHMODEL_REVIEWER_API_KEY", "")
    if args.check_config:
        print(
            json.dumps(
                {
                    "endpoint": endpoint(args.base_url),
                    "model": args.model,
                    "api_key_present": bool(api_key),
                },
                ensure_ascii=False,
            )
        )
        return 0
    if not api_key:
        print(
            "ERROR: set MATHMODEL_REVIEWER_API_KEY in the process environment",
            file=sys.stderr,
        )
        return 2
    if args.packet is None or args.output is None:
        print("ERROR: --packet and --output are required for a call", file=sys.stderr)
        return 2

    root = args.project_root.resolve(strict=True)
    manifest_path = root / "reports" / "PROBLEM_MANIFEST.json"
    try:
        manifest = load_json(manifest_path)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: cannot read problem manifest: {exc}", file=sys.stderr)
        return 2
    if (
        manifest.get("mode") == "live-competition"
        and not args.allow_live_external_review
    ):
        print(
            "ERROR: live-competition external review is fail-closed; verify current rules and obtain human approval first",
            file=sys.stderr,
        )
        return 2
    packet_path = args.packet.resolve(strict=True)
    output_path = args.output.resolve()
    for path in (packet_path, output_path):
        try:
            path.relative_to(root)
        except ValueError:
            print(f"ERROR: path escapes project root: {path}", file=sys.stderr)
            return 2
    packet = load_json(packet_path)
    if packet.get("kind") != "CONTEXT_PACKET" or not isinstance(
        packet.get("inputs"), list
    ):
        print("ERROR: invalid context packet", file=sys.stderr)
        return 2
    if packet.get("model") != args.model:
        print("ERROR: --model must match the sealed context packet", file=sys.stderr)
        return 2
    if output_path.exists():
        print("ERROR: refusing to overwrite reviewer output", file=sys.stderr)
        return 2
    try:
        documents = load_packet_documents(root, packet, args.max_input_bytes)
    except (ValueError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    prompt = (
        "Act as an independent mathematical-modeling reviewer. Read only the sealed packet "
        "and its explicitly allowlisted UTF-8 documents below. Return JSON only with keys "
        "status, summary, req_ids, claims, artifacts, open_questions, recommended_checks. "
        "Each claim must have claim_id, text, evidence, confidence from 0 to 1, and limitations. "
        "Use status PROPOSED, NEEDS_REVIEW, REJECTED, or UNVERIFIED; never PASS and never claim "
        "human approval. Prefer deterministic checks over agreement.\n\n"
        + json.dumps({"packet": packet, "documents": documents}, ensure_ascii=False)
    )
    body = json.dumps(
        {
            "model": args.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "temperature": 0,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        endpoint(args.base_url),
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"ERROR: external reviewer call failed: {exc}", file=sys.stderr)
        return 1
    choices = payload.get("choices") or []
    if not choices:
        print("ERROR: reviewer response has no choices", file=sys.stderr)
        return 1
    content = choices[0].get("message", {}).get("content", "")
    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        print("ERROR: reviewer did not return valid JSON", file=sys.stderr)
        return 1
    if result.get("status") not in {
        "PROPOSED",
        "NEEDS_REVIEW",
        "REJECTED",
        "UNVERIFIED",
    }:
        print("ERROR: reviewer returned a forbidden status", file=sys.stderr)
        return 1
    result.update(
        {
            "schema_version": 1,
            "task_id": packet["task_id"],
            "packet_sha256": sha256(packet_path),
            "role": packet["role"],
            "provider": packet["provider"],
            "model": packet["model"],
            "req_ids": packet["req_ids"],
        }
    )
    for key in (
        "req_ids",
        "claims",
        "artifacts",
        "open_questions",
        "recommended_checks",
    ):
        result.setdefault(key, [])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output_path.relative_to(root).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
