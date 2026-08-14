#!/usr/bin/env python3
"""Plan and audit conflict-free multi-agent paper drafting."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")


class WritingTeamError(ValueError):
    pass


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def project_path(root: Path, raw: str, *, existing: bool = True) -> Path:
    path = Path(raw)
    path = path if path.is_absolute() else root / path
    path = path.resolve(strict=existing)
    path.relative_to(root)
    return path


def section_id(path: Path) -> str:
    value = SAFE_ID.sub("-", path.stem).strip("-.")
    if not value:
        raise WritingTeamError(f"cannot derive a section ID from {path}")
    return value


def command_init(args: argparse.Namespace) -> int:
    root = args.project_root.resolve(strict=True)
    claims = project_path(root, args.claim_ledger)
    traceability = project_path(root, args.traceability)
    sections = []
    seen: set[str] = set()
    for raw in args.section:
        path = project_path(root, raw, existing=False)
        item_id = section_id(path)
        if item_id in seen:
            raise WritingTeamError(f"duplicate section ID: {item_id}")
        seen.add(item_id)
        sections.append(
            {
                "section_id": item_id,
                "path": path.relative_to(root).as_posix(),
                "existing_sha256": digest(path) if path.is_file() else None,
                "tasks": {
                    "draft": f"writing-{item_id}-draft",
                    "equation_review": f"writing-{item_id}-equation-review",
                    "evidence_review": f"writing-{item_id}-evidence-review",
                },
                "merge_status": "PENDING",
            }
        )
    document = {
        "schema_version": 1,
        "kind": "WRITING_TEAM_PLAN",
        "policy": {
            "single_merger": True,
            "parallel_writes_to_final_sections": False,
            "reviewers_are_read_only": True,
            "ai_can_approve_paper": False,
            "require_heterogeneous_review": args.require_heterogeneous_review,
        },
        "inputs": {
            "claim_ledger": args.claim_ledger,
            "claim_ledger_sha256": digest(claims),
            "traceability": args.traceability,
            "traceability_sha256": digest(traceability),
        },
        "sections": sections,
        "required_roles": [
            "section-drafter",
            "equation-reviewer",
            "evidence-reviewer",
            "paper-merger",
        ],
    }
    output = project_path(root, str(args.output), existing=False)
    if output.exists():
        raise WritingTeamError(f"refusing to overwrite plan: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(output.relative_to(root).as_posix())
    return 0


def command_audit(args: argparse.Namespace) -> int:
    root = args.project_root.resolve(strict=True)
    plan_path = project_path(root, str(args.plan))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    failures: list[str] = []
    inputs = plan.get("inputs", {})
    for name in ("claim_ledger", "traceability"):
        path = project_path(root, inputs.get(name, ""))
        if digest(path) != inputs.get(f"{name}_sha256"):
            failures.append(f"upstream writing input changed: {name}")
    result_dir = project_path(root, str(args.results))
    results: dict[str, dict[str, Any]] = {}
    for path in result_dir.glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if value.get("kind") == "AGENT_RESULT":
            results[str(value.get("task_id"))] = value
    heterogeneous_sections = 0
    for section in plan.get("sections", []):
        tasks = section.get("tasks", {})
        role_expectations = {
            "draft": "section-drafter",
            "equation_review": "equation-reviewer",
            "evidence_review": "evidence-reviewer",
        }
        selected: dict[str, dict[str, Any]] = {}
        for lane, expected_role in role_expectations.items():
            result = results.get(tasks.get(lane))
            if result is None:
                failures.append(f"{section.get('section_id')} missing {lane} result")
                continue
            selected[lane] = result
            if result.get("role") != expected_role:
                failures.append(f"{tasks.get(lane)} role is not {expected_role}")
            if result.get("status") not in {
                "PROPOSED",
                "NEEDS_REVIEW",
                "REJECTED",
                "UNVERIFIED",
            }:
                failures.append(f"{tasks.get(lane)} has a forbidden status")
        if len(selected) == 3:
            role_identities = {
                (item.get("provider"), item.get("model"), item.get("role"))
                for item in selected.values()
            }
            if len(role_identities) < 3:
                failures.append(
                    f"{section.get('section_id')} review identities are not separated"
                )
            model_identities = {
                (item.get("provider"), item.get("model")) for item in selected.values()
            }
            heterogeneous = len(model_identities) >= 2
            heterogeneous_sections += int(heterogeneous)
            if (
                plan.get("policy", {}).get("require_heterogeneous_review")
                and not heterogeneous
            ):
                failures.append(
                    f"{section.get('section_id')} requires a reviewer from a different provider/model"
                )
            if (
                selected["equation_review"].get("status") == "REJECTED"
                or selected["evidence_review"].get("status") == "REJECTED"
            ):
                failures.append(f"{section.get('section_id')} has a rejected review")
    report = {
        "schema_version": 1,
        "kind": "WRITING_TEAM_AUDIT",
        "plan_sha256": digest(plan_path),
        "section_count": len(plan.get("sections", [])),
        "heterogeneous_section_count": heterogeneous_sections,
        "failures": failures,
        "status": "FAIL" if failures else "READY_FOR_HUMAN_MERGE",
        "note": "READY_FOR_HUMAN_MERGE is not a paper gate approval.",
    }
    output = project_path(root, str(args.output), existing=False)
    if output.exists():
        raise WritingTeamError(f"refusing to overwrite audit: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"status": report["status"], "failures": len(failures)}))
    return 1 if failures else 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("--project-root", type=Path, default=Path.cwd())
    init.add_argument("--claim-ledger", default="results/claim_ledger.json")
    init.add_argument("--traceability", default="reports/PAPER_TRACEABILITY.json")
    init.add_argument("--section", action="append", required=True)
    init.add_argument("--require-heterogeneous-review", action="store_true")
    init.add_argument(
        "--output", type=Path, default=Path("reports/WRITING_TEAM_PLAN.json")
    )
    init.set_defaults(handler=command_init)
    audit = commands.add_parser("audit")
    audit.add_argument("--project-root", type=Path, default=Path.cwd())
    audit.add_argument(
        "--plan", type=Path, default=Path("reports/WRITING_TEAM_PLAN.json")
    )
    audit.add_argument("--results", type=Path, default=Path("reports/agents/results"))
    audit.add_argument(
        "--output", type=Path, default=Path("reports/WRITING_TEAM_AUDIT.json")
    )
    audit.set_defaults(handler=command_audit)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.handler(args)
    except (
        WritingTeamError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        ValueError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
