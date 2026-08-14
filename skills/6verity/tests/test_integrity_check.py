from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "integrity_check.py"
SPEC = importlib.util.spec_from_file_location("mathmodel_integrity_check", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
integrity_check = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = integrity_check
SPEC.loader.exec_module(integrity_check)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def file_record(root: Path, relative: str, role: str = "artifact") -> dict:
    path = root / relative
    return {
        "role": role,
        "path": relative,
        "size": path.stat().st_size,
        "sha256": sha256(path),
    }


def validation(
    validation_id: str,
    claim_id: str,
    checks: list[str],
    evidence: str = "evidence/validation.txt",
) -> dict:
    return {
        "id": validation_id,
        "claim_ids": [claim_id],
        "status": "PASSED",
        "checks": [
            {
                "type": check,
                "status": "PASSED",
                "configuration": {"method": "synthetic-fixture", "parameterized": True},
                "evidence": [evidence],
            }
            for check in checks
        ],
    }


def build_fixture(root: Path, *, review_mode: str = "human-supervised") -> None:
    (root / "input").mkdir(parents=True)
    (root / "evidence").mkdir(parents=True)
    (root / "reports").mkdir(parents=True)
    (root / "input" / "statement.txt").write_text("synthetic statement\n", encoding="utf-8")
    for name in (
        "result-opt.json",
        "result-pred.json",
        "result-sim.json",
        "result-inverse.json",
    ):
        (root / "evidence" / name).write_text('{"synthetic": true}\n', encoding="utf-8")
    (root / "evidence" / "validation.txt").write_text(
        "independent synthetic validation evidence\n", encoding="utf-8"
    )
    (root / "evidence" / "human-review.txt").write_text(
        "human review attachment placeholder for the synthetic fixture\n", encoding="utf-8"
    )

    inputs = [file_record(root, "input/statement.txt", "problem-statement")]
    problem = {
        "schema_version": 1,
        "manifest_type": "problem",
        "task_mode": "human-supervised",
        "inputs": inputs,
        "root_hash": canonical_hash(inputs),
    }
    write_json(root / "PROBLEM_MANIFEST.json", problem)

    contract = {
        "schema_version": 1,
        "status": "FROZEN",
        "problem_manifest_sha256": sha256(root / "PROBLEM_MANIFEST.json"),
        "problem_root_hash": problem["root_hash"],
        "requirements": [
            {"id": "REQ-OPT", "required": True},
            {"id": "REQ-PRED", "required": True},
            {"id": "REQ-SIM", "required": True},
            {"id": "REQ-INVERSE", "required": True},
            {"id": "REQ-OPTIONAL", "required": False},
        ],
        "quantities": [
            {"id": "CONST-X", "kind": "fixed", "value": 3, "unit": "u"},
            {
                "id": "DECISION-X",
                "kind": "decision",
                "domain": {"min": 0, "max": 1},
                "unit": "u",
            },
        ],
    }
    write_json(root / "PROBLEM_CONTRACT.json", contract)

    claims = {
        "schema_version": 1,
        "problem_contract_sha256": sha256(root / "PROBLEM_CONTRACT.json"),
        "claims": [
            {
                "id": "CLM-OPT",
                "contract_refs": ["REQ-OPT"],
                "claim_type": "optimization",
                "status": "supported",
                "evidence": ["evidence/result-opt.json"],
                "validation_ids": ["VAL-OPT"],
                "quantity_bindings": [
                    {"id": "CONST-X", "kind": "fixed", "value": 3, "unit": "u"},
                    {
                        "id": "DECISION-X",
                        "kind": "decision",
                        "domain": {"min": 0, "max": 1},
                        "unit": "u",
                        "value": 0.4,
                    },
                ],
            },
            {
                "id": "CLM-PRED",
                "contract_refs": ["REQ-PRED"],
                "claim_type": "prediction",
                "status": "supported",
                "evidence": ["evidence/result-pred.json"],
                "validation_ids": ["VAL-PRED"],
            },
            {
                "id": "CLM-SIM",
                "contract_refs": ["REQ-SIM"],
                "claim_type": "simulation",
                "status": "supported",
                "evidence": ["evidence/result-sim.json"],
                "validation_ids": ["VAL-SIM"],
            },
            {
                "id": "CLM-INVERSE",
                "contract_refs": ["REQ-INVERSE"],
                "claim_type": "parameter_estimation",
                "status": "supported",
                "evidence": ["evidence/result-inverse.json"],
                "validation_ids": ["VAL-INVERSE"],
            },
        ],
    }
    write_json(root / "claim_ledger.json", claims)

    validations = {
        "schema_version": 1,
        "problem_contract_sha256": sha256(root / "PROBLEM_CONTRACT.json"),
        "claim_ledger_sha256": sha256(root / "claim_ledger.json"),
        "validations": [
            validation(
                "VAL-OPT",
                "CLM-OPT",
                ["constraint_check", "domain_coverage", "exact_solver_gap"],
            ),
            validation(
                "VAL-PRED",
                "CLM-PRED",
                ["backtest", "error_metrics", "leakage_check"],
            ),
            validation(
                "VAL-SIM",
                "CLM-SIM",
                ["invariant_check", "resolution_convergence", "cross_implementation"],
            ),
            validation(
                "VAL-INVERSE",
                "CLM-INVERSE",
                [
                    "synthetic_recovery",
                    "identifiability",
                    "baseline_comparison",
                    "nuisance_parameter_sensitivity",
                ],
            ),
        ],
    }
    write_json(root / "validation_manifest.json", validations)

    run_files = [
        file_record(root, "input/statement.txt", "source"),
        file_record(root, "evidence/result-opt.json"),
        file_record(root, "evidence/result-pred.json"),
        file_record(root, "evidence/result-sim.json"),
        file_record(root, "evidence/result-inverse.json"),
        file_record(root, "evidence/validation.txt", "validation"),
        file_record(root, "evidence/human-review.txt", "human-review-evidence"),
    ]
    run_files.sort(key=lambda item: (item["role"], item["path"]))
    run_manifest = {
        "schema_version": 1,
        "manifest_type": "run",
        "sources": [{"role": "source", "path": "input/statement.txt", "type": "file"}],
        "files": run_files,
        "root_hash": canonical_hash(run_files),
    }
    write_json(root / "run_manifest.json", run_manifest)

    checkpoints = []
    for checkpoint_id in integrity_check.HUMAN_CHECKPOINTS:
        checkpoints.append(
            {
                "id": checkpoint_id,
                "status": "APPROVED",
                "reviewer": {"name": "Synthetic Human", "type": "human"},
                "reviewed_at": "2026-01-01T00:00:00Z",
                "scope": f"Review synthetic {checkpoint_id} artifacts",
                "evidence": ["evidence/human-review.txt"],
                "comments": "Reviewed for the synthetic integrity fixture.",
            }
        )
    human_review = {
        "schema_version": 1,
        "mode": review_mode,
        "authorship": {"type": "human", "agent_generated": False},
        "problem_manifest_sha256": sha256(root / "PROBLEM_MANIFEST.json"),
        "problem_contract_sha256": sha256(root / "PROBLEM_CONTRACT.json"),
        "checkpoints": checkpoints,
    }
    write_json(root / "reports" / "HUMAN_REVIEW.json", human_review)
    write_json(
        root / "reports" / "CURRENT_VERSIONS.json",
        {
            "schema_version": 1,
            "problem_root_hash": problem["root_hash"],
            "selections": {},
            "selection_hash": canonical_hash({}),
        },
    )
    (root / "reports" / "VERSION_DECISIONS.jsonl").write_bytes(b"")
    refresh_chain(root)


def refresh_chain(root: Path, *, refresh_stage: bool = True) -> None:
    problem = read_json(root / "PROBLEM_MANIFEST.json")
    contract = read_json(root / "PROBLEM_CONTRACT.json")
    contract["problem_manifest_sha256"] = sha256(root / "PROBLEM_MANIFEST.json")
    contract["problem_root_hash"] = problem["root_hash"]
    write_json(root / "PROBLEM_CONTRACT.json", contract)

    claims = read_json(root / "claim_ledger.json")
    claims["problem_contract_sha256"] = sha256(root / "PROBLEM_CONTRACT.json")
    write_json(root / "claim_ledger.json", claims)

    validations = read_json(root / "validation_manifest.json")
    validations["problem_contract_sha256"] = sha256(root / "PROBLEM_CONTRACT.json")
    validations["claim_ledger_sha256"] = sha256(root / "claim_ledger.json")
    write_json(root / "validation_manifest.json", validations)

    run = read_json(root / "run_manifest.json")
    run["root_hash"] = canonical_hash(run["files"])
    write_json(root / "run_manifest.json", run)

    human = read_json(root / "reports" / "HUMAN_REVIEW.json")
    human["problem_manifest_sha256"] = sha256(root / "PROBLEM_MANIFEST.json")
    human["problem_contract_sha256"] = sha256(root / "PROBLEM_CONTRACT.json")
    write_json(root / "reports" / "HUMAN_REVIEW.json", human)

    if refresh_stage:
        upstream = {
            "problem_manifest": sha256(root / "PROBLEM_MANIFEST.json"),
            "problem_contract": sha256(root / "PROBLEM_CONTRACT.json"),
            "claim_ledger": sha256(root / "claim_ledger.json"),
            "validation_manifest": sha256(root / "validation_manifest.json"),
            "run_manifest": sha256(root / "run_manifest.json"),
            "human_review": sha256(root / "reports" / "HUMAN_REVIEW.json"),
            "current_versions": sha256(root / "reports" / "CURRENT_VERSIONS.json"),
        }
        gates = {
            "schema_version": 1,
            "problem_manifest_sha256": sha256(root / "PROBLEM_MANIFEST.json"),
            "problem_root_hash": problem["root_hash"],
            "stages": {
                "final-integrity": {
                    "status": "PASSED",
                    "required": True,
                    "upstream_hashes": upstream,
                    "required_artifacts": ["evidence/result-opt.json"],
                    "evidence": ["evidence/validation.txt"],
                }
            },
        }
        write_json(root / "STAGE_GATES.json", gates)


def diagnostic_codes(report) -> set[str]:
    return {item.code for item in report.diagnostics}


class IntegrityCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        build_fixture(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def check(self):
        return integrity_check.run_check(self.root)

    def test_valid_cross_type_fixture_and_optional_requirement_pass(self) -> None:
        report = self.check()
        self.assertEqual(report.status, "PASS", report.to_dict())
        self.assertFalse(report.errors)

    def test_current_version_index_tampering_is_rejected(self) -> None:
        current = read_json(self.root / "reports" / "CURRENT_VERSIONS.json")
        current["selection_hash"] = "0" * 64
        write_json(self.root / "reports" / "CURRENT_VERSIONS.json", current)
        report = self.check()
        self.assertIn("CURRENT_VERSIONS_HASH_INVALID", diagnostic_codes(report))

    def test_custom_claim_type_accepts_alternative_validation(self) -> None:
        (self.root / "evidence" / "result-custom.json").write_text(
            '{"synthetic": true}\n', encoding="utf-8"
        )
        contract = read_json(self.root / "PROBLEM_CONTRACT.json")
        contract["requirements"].append({"id": "REQ-CUSTOM", "required": True})
        write_json(self.root / "PROBLEM_CONTRACT.json", contract)
        claims = read_json(self.root / "claim_ledger.json")
        claims["claims"].append(
            {
                "id": "CLM-CUSTOM",
                "contract_refs": ["REQ-CUSTOM"],
                "claim_type": "ordinal-decision",
                "status": "supported",
                "evidence": ["evidence/result-custom.json"],
                "validation_ids": ["VAL-CUSTOM"],
            }
        )
        write_json(self.root / "claim_ledger.json", claims)
        validations = read_json(self.root / "validation_manifest.json")
        validations["claim_type_profiles"] = {
            "ordinal-decision": {
                "required_checks": [
                    {"any_of": ["audit-a", "audit-b"]},
                    "sensitivity",
                ]
            }
        }
        validations["validations"].append(
            validation("VAL-CUSTOM", "CLM-CUSTOM", ["audit-b", "sensitivity"])
        )
        write_json(self.root / "validation_manifest.json", validations)
        run = read_json(self.root / "run_manifest.json")
        run["files"].append(file_record(self.root, "evidence/result-custom.json"))
        run["files"].sort(key=lambda item: (item["role"], item["path"]))
        write_json(self.root / "run_manifest.json", run)
        refresh_chain(self.root)

        report = self.check()
        self.assertEqual(report.status, "PASS", report.to_dict())

    def test_uncovered_requirement_is_rejected(self) -> None:
        contract = read_json(self.root / "PROBLEM_CONTRACT.json")
        contract["requirements"].append({"id": "REQ-UNANSWERED", "required": True})
        write_json(self.root / "PROBLEM_CONTRACT.json", contract)
        refresh_chain(self.root)
        self.assertIn("REQUIREMENT_UNCOVERED", diagnostic_codes(self.check()))

    def test_unknown_reqid_is_rejected(self) -> None:
        claims = read_json(self.root / "claim_ledger.json")
        claims["claims"][0]["contract_refs"] = ["REQ-NOT-DECLARED"]
        write_json(self.root / "claim_ledger.json", claims)
        refresh_chain(self.root)
        self.assertIn("UNKNOWN_REQUIREMENT_ID", diagnostic_codes(self.check()))

    def test_fixed_value_and_decision_domain_drift_are_rejected(self) -> None:
        claims = read_json(self.root / "claim_ledger.json")
        bindings = claims["claims"][0]["quantity_bindings"]
        bindings[0]["value"] = 4
        bindings[1]["domain"] = {"min": -1, "max": 1}
        write_json(self.root / "claim_ledger.json", claims)
        refresh_chain(self.root)
        codes = diagnostic_codes(self.check())
        self.assertIn("FIXED_QUANTITY_DRIFT", codes)
        self.assertIn("DECISION_DOMAIN_DRIFT", codes)

    def test_claim_type_minimum_validation_is_enforced(self) -> None:
        validations = read_json(self.root / "validation_manifest.json")
        pred = next(item for item in validations["validations"] if item["id"] == "VAL-PRED")
        pred["checks"] = [item for item in pred["checks"] if item["type"] != "leakage_check"]
        write_json(self.root / "validation_manifest.json", validations)
        refresh_chain(self.root)
        self.assertIn("MINIMUM_VALIDATION_MISSING", diagnostic_codes(self.check()))

    def test_inverse_claim_rejects_pipeline_consistency_without_identifiability(self) -> None:
        validations = read_json(self.root / "validation_manifest.json")
        inverse = next(
            item for item in validations["validations"] if item["id"] == "VAL-INVERSE"
        )
        inverse["checks"] = [
            item for item in inverse["checks"] if item["type"] != "identifiability"
        ]
        write_json(self.root / "validation_manifest.json", validations)
        refresh_chain(self.root)
        self.assertIn("MINIMUM_VALIDATION_MISSING", diagnostic_codes(self.check()))

    def test_missing_evidence_path_is_rejected(self) -> None:
        claims = read_json(self.root / "claim_ledger.json")
        claims["claims"][0]["evidence"] = ["evidence/not-created.json"]
        write_json(self.root / "claim_ledger.json", claims)
        refresh_chain(self.root)
        self.assertIn("EVIDENCE_PATH_MISSING", diagnostic_codes(self.check()))

    def test_modified_artifact_breaks_hash_chain(self) -> None:
        (self.root / "evidence" / "result-opt.json").write_text(
            '{"synthetic": false}\n', encoding="utf-8"
        )
        codes = diagnostic_codes(self.check())
        self.assertTrue(
            {"FILE_HASH_MISMATCH", "EVIDENCE_HASH_MISMATCH"}.intersection(codes),
            codes,
        )

    def test_manifest_root_hash_mismatch_is_rejected(self) -> None:
        run = read_json(self.root / "run_manifest.json")
        run["root_hash"] = "0" * 64
        write_json(self.root / "run_manifest.json", run)
        gates = read_json(self.root / "STAGE_GATES.json")
        gates["stages"]["final-integrity"]["upstream_hashes"]["run_manifest"] = sha256(
            self.root / "run_manifest.json"
        )
        write_json(self.root / "STAGE_GATES.json", gates)
        self.assertIn("ROOT_HASH_MISMATCH", diagnostic_codes(self.check()))

    def test_stage_becomes_stale_when_upstream_changes(self) -> None:
        validations = read_json(self.root / "validation_manifest.json")
        validations["validations"][0]["checks"][0]["configuration"]["note"] = "changed"
        write_json(self.root / "validation_manifest.json", validations)
        report = self.check()
        self.assertIn("STALE_STAGE", diagnostic_codes(report))

    def test_missing_human_checkpoint_is_rejected(self) -> None:
        human = read_json(self.root / "reports" / "HUMAN_REVIEW.json")
        human["checkpoints"] = [
            item for item in human["checkpoints"] if item["id"] != "submission"
        ]
        write_json(self.root / "reports" / "HUMAN_REVIEW.json", human)
        refresh_chain(self.root)
        self.assertIn("HUMAN_CHECKPOINT_MISSING", diagnostic_codes(self.check()))

    def test_pending_human_approval_is_rejected(self) -> None:
        human = read_json(self.root / "reports" / "HUMAN_REVIEW.json")
        human["checkpoints"][2]["status"] = "PENDING"
        write_json(self.root / "reports" / "HUMAN_REVIEW.json", human)
        refresh_chain(self.root)
        self.assertIn("HUMAN_APPROVAL_MISSING", diagnostic_codes(self.check()))

    def test_pseudo_empty_or_agent_filled_approval_is_rejected(self) -> None:
        human = read_json(self.root / "reports" / "HUMAN_REVIEW.json")
        item = human["checkpoints"][0]
        item["reviewer"] = "   "
        item["reviewer_type"] = "agent"
        item["comments"] = []
        item["agent_generated"] = True
        write_json(self.root / "reports" / "HUMAN_REVIEW.json", human)
        refresh_chain(self.root)
        codes = diagnostic_codes(self.check())
        self.assertIn("HUMAN_REVIEWER_MISSING", codes)
        self.assertIn("HUMAN_REVIEWER_TYPE_INVALID", codes)
        self.assertIn("HUMAN_REVIEW_COMMENTS_MISSING", codes)
        self.assertIn("AGENT_FILLED_HUMAN_REVIEW", codes)

    def test_missing_human_authorship_attestation_is_rejected(self) -> None:
        human = read_json(self.root / "reports" / "HUMAN_REVIEW.json")
        human.pop("authorship")
        write_json(self.root / "reports" / "HUMAN_REVIEW.json", human)
        refresh_chain(self.root)
        self.assertIn("HUMAN_REVIEW_AUTHORSHIP_MISSING", diagnostic_codes(self.check()))

    def test_simulation_waivers_cap_status_at_unverified(self) -> None:
        problem = read_json(self.root / "PROBLEM_MANIFEST.json")
        problem["task_mode"] = "autonomous-simulation"
        write_json(self.root / "PROBLEM_MANIFEST.json", problem)
        human = read_json(self.root / "reports" / "HUMAN_REVIEW.json")
        human["mode"] = "autonomous-simulation"
        human["authorship"] = {
            "type": "simulation-orchestrator",
            "agent_generated": True,
        }
        for item in human["checkpoints"]:
            item["status"] = "WAIVED_FOR_SIMULATION"
            item.pop("reviewer", None)
            item.pop("reviewer_type", None)
            item.pop("reviewed_at", None)
            item["scope"] = "Synthetic autonomous simulation only"
            item["comments"] = "Human approval intentionally waived for this simulation."
        write_json(self.root / "reports" / "HUMAN_REVIEW.json", human)
        refresh_chain(self.root)

        report = self.check()
        self.assertFalse(report.errors, report.to_dict())
        self.assertEqual(report.status, "UNVERIFIED")
        self.assertFalse(report.passed)

        completed = subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--root", str(self.root), "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stdout)["status"], "UNVERIFIED")

    def test_list_form_stage_schema_is_accepted(self) -> None:
        gates = read_json(self.root / "STAGE_GATES.json")
        stage = dict(gates["stages"]["final-integrity"])
        stage["id"] = "final-integrity"
        gates["stages"] = [stage]
        write_json(self.root / "STAGE_GATES.json", gates)
        report = self.check()
        self.assertEqual(report.status, "PASS", report.to_dict())


if __name__ == "__main__":
    unittest.main()
