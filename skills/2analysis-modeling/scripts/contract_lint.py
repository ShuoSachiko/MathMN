#!/usr/bin/env python3
"""Lint the problem contract before freezing it for downstream stages.

This gate moves part of 6verity's semantic acceptance earlier: 2analysis runs it
before marking PROBLEM_CONTRACT.json as FROZEN so contract slips surface in the
modeling stage instead of at the end of the pipeline. The authoritative schema
is `python <6verity>/scripts/integrity_check.py --print-schema`; this linter
checks the same top-level shape plus per-requirement semantic fields and only
runs on the contract, so it is cheap enough for every freeze cycle.

Exit codes: 0 = no errors, 1 = errors found, 2 = input missing/unreadable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PLACEHOLDER_RE = re.compile(
    r"PLACEHOLDER|TODO|TBD|XXX|待补充|待续写|待定|示例数据|示例数值|待替换"
    r"|EXAMPLE-VALUE|【占位|占位"
)

QUANTITY_KINDS = {"fixed", "decision", "state", "control", "derived"}

# Tolerant field aliases so existing contracts authored by different agents
# still lint instead of crashing. The first match wins; missing all = error.
_LOCATION_KEYS = ("source_ref", "location", "原文定位", "source")
_ACTION_KEYS = ("action", "动作", "verb")
_OUTPUT_KEYS = ("outputs", "必交输出", "deliverables")
_ACCEPTANCE_KEYS = ("acceptance", "验收断言", "acceptance_criteria")
_DOWNSTREAM_KEYS = ("downstream", "下游映射")


def _walk_strings(node: object):
    """Yield every leaf string in a JSON-like tree."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _walk_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_strings(item)


def _first(mapping: dict, keys: tuple[str, ...]):
    for key in keys:
        if mapping.get(key) not in (None, "", []):
            return mapping[key]
    return None


def check(root: Path) -> tuple[list[str], list[str]]:
    """Lint reports/PROBLEM_CONTRACT.json under root.

    Returns (errors, warnings); errors block FROZEN, warnings do not.
    """
    errors: list[str] = []
    warnings: list[str] = []
    contract_path = root / "reports" / "PROBLEM_CONTRACT.json"
    md_path = root / "reports" / "PROBLEM_CONTRACT.md"

    if not contract_path.is_file():
        return [f"missing {contract_path.as_posix()}"], []
    try:
        data = json.loads(contract_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"unreadable contract: {exc}"], []

    # ---- top-level shape (aligned with integrity_check schema) ----
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    if data.get("status") != "FROZEN":
        errors.append("status must be FROZEN before any downstream stage")
    if not (data.get("problem_manifest_sha256") or data.get("problem_root_hash")):
        errors.append("missing problem_manifest_sha256 / problem_root_hash")

    requirements = data.get("requirements")
    if not isinstance(requirements, list) or not requirements:
        errors.append("requirements must be a non-empty list")
        return errors, warnings

    # ---- per-requirement semantic fields ----
    seen_ids: set[str] = set()
    for index, req in enumerate(requirements):
        owner = f"requirements[{index}]"
        if not isinstance(req, dict):
            errors.append(f"{owner}: must be an object")
            continue
        req_id = req.get("id") or req.get("ReqID")
        if not req_id or not re.match(r"^REQ-", str(req_id)):
            errors.append(f"{owner}: id missing or not REQ-* form")
        elif req_id in seen_ids:
            errors.append(f"{owner}: duplicate id {req_id}")
        else:
            seen_ids.add(str(req_id))
        if _first(req, _LOCATION_KEYS) is None:
            warnings.append(f"{owner}: no source location (原文定位)")
        if _first(req, _ACTION_KEYS) is None:
            errors.append(f"{owner}: no action (做什么)")
        if _first(req, _OUTPUT_KEYS) is None:
            errors.append(f"{owner}: no required outputs (必交输出)")
        if _first(req, _ACCEPTANCE_KEYS) is None:
            errors.append(f"{owner}: no acceptance assertion (验收断言)")
        if _first(req, _DOWNSTREAM_KEYS) is None:
            warnings.append(f"{owner}: no downstream mapping (下游映射)")

    # ---- quantities ----
    quantities = data.get("quantities")
    if not isinstance(quantities, list) or not quantities:
        errors.append("quantities must be a non-empty list")
    else:
        for index, quantity in enumerate(quantities):
            owner = f"quantities[{index}]"
            if not isinstance(quantity, dict):
                errors.append(f"{owner}: must be an object")
                continue
            if not quantity.get("id"):
                errors.append(f"{owner}: id missing")
            kind = quantity.get("kind")
            if kind not in QUANTITY_KINDS:
                errors.append(f"{owner}: kind must be one of {sorted(QUANTITY_KINDS)}")
            if not (quantity.get("unit") or quantity.get("definition") or quantity.get("domain")):
                errors.append(f"{owner}: unit or definition/domain missing")
            if not (quantity.get("source_req") or quantity.get("source_reqid") or quantity.get("req")):
                warnings.append(f"{owner}: no source ReqID")

    # ---- placeholder tokens anywhere in the contract ----
    for text in _walk_strings(data):
        if PLACEHOLDER_RE.search(text):
            errors.append(f"placeholder token found in contract: {text[:60]}")

    # ---- md/json parity ----
    if not md_path.is_file():
        errors.append("missing PROBLEM_CONTRACT.md (must stay in sync with the JSON)")
    else:
        md_text = md_path.read_text(encoding="utf-8")
        for req_id in seen_ids:
            if req_id not in md_text:
                errors.append(f"{req_id} not found in PROBLEM_CONTRACT.md")

    return errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--json", action="store_true", help="print machine-readable result")
    args = parser.parse_args()
    errors, warnings = check(args.root)
    if args.json:
        print(json.dumps({"errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2))
    else:
        for warning in warnings:
            print(f"WARN: {warning}")
        for error in errors:
            print(f"FAIL: {error}")
        if errors:
            print(f"contract lint: {len(errors)} error(s), {len(warnings)} warning(s)")
        else:
            print(f"contract lint: PASS ({len(warnings)} warning(s))")
    if errors:
        return 1
    if not (args.root / "reports" / "PROBLEM_CONTRACT.json").is_file():
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
