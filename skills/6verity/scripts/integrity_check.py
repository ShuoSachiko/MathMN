#!/usr/bin/env python3
"""Cross-problem integrity gate for the MathModel workflow.

The checker deliberately validates evidence structure rather than contest answers.
It uses only the Python standard library and never executes submitted model code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = 1
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

MANIFEST_CANDIDATES: dict[str, tuple[str, ...]] = {
    "problem_manifest": ("reports/PROBLEM_MANIFEST.json", "PROBLEM_MANIFEST.json"),
    "problem_contract": ("reports/PROBLEM_CONTRACT.json", "PROBLEM_CONTRACT.json"),
    "claim_ledger": ("results/claim_ledger.json", "claim_ledger.json"),
    "validation_manifest": (
        "results/validation_manifest.json",
        "validation_manifest.json",
    ),
    "run_manifest": ("results/run_manifest.json", "run_manifest.json"),
    "stage_gates": ("reports/STAGE_GATES.json", "STAGE_GATES.json"),
    "human_review": ("reports/HUMAN_REVIEW.json", "HUMAN_REVIEW.json"),
    "current_versions": ("reports/CURRENT_VERSIONS.json", "CURRENT_VERSIONS.json"),
}

DISPLAY_NAMES = {
    "problem_manifest": "reports/PROBLEM_MANIFEST.json",
    "problem_contract": "reports/PROBLEM_CONTRACT.json",
    "claim_ledger": "results/claim_ledger.json",
    "validation_manifest": "results/validation_manifest.json",
    "run_manifest": "results/run_manifest.json",
    "stage_gates": "reports/STAGE_GATES.json",
    "human_review": "reports/HUMAN_REVIEW.json",
    "current_versions": "reports/CURRENT_VERSIONS.json",
}

COMPLETE_STATUSES = {"pass", "passed", "complete", "completed", "done"}
INCOMPLETE_STATUSES = {"not_started", "pending", "running", "in_progress"}
FAILED_STATUSES = {"fail", "failed", "blocked", "stale"}
SUPPORTED_CLAIM_STATUSES = {"supported"}
HUMAN_CHECKPOINTS = (
    "intake",
    "contract",
    "model",
    "results",
    "paper",
    "submission",
)
HUMAN_REVIEW_MODES = {"live_competition", "human_supervised"}
SIMULATION_MODE = "autonomous_simulation"


# Each inner tuple is an alternatives group; every group must be represented by
# at least one passed validation check.  The vocabulary is intentionally about
# evidence properties, not preferred algorithms.
BUILTIN_PROFILES: dict[str, tuple[tuple[str, ...], ...]] = {
    "exact": (
        (
            "independent_recompute",
            "analytic_check",
            "proof",
            "exhaustive_check",
            "cross_implementation",
        ),
    ),
    "proved": (
        ("proof", "proof_review", "formal_verification", "analytic_check"),
    ),
    "optimization": (
        ("feasibility", "constraint_check"),
        ("domain_coverage", "search_domain", "boundary_check"),
        (
            "optimality_certificate",
            "exact_solver_gap",
            "global_bound",
            "exhaustive_check",
            "multi_start",
            "resolution_convergence",
            "baseline_comparison",
        ),
    ),
    "prediction": (
        ("holdout", "backtest", "cross_validation", "out_of_sample"),
        ("error_metrics", "calibration", "predictive_metrics"),
        ("leakage_check", "temporal_split_check"),
    ),
    "inverse": (
        (
            "synthetic_recovery",
            "forward_model_check",
            "analytic_check",
            "benchmark_case",
        ),
        (
            "identifiability",
            "profile_likelihood",
            "condition_check",
            "parameter_recovery",
        ),
        (
            "baseline_comparison",
            "cross_method",
            "window_sensitivity",
            "alternative_specification",
        ),
        (
            "uncertainty",
            "sensitivity",
            "systematic_error",
            "nuisance_parameter_sensitivity",
        ),
    ),
    "statistical": (
        ("assumption_check", "diagnostic_check"),
        ("uncertainty", "confidence_interval", "bootstrap"),
        ("robustness", "sensitivity", "alternative_specification"),
    ),
    "evaluation": (
        ("direction_normalization", "scale_check"),
        ("weight_provenance", "weight_consistency"),
        ("sensitivity", "ranking_stability", "robustness"),
    ),
    "simulation": (
        (
            "constraint_check",
            "invariant_check",
            "conservation_check",
            "physical_boundary",
        ),
        (
            "resolution_convergence",
            "step_convergence",
            "sample_convergence",
        ),
        (
            "independent_recompute",
            "cross_implementation",
            "analytic_check",
            "benchmark_case",
        ),
    ),
    "numerical": (
        ("constraint_check", "residual_check", "sanity_check"),
        ("resolution_convergence", "tolerance_convergence", "condition_check"),
    ),
    "heuristic": (
        ("feasibility", "constraint_check"),
        ("multi_start", "multi_seed", "stability"),
        ("baseline_comparison", "bound_comparison", "small_instance_exact"),
    ),
    "descriptive": (
        ("source_trace", "data_reconciliation", "independent_recompute"),
    ),
    "causal": (
        ("identification_check", "design_check"),
        ("confounding_check", "falsification_test"),
        ("uncertainty", "confidence_interval", "bootstrap"),
        ("robustness", "sensitivity", "alternative_specification"),
    ),
}

CLAIM_TYPE_ALIASES = {
    "global_optimization": "optimization",
    "local_optimization": "optimization",
    "optimization_result": "optimization",
    "forecast": "prediction",
    "forecasting": "prediction",
    "regression": "prediction",
    "classification": "prediction",
    "inverse_problem": "inverse",
    "parameter_estimation": "inverse",
    "parameter_inference": "inverse",
    "system_identification": "inverse",
    "inference": "statistical",
    "ranking": "evaluation",
    "assessment": "evaluation",
    "scoring": "evaluation",
    "mechanistic": "simulation",
    "dynamics": "simulation",
    "numerically_verified": "numerical",
    "approximate": "numerical",
    "comparison": "descriptive",
}

QUANTITY_KIND_ALIASES = {
    "constant": "fixed",
    "given": "fixed",
    "fixed_quantity": "fixed",
    "decision_variable": "decision",
    "variable": "decision",
    "state_variable": "state",
    "control_parameter": "control",
    "algorithm_parameter": "control",
    "derived_quantity": "derived",
}


@dataclass(frozen=True)
class Diagnostic:
    severity: str
    code: str
    message: str
    source: str | None = None


class IntegrityReport:
    def __init__(self) -> None:
        self.diagnostics: list[Diagnostic] = []
        self._seen: set[tuple[str, str, str, str | None]] = set()
        self.checked_manifests: dict[str, str] = {}
        self.verification_ceiling: str | None = None

    def add(
        self,
        severity: str,
        code: str,
        message: str,
        source: str | None = None,
    ) -> None:
        item = Diagnostic(severity.upper(), code, message, source)
        key = (item.severity, item.code, item.message, item.source)
        if key not in self._seen:
            self._seen.add(key)
            self.diagnostics.append(item)

    def error(self, code: str, message: str, source: str | None = None) -> None:
        self.add("ERROR", code, message, source)

    def warn(self, code: str, message: str, source: str | None = None) -> None:
        self.add("WARN", code, message, source)

    @property
    def errors(self) -> list[Diagnostic]:
        return [item for item in self.diagnostics if item.severity == "ERROR"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [item for item in self.diagnostics if item.severity == "WARN"]

    @property
    def passed(self) -> bool:
        return self.status == "PASS"

    @property
    def status(self) -> str:
        if self.errors:
            return "FAIL"
        return self.verification_ceiling or "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "checked_manifests": self.checked_manifests,
            "diagnostics": [asdict(item) for item in self.diagnostics],
        }


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalized_hash(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    result = value.strip().lower()
    if result.startswith("sha256:"):
        result = result[7:]
    return result if HEX64_RE.fullmatch(result) else None


def normalize_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def first_present(mapping: Mapping[str, Any], names: Sequence[str]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


class IntegrityChecker:
    def __init__(
        self,
        root: Path,
        manifest_overrides: Mapping[str, Path | str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.overrides = dict(manifest_overrides or {})
        self.report = IntegrityReport()
        self.docs: dict[str, dict[str, Any]] = {}
        self.paths: dict[str, Path] = {}
        self.raw_hashes: dict[str, str] = {}
        self.artifact_hashes: dict[str, str] = {}
        self.requirements: dict[str, dict[str, Any]] = {}
        self.quantities: dict[str, dict[str, Any]] = {}
        self.claims: dict[str, dict[str, Any]] = {}
        self.validation_ids: set[str] = set()
        self.validation_claims: dict[str, set[str]] = {}

    def run(self) -> IntegrityReport:
        if not self.root.is_dir():
            self.report.error("ROOT_NOT_FOUND", f"project root is not a directory: {self.root}")
            return self.report

        self._load_manifests()
        if len(self.docs) != len(MANIFEST_CANDIDATES):
            return self.report

        self._check_headers()
        self._register_manifest_files()
        self._check_problem_manifest()
        self._check_run_manifest()
        self._check_problem_contract()
        self._check_claim_ledger()
        claim_checks = self._check_validation_manifest()
        self._check_claim_validation_coverage(claim_checks)
        self._check_requirement_coverage()
        self._check_downstream_quantities()
        self._check_human_review()
        self._check_current_versions()
        self._check_stage_gates()
        return self.report

    def _load_manifests(self) -> None:
        for logical, candidates in MANIFEST_CANDIDATES.items():
            path: Path | None = None
            override = self.overrides.get(logical)
            if override is not None:
                raw = Path(override)
                path = raw if raw.is_absolute() else self.root / raw
            else:
                for candidate in candidates:
                    current = self.root / candidate
                    if current.is_file():
                        path = current
                        break
            if path is None or not path.is_file():
                self.report.error(
                    "MISSING_MANIFEST",
                    f"required manifest not found: {DISPLAY_NAMES[logical]}",
                    DISPLAY_NAMES[logical],
                )
                continue
            try:
                raw = path.read_bytes()
                parsed = json.loads(raw.decode("utf-8-sig"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                self.report.error(
                    "INVALID_JSON",
                    f"cannot read JSON: {exc}",
                    self._label(path),
                )
                continue
            if not isinstance(parsed, dict):
                self.report.error(
                    "INVALID_SCHEMA",
                    "manifest root must be a JSON object",
                    self._label(path),
                )
                continue
            self.docs[logical] = parsed
            self.paths[logical] = path.resolve()
            self.raw_hashes[logical] = sha256_bytes(raw)
            self.report.checked_manifests[logical] = self._label(path)

    def _check_headers(self) -> None:
        for logical, doc in self.docs.items():
            version = doc.get("schema_version")
            if version not in (SCHEMA_VERSION, str(SCHEMA_VERSION), "v1"):
                self.report.error(
                    "UNSUPPORTED_SCHEMA_VERSION",
                    f"schema_version must be {SCHEMA_VERSION}, got {version!r}",
                    self._source(logical),
                )
        expected_types = {"problem_manifest": "problem", "run_manifest": "run"}
        for logical, expected in expected_types.items():
            actual = normalize_token(self.docs[logical].get("manifest_type"))
            if actual != expected:
                self.report.error(
                    "INVALID_MANIFEST_TYPE",
                    f"manifest_type must be {expected!r}, got {actual!r}",
                    self._source(logical),
                )

    def _register_manifest_files(self) -> None:
        for logical, path in self.paths.items():
            self._register_artifact(path, self.raw_hashes[logical], self._source(logical))

    def _check_problem_manifest(self) -> None:
        doc = self.docs["problem_manifest"]
        source = self._source("problem_manifest")
        inputs = doc.get("inputs")
        if not isinstance(inputs, list) or not inputs:
            self.report.error("PROBLEM_INPUTS_MISSING", "inputs must be a non-empty list", source)
            return
        self._check_root_hash(doc, "inputs", source, strip_verification=True)
        for index, record in enumerate(inputs):
            owner = f"{source}:inputs[{index}]"
            if not isinstance(record, dict):
                self.report.error("INVALID_FILE_RECORD", "input must be an object", owner)
                continue
            kind = normalize_token(record.get("type") or "file")
            if kind in {"dir", "directory"}:
                self._check_input_directory(record, owner)
            else:
                self._verify_file_record(record, owner, require_hash=True)

    def _check_input_directory(self, record: Mapping[str, Any], owner: str) -> None:
        directory = self._resolve_project_path(record.get("path"), owner)
        if directory is None:
            return
        if not directory.is_dir():
            self.report.error("INPUT_DIRECTORY_MISSING", "declared input directory is missing", owner)
            return
        members = record.get("files")
        if not isinstance(members, list):
            self.report.error(
                "DIRECTORY_FILES_MISSING",
                "directory input must enumerate files",
                owner,
            )
            return
        declared: set[str] = set()
        for index, member in enumerate(members):
            member_owner = f"{owner}:files[{index}]"
            if not isinstance(member, dict):
                self.report.error("INVALID_FILE_RECORD", "directory member must be an object", member_owner)
                continue
            path_value = member.get("path")
            candidate = self._resolve_member_path(directory, path_value, member_owner)
            if candidate is None:
                continue
            declared.add(self._label(candidate))
            self._verify_file_record(member, member_owner, resolved=candidate, require_hash=True)
        actual = {self._label(path) for path in directory.rglob("*") if path.is_file()}
        missing = sorted(actual - declared)
        extra = sorted(declared - actual)
        if missing:
            self.report.error(
                "UNMANIFESTED_INPUT_FILE",
                f"directory contains unmanifested files: {', '.join(missing[:8])}",
                owner,
            )
        if extra:
            self.report.error(
                "MISSING_INPUT_FILE",
                f"manifest lists missing directory files: {', '.join(extra[:8])}",
                owner,
            )

    def _check_run_manifest(self) -> None:
        doc = self.docs["run_manifest"]
        source = self._source("run_manifest")
        files = doc.get("files")
        if not isinstance(files, list) or not files:
            self.report.error("RUN_FILES_MISSING", "files must be a non-empty list", source)
            return
        self._check_root_hash(
            doc, "files", source, composite=("commands", "files", "runtime", "sources")
        )
        run_paths: set[str] = set()
        for index, record in enumerate(files):
            owner = f"{source}:files[{index}]"
            if not isinstance(record, dict):
                self.report.error("INVALID_FILE_RECORD", "run file must be an object", owner)
                continue
            path = self._verify_file_record(record, owner, require_hash=True)
            if path is not None:
                run_paths.add(self._label(path))

        sources = doc.get("sources", [])
        if sources is not None and not isinstance(sources, list):
            self.report.error("RUN_SOURCES_INVALID", "sources must be a list", source)
            return
        for index, item in enumerate(sources or []):
            owner = f"{source}:sources[{index}]"
            if not isinstance(item, dict):
                self.report.error("RUN_SOURCE_INVALID", "source must be an object", owner)
                continue
            path = self._resolve_project_path(item.get("path"), owner)
            if path is None or not path.exists():
                self.report.error("RUN_SOURCE_MISSING", "run source path is missing", owner)
                continue
            label = self._label(path)
            if path.is_file() and label not in run_paths:
                self.report.error(
                    "RUN_SOURCE_UNHASHED",
                    f"source file is not covered by run files: {label}",
                    owner,
                )
            if path.is_dir() and not any(value.startswith(label.rstrip("/") + "/") for value in run_paths):
                self.report.error(
                    "RUN_SOURCE_UNHASHED",
                    f"source directory has no hashed run files: {label}",
                    owner,
                )

    def _check_problem_contract(self) -> None:
        doc = self.docs["problem_contract"]
        source = self._source("problem_contract")
        status = normalize_token(doc.get("status") or doc.get("contract_status"))
        if status != "frozen":
            self.report.error("CONTRACT_NOT_FROZEN", "problem contract status must be FROZEN", source)
        self._check_reference(
            doc,
            ("problem_manifest_sha256",),
            self.raw_hashes["problem_manifest"],
            source,
            alternate=("problem_root_hash", self.docs["problem_manifest"].get("root_hash")),
        )

        requirements = doc.get("requirements")
        if not isinstance(requirements, list) or not requirements:
            self.report.error("REQUIREMENTS_MISSING", "requirements must be a non-empty list", source)
        else:
            for index, item in enumerate(requirements):
                owner = f"{source}:requirements[{index}]"
                if isinstance(item, str):
                    item = {"id": item}
                if not isinstance(item, dict):
                    self.report.error("INVALID_REQUIREMENT", "requirement must be an object", owner)
                    continue
                req_id = str(item.get("id") or item.get("req_id") or "").strip()
                if not req_id:
                    self.report.error("REQUIREMENT_ID_MISSING", "requirement id is missing", owner)
                    continue
                if not re.match(r"^REQ(?:[-_].+)?$", req_id, re.IGNORECASE):
                    self.report.error(
                        "INVALID_REQUIREMENT_ID",
                        f"requirement id must use the REQ-* namespace: {req_id}",
                        owner,
                    )
                if req_id in self.requirements:
                    self.report.error("DUPLICATE_REQUIREMENT_ID", f"duplicate requirement: {req_id}", owner)
                    continue
                self.requirements[req_id] = dict(item)

        for quantity in self._quantity_records(doc, contract=True):
            quantity_id = quantity["id"]
            if quantity.get("kind") not in {"fixed", "decision", "state", "control", "derived"}:
                self.report.error(
                    "QUANTITY_KIND_INVALID",
                    f"contract quantity has an invalid or missing kind: {quantity_id}",
                    source,
                )
            if quantity_id in self.quantities:
                self.report.error(
                    "DUPLICATE_QUANTITY_ID",
                    f"duplicate contract quantity: {quantity_id}",
                    source,
                )
            else:
                self.quantities[quantity_id] = quantity

    def _check_claim_ledger(self) -> None:
        doc = self.docs["claim_ledger"]
        source = self._source("claim_ledger")
        self._check_reference(
            doc,
            ("problem_contract_sha256", "contract_sha256"),
            self.raw_hashes["problem_contract"],
            source,
        )
        if "problem_manifest_sha256" in doc:
            self._check_reference(
                doc,
                ("problem_manifest_sha256",),
                self.raw_hashes["problem_manifest"],
                source,
            )

        claims = doc.get("claims")
        if not isinstance(claims, list) or not claims:
            self.report.error("CLAIMS_MISSING", "claims must be a non-empty list", source)
            return
        for index, item in enumerate(claims):
            owner = f"{source}:claims[{index}]"
            if not isinstance(item, dict):
                self.report.error("INVALID_CLAIM", "claim must be an object", owner)
                continue
            claim_id = str(item.get("id") or item.get("claim_id") or "").strip()
            if not claim_id:
                self.report.error("CLAIM_ID_MISSING", "claim id is missing", owner)
                continue
            if claim_id in self.claims:
                self.report.error("DUPLICATE_CLAIM_ID", f"duplicate claim: {claim_id}", owner)
                continue
            status = normalize_token(item.get("status") or "supported")
            claim_type = normalize_token(item.get("claim_type") or item.get("type"))
            refs = self._string_list(
                first_present(item, ("contract_refs", "requirement_ids", "req_ids", "requirements"))
            )
            if not refs and status in SUPPORTED_CLAIM_STATUSES:
                self.report.error("CLAIM_REQID_MISSING", "supported claim has no ReqID", owner)
            for req_id in refs:
                if req_id not in self.requirements:
                    self.report.error(
                        "UNKNOWN_REQUIREMENT_ID",
                        f"claim references unknown requirement: {req_id}",
                        owner,
                    )
            if not claim_type and status in SUPPORTED_CLAIM_STATUSES:
                self.report.error("CLAIM_TYPE_MISSING", "supported claim has no claim_type", owner)
            normalized = dict(item)
            normalized["_id"] = claim_id
            normalized["_status"] = status
            normalized["_type"] = claim_type
            normalized["_refs"] = refs
            self.claims[claim_id] = normalized
            if status in SUPPORTED_CLAIM_STATUSES:
                evidence = self._collect_evidence(
                    item,
                    ("evidence", "evidence_paths", "result_evidence", "artifacts"),
                )
                self._validate_evidence(evidence, owner, required=True)

    def _check_validation_manifest(self) -> dict[str, set[str]]:
        doc = self.docs["validation_manifest"]
        source = self._source("validation_manifest")
        self._check_reference(
            doc,
            ("claim_ledger_sha256",),
            self.raw_hashes["claim_ledger"],
            source,
        )
        self._check_reference(
            doc,
            ("problem_contract_sha256", "contract_sha256"),
            self.raw_hashes["problem_contract"],
            source,
        )

        validations = doc.get("validations")
        if not isinstance(validations, list) or not validations:
            self.report.error("VALIDATIONS_MISSING", "validations must be a non-empty list", source)
            return {}

        claim_checks: dict[str, set[str]] = {claim_id: set() for claim_id in self.claims}
        for index, validation in enumerate(validations):
            owner = f"{source}:validations[{index}]"
            if not isinstance(validation, dict):
                self.report.error("INVALID_VALIDATION", "validation must be an object", owner)
                continue
            validation_id = str(validation.get("id") or validation.get("validation_id") or "").strip()
            if not validation_id:
                self.report.error("VALIDATION_ID_MISSING", "validation id is missing", owner)
                continue
            if validation_id in self.validation_ids:
                self.report.error(
                    "DUPLICATE_VALIDATION_ID",
                    f"duplicate validation: {validation_id}",
                    owner,
                )
                continue
            self.validation_ids.add(validation_id)
            claim_ids = self._string_list(
                first_present(validation, ("claim_ids", "claims", "claim_refs"))
            )
            if not claim_ids:
                self.report.error("VALIDATION_CLAIMS_MISSING", "validation has no claim_ids", owner)
            self.validation_claims[validation_id] = set(claim_ids)
            for claim_id in claim_ids:
                if claim_id not in self.claims:
                    self.report.error(
                        "UNKNOWN_CLAIM_ID",
                        f"validation references unknown claim: {claim_id}",
                        owner,
                    )

            status = normalize_token(validation.get("status") or "passed")
            checks = self._validation_checks(validation)
            if not checks:
                self.report.error("VALIDATION_CHECKS_MISSING", "validation has no checks", owner)
                continue
            parent_evidence = self._collect_evidence(
                validation,
                ("evidence", "evidence_paths", "artifacts"),
            )
            parent_config = validation.get("configuration")
            for check_index, check in enumerate(checks):
                check_owner = f"{owner}:checks[{check_index}]"
                check_type = normalize_token(check.get("type") or check.get("validation_type"))
                if not check_type:
                    self.report.error("VALIDATION_TYPE_MISSING", "validation check type is missing", check_owner)
                    continue
                check_status = normalize_token(check.get("status") or status)
                config = check.get("configuration")
                if config is None and isinstance(parent_config, dict):
                    keyed = parent_config.get(check_type)
                    config = keyed if isinstance(keyed, dict) else parent_config
                if check_status in COMPLETE_STATUSES:
                    if not isinstance(config, dict) or not config:
                        self.report.error(
                            "VALIDATION_CONFIG_MISSING",
                            f"passed check {check_type!r} needs a non-empty configuration object",
                            check_owner,
                        )
                    evidence = self._collect_evidence(
                        check,
                        ("evidence", "evidence_paths", "artifacts"),
                    ) or parent_evidence
                    self._validate_evidence(evidence, check_owner, required=True)
                    for claim_id in claim_ids:
                        if claim_id in claim_checks:
                            claim_checks[claim_id].add(check_type)
                elif check_status in FAILED_STATUSES:
                    self.report.error(
                        "VALIDATION_FAILED",
                        f"validation check failed: {check_type}",
                        check_owner,
                    )
        return claim_checks

    def _check_claim_validation_coverage(self, claim_checks: Mapping[str, set[str]]) -> None:
        profiles = {name: list(groups) for name, groups in BUILTIN_PROFILES.items()}
        for doc in (self.docs["validation_manifest"], self.docs["stage_gates"]):
            custom = doc.get("claim_type_profiles") or doc.get("claim_type_minimums")
            if not isinstance(custom, dict):
                continue
            for raw_name, raw_profile in custom.items():
                name = normalize_token(raw_name)
                groups = self._parse_profile(raw_profile, f"claim_type_profiles.{raw_name}")
                if not groups:
                    continue
                if name in profiles:
                    profiles[name].extend(groups)
                else:
                    profiles[name] = groups

        ledger_source = self._source("claim_ledger")
        validation_source = self._source("validation_manifest")
        for claim_id, claim in self.claims.items():
            if claim["_status"] not in SUPPORTED_CLAIM_STATUSES:
                continue
            explicit_ids = self._string_list(
                first_present(claim, ("validation_ids", "validations", "validation_refs"))
            )
            for validation_id in explicit_ids:
                if validation_id not in self.validation_ids:
                    self.report.error(
                        "UNKNOWN_VALIDATION_ID",
                        f"claim {claim_id} references unknown validation: {validation_id}",
                        ledger_source,
                    )
                elif claim_id not in self.validation_claims.get(validation_id, set()):
                    self.report.error(
                        "VALIDATION_LINK_MISMATCH",
                        f"claim {claim_id} references validation {validation_id}, but that validation does not link back",
                        ledger_source,
                    )
            checks = set(claim_checks.get(claim_id, set()))
            if not checks:
                self.report.error(
                    "CLAIM_UNVALIDATED",
                    f"supported claim has no passed validation: {claim_id}",
                    validation_source,
                )
                continue
            raw_type = claim["_type"]
            profile_name = CLAIM_TYPE_ALIASES.get(raw_type, raw_type)
            groups = list(profiles.get(profile_name, []))
            claim_specific = claim.get("minimum_validation") or claim.get("required_checks")
            if claim_specific is not None:
                groups.extend(self._parse_profile(claim_specific, f"claim {claim_id}"))
            if not groups:
                self.report.error(
                    "CLAIM_TYPE_UNCONFIGURED",
                    f"claim_type {raw_type!r} has no minimum validation profile",
                    ledger_source,
                )
                continue
            for alternatives in groups:
                if not checks.intersection(alternatives):
                    self.report.error(
                        "MINIMUM_VALIDATION_MISSING",
                        f"claim {claim_id} ({raw_type}) needs one of "
                        f"[{', '.join(alternatives)}]; passed checks are "
                        f"[{', '.join(sorted(checks))}]",
                        validation_source,
                    )

    def _check_requirement_coverage(self) -> None:
        covered: set[str] = set()
        for claim in self.claims.values():
            if claim["_status"] in SUPPORTED_CLAIM_STATUSES:
                covered.update(claim["_refs"])
        source = self._source("problem_contract")
        for req_id, requirement in self.requirements.items():
            required = requirement.get("required", not bool(requirement.get("optional", False)))
            status = normalize_token(requirement.get("status"))
            if status in {"waived", "not_applicable", "out_of_scope"}:
                required = False
            if required and req_id not in covered:
                self.report.error(
                    "REQUIREMENT_UNCOVERED",
                    f"required ReqID has no supported claim: {req_id}",
                    source,
                )

    def _check_downstream_quantities(self) -> None:
        sources: list[tuple[str, Mapping[str, Any]]] = [
            (self._source("claim_ledger"), self.docs["claim_ledger"]),
            (self._source("validation_manifest"), self.docs["validation_manifest"]),
            (self._source("run_manifest"), self.docs["run_manifest"]),
        ]
        for claim_id, claim in self.claims.items():
            sources.append((f"claim {claim_id}", claim))
        for source, container in sources:
            for record in self._quantity_records(container, contract=False):
                self._check_quantity_binding(record, source)
            for field in ("fixed_overrides", "quantity_overrides", "overrides"):
                for raw in as_list(container.get(field)):
                    record = self._normalize_quantity(raw, None, None)
                    if record and record["id"] in self.quantities:
                        contract = self.quantities[record["id"]]
                        if contract["kind"] == "fixed":
                            self.report.error(
                                "FIXED_QUANTITY_OVERRIDE",
                                f"fixed quantity is overridden: {record['id']}",
                                source,
                            )

    def _check_current_versions(self) -> None:
        doc = self.docs["current_versions"]
        source = self._source("current_versions")
        if doc.get("problem_root_hash") != self.docs["problem_manifest"].get("root_hash"):
            self.report.error(
                "CURRENT_VERSIONS_STALE",
                "CURRENT_VERSIONS.json is not bound to the current problem root hash",
                source,
            )
        selections = doc.get("selections")
        if not isinstance(selections, dict):
            self.report.error("CURRENT_VERSIONS_INVALID", "selections must be an object", source)
            return
        if doc.get("selection_hash") != sha256_bytes(canonical_json_bytes(selections)):
            self.report.error(
                "CURRENT_VERSIONS_HASH_INVALID",
                "selection_hash does not match the selected task versions",
                source,
            )
        history_path = self.root / "reports" / "VERSION_DECISIONS.jsonl"
        if not history_path.is_file():
            self.report.error("VERSION_DECISIONS_MISSING", "version selection history is missing", source)
            return
        try:
            raw = history_path.read_bytes()
        except OSError as exc:
            self.report.error("VERSION_DECISIONS_INVALID", f"cannot read selection history: {exc}", source)
            return
        if raw and not raw.endswith(b"\n"):
            self.report.error("VERSION_DECISIONS_INVALID", "selection history must end with a newline", source)
        previous_hash: str | None = None
        latest: dict[str, dict[str, Any]] = {}
        for line_number, line in enumerate(raw.splitlines(), 1):
            try:
                event = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError) as exc:
                self.report.error("VERSION_DECISIONS_INVALID", f"invalid line {line_number}: {exc}", source)
                continue
            if not isinstance(event, dict):
                self.report.error("VERSION_DECISIONS_INVALID", f"line {line_number} is not an object", source)
                continue
            event_hash = event.get("event_hash")
            payload = {key: value for key, value in event.items() if key != "event_hash"}
            if event.get("seq") != line_number or event.get("previous_event_hash") != previous_hash:
                self.report.error("VERSION_DECISIONS_CHAIN_BROKEN", f"history chain is broken at line {line_number}", source)
            if event_hash != sha256_bytes(canonical_json_bytes(payload)):
                self.report.error("VERSION_DECISIONS_HASH_INVALID", f"event hash is invalid at line {line_number}", source)
            previous_hash = event_hash if isinstance(event_hash, str) else None
            task_id = str(event.get("task_id") or "").strip()
            if task_id:
                latest[task_id] = event
        approved_ids = {
            str(item.get("approval_id"))
            for item in as_list(self.docs["human_review"].get("checkpoints"))
            if isinstance(item, dict)
            and normalize_token(item.get("status")) == "approved"
            and item.get("approval_id")
        }
        review_mode = normalize_token(
            self.docs["problem_manifest"].get("review_mode")
            or self.docs["human_review"].get("review_mode")
            or self.docs["human_review"].get("mode")
        )
        for task_id, selection in selections.items():
            owner = f"{source}:selections.{task_id}"
            if not isinstance(selection, dict):
                self.report.error("CURRENT_VERSION_INVALID", "selection must be an object", owner)
                continue
            version_hash = selection.get("version_hash")
            if not isinstance(version_hash, str) or not HEX64_RE.fullmatch(version_hash):
                self.report.error("CURRENT_VERSION_HASH_INVALID", "version_hash must be SHA-256", owner)
            else:
                version_path = (
                    self.root / ".task_versions" / "tasks" / str(task_id)
                    / "versions" / f"{version_hash}.json"
                )
                if not version_path.is_file():
                    self.report.error("CURRENT_VERSION_MISSING", "selected immutable version is missing", owner)
            event = latest.get(str(task_id))
            if event is None or event.get("selected_version_hash") != version_hash:
                self.report.error("CURRENT_VERSION_HISTORY_MISMATCH", "selection disagrees with append-only history", owner)
            if review_mode in HUMAN_REVIEW_MODES and selection.get("human_review_ref") not in approved_ids:
                self.report.error(
                    "CURRENT_VERSION_REVIEW_MISSING",
                    "human-supervised selection must reference an APPROVED checkpoint approval_id",
                    owner,
                )

    def _check_stage_gates(self) -> None:
        doc = self.docs["stage_gates"]
        source = self._source("stage_gates")
        self._check_reference(
            doc,
            ("problem_manifest_sha256",),
            self.raw_hashes["problem_manifest"],
            source,
            alternate=("problem_root_hash", self.docs["problem_manifest"].get("root_hash")),
        )
        raw_stages = doc.get("stages")
        stages: list[dict[str, Any]] = []
        if isinstance(raw_stages, list):
            stages = [dict(item) for item in raw_stages if isinstance(item, dict)]
            if len(stages) != len(raw_stages):
                self.report.error("INVALID_STAGE", "each stage must be an object", source)
        elif isinstance(raw_stages, dict):
            for stage_id, item in raw_stages.items():
                if not isinstance(item, dict):
                    self.report.error("INVALID_STAGE", f"stage {stage_id} must be an object", source)
                    continue
                value = dict(item)
                value.setdefault("id", stage_id)
                stages.append(value)
        else:
            self.report.error("STAGES_MISSING", "stages must be a list or object", source)
            return
        if not stages:
            self.report.error("STAGES_MISSING", "at least one stage is required", source)
            return

        by_id: dict[str, dict[str, Any]] = {}
        complete_ids: set[str] = set()
        tracked: set[str] = set()
        inherited_upstream = doc.get("upstream_hashes")
        for index, stage in enumerate(stages):
            owner = f"{source}:stages[{index}]"
            stage_id = str(stage.get("id") or "").strip()
            if not stage_id:
                self.report.error("STAGE_ID_MISSING", "stage id is missing", owner)
                continue
            if stage_id in by_id:
                self.report.error("DUPLICATE_STAGE_ID", f"duplicate stage: {stage_id}", owner)
                continue
            by_id[stage_id] = stage
            required = bool(stage.get("required", True))
            status = normalize_token(stage.get("status"))
            if status in COMPLETE_STATUSES:
                complete_ids.add(stage_id)
            elif status in FAILED_STATUSES:
                self.report.error("STAGE_FAILED", f"stage is not valid: {stage_id} ({status})", owner)
            elif required:
                self.report.error("STAGE_INCOMPLETE", f"required stage is incomplete: {stage_id} ({status})", owner)

            if status not in COMPLETE_STATUSES:
                continue
            upstream = stage.get("upstream_hashes", inherited_upstream)
            pairs = self._upstream_pairs(upstream, owner)
            if not pairs:
                self.report.error(
                    "STAGE_UPSTREAM_MISSING",
                    f"completed stage has no upstream hash snapshot: {stage_id}",
                    owner,
                )
            for reference, expected in pairs:
                current, logical = self._current_reference_hash(reference)
                if current is None:
                    self.report.error(
                        "STAGE_UPSTREAM_UNKNOWN",
                        f"cannot resolve upstream reference: {reference}",
                        owner,
                    )
                    continue
                tracked.add(logical)
                if normalized_hash(expected) != normalized_hash(current):
                    self.report.error(
                        "STALE_STAGE",
                        f"stage {stage_id} has stale upstream hash for {reference}",
                        owner,
                    )
            evidence = self._collect_evidence(
                stage,
                ("evidence", "evidence_paths"),
            )
            self._validate_evidence(evidence, owner, required=True)
            artifacts = self._collect_evidence(
                stage,
                ("required_artifacts", "artifacts"),
            )
            self._validate_evidence(artifacts, owner, required=bool(stage.get("required_artifacts")))
            self._check_stage_timestamp(stage, pairs, evidence + artifacts, owner)

        for stage_id, stage in by_id.items():
            if stage_id not in complete_ids:
                continue
            for dependency in self._string_list(stage.get("depends_on")):
                if dependency not in by_id:
                    self.report.error(
                        "UNKNOWN_STAGE_DEPENDENCY",
                        f"stage {stage_id} depends on unknown stage {dependency}",
                        source,
                    )
                elif dependency not in complete_ids:
                    self.report.error(
                        "STALE_STAGE",
                        f"stage {stage_id} completed before dependency {dependency}",
                        source,
                    )

        for logical in (
            "problem_manifest",
            "problem_contract",
            "claim_ledger",
            "validation_manifest",
            "run_manifest",
            "human_review",
            "current_versions",
        ):
            if logical not in tracked:
                self.report.error(
                    "STAGE_UNTRACKED_UPSTREAM",
                    f"no completed stage snapshots {DISPLAY_NAMES[logical]}",
                    source,
                )

    def _check_human_review(self) -> None:
        doc = self.docs["human_review"]
        source = self._source("human_review")
        self._check_reference(
            doc,
            ("problem_manifest_sha256",),
            self.raw_hashes["problem_manifest"],
            source,
            alternate=("problem_root_hash", self.docs["problem_manifest"].get("root_hash")),
        )
        if "problem_contract_sha256" in doc or "contract_sha256" in doc:
            self._check_reference(
                doc,
                ("problem_contract_sha256", "contract_sha256"),
                self.raw_hashes["problem_contract"],
                source,
            )

        mode = normalize_token(doc.get("mode") or doc.get("review_mode") or doc.get("task_mode"))
        problem_mode = normalize_token(
            self.docs["problem_manifest"].get("task_mode")
            or self.docs["problem_manifest"].get("mode")
            or self.docs["problem_contract"].get("task_mode")
        )
        if mode not in HUMAN_REVIEW_MODES | {SIMULATION_MODE}:
            self.report.error(
                "HUMAN_REVIEW_MODE_INVALID",
                "review mode must be live-competition, human-supervised, or autonomous-simulation",
                source,
            )
            return
        if problem_mode in HUMAN_REVIEW_MODES and mode == SIMULATION_MODE:
            self.report.error(
                "HUMAN_REVIEW_MODE_CONFLICT",
                "a live-competition task cannot waive review as autonomous simulation",
                source,
            )
        authorship = doc.get("authorship")
        if not isinstance(authorship, dict):
            self.report.error(
                "HUMAN_REVIEW_AUTHORSHIP_MISSING",
                "authorship must attest that a human or controlled human-review UI recorded this file",
                source,
            )
        else:
            author_type = normalize_token(
                authorship.get("type") or authorship.get("author_type") or authorship.get("recorded_by")
            )
            if mode in HUMAN_REVIEW_MODES:
                if author_type not in {"human", "controlled_human_review_ui", "human_review_ui"}:
                    self.report.error(
                        "HUMAN_REVIEW_AUTHORSHIP_INVALID",
                        "authorship type must be human or controlled-human-review-ui",
                        source,
                    )
                if authorship.get("agent_generated") is not False:
                    self.report.error(
                        "HUMAN_REVIEW_AUTHORSHIP_INVALID",
                        "authorship.agent_generated must explicitly be false",
                        source,
                    )
            elif not (
                author_type in {"simulation_orchestrator", "agent_simulation"}
                and authorship.get("agent_generated") is True
            ) and not (
                author_type in {"human", "controlled_human_review_ui", "human_review_ui"}
                and authorship.get("agent_generated") is False
            ):
                self.report.error(
                    "HUMAN_REVIEW_AUTHORSHIP_INVALID",
                    "simulation waiver authorship must identify a simulation orchestrator or a human",
                    source,
                )
        if mode == SIMULATION_MODE:
            self.report.verification_ceiling = "UNVERIFIED"
            self.report.warn(
                "SIMULATION_UNVERIFIED",
                "human checkpoints are waived for simulation; final status cannot exceed UNVERIFIED",
                source,
            )

        raw_checkpoints = doc.get("checkpoints")
        checkpoints: dict[str, dict[str, Any]] = {}
        if isinstance(raw_checkpoints, dict):
            for checkpoint_id, value in raw_checkpoints.items():
                if not isinstance(value, dict):
                    self.report.error(
                        "HUMAN_CHECKPOINT_INVALID",
                        f"checkpoint {checkpoint_id} must be an object",
                        source,
                    )
                    continue
                item = dict(value)
                item.setdefault("id", checkpoint_id)
                checkpoints[normalize_token(checkpoint_id)] = item
        elif isinstance(raw_checkpoints, list):
            for index, value in enumerate(raw_checkpoints):
                owner = f"{source}:checkpoints[{index}]"
                if not isinstance(value, dict):
                    self.report.error("HUMAN_CHECKPOINT_INVALID", "checkpoint must be an object", owner)
                    continue
                checkpoint_id = normalize_token(value.get("id") or value.get("checkpoint"))
                if not checkpoint_id:
                    self.report.error("HUMAN_CHECKPOINT_ID_MISSING", "checkpoint id is missing", owner)
                    continue
                if checkpoint_id in checkpoints:
                    self.report.error(
                        "DUPLICATE_HUMAN_CHECKPOINT",
                        f"duplicate checkpoint: {checkpoint_id}",
                        owner,
                    )
                checkpoints[checkpoint_id] = dict(value)
        else:
            self.report.error(
                "HUMAN_CHECKPOINTS_MISSING",
                "checkpoints must be a list or object",
                source,
            )
            return

        for checkpoint_id in HUMAN_CHECKPOINTS:
            owner = f"{source}:checkpoint.{checkpoint_id}"
            item = checkpoints.get(checkpoint_id)
            if item is None:
                self.report.error(
                    "HUMAN_CHECKPOINT_MISSING",
                    f"required human checkpoint is missing: {checkpoint_id}",
                    source,
                )
                continue
            status = normalize_token(item.get("status"))
            if mode in HUMAN_REVIEW_MODES:
                if status != "approved":
                    self.report.error(
                        "HUMAN_APPROVAL_MISSING",
                        f"checkpoint {checkpoint_id} must be APPROVED",
                        owner,
                    )
                self._check_human_approval_fields(item, owner)
            else:
                if status == "approved":
                    self._check_human_approval_fields(item, owner)
                elif status == "waived_for_simulation":
                    self._check_simulation_waiver_fields(item, owner)
                else:
                    self.report.error(
                        "SIMULATION_WAIVER_INVALID",
                        f"checkpoint {checkpoint_id} must be APPROVED or WAIVED_FOR_SIMULATION",
                        owner,
                    )

        unknown = sorted(set(checkpoints) - set(HUMAN_CHECKPOINTS))
        if unknown:
            self.report.warn(
                "UNKNOWN_HUMAN_CHECKPOINT",
                f"unrecognized checkpoints are ignored: {', '.join(unknown)}",
                source,
            )

    def _check_human_approval_fields(self, item: Mapping[str, Any], owner: str) -> None:
        reviewer = item.get("reviewer")
        if isinstance(reviewer, dict):
            reviewer_name = reviewer.get("name") or reviewer.get("id")
            reviewer_type = reviewer.get("type") or reviewer.get("kind") or item.get("reviewer_type")
        else:
            reviewer_name = reviewer
            reviewer_type = item.get("reviewer_type")
        if not self._nonempty(reviewer_name):
            self.report.error("HUMAN_REVIEWER_MISSING", "human reviewer is missing", owner)
        if normalize_token(reviewer_type) != "human":
            self.report.error(
                "HUMAN_REVIEWER_TYPE_INVALID",
                "reviewer_type must explicitly be human",
                owner,
            )
        entered_by = normalize_token(
            item.get("entered_by") or item.get("authored_by") or item.get("created_by")
        )
        if item.get("agent_generated") is True or entered_by in {
            "agent",
            "ai",
            "codex",
            "assistant",
            "model",
            "llm",
        }:
            self.report.error(
                "AGENT_FILLED_HUMAN_REVIEW",
                "an Agent/AI must not author a human approval checkpoint",
                owner,
            )
        reviewed_at = item.get("reviewed_at")
        if self._parse_time(reviewed_at) is None:
            self.report.error("HUMAN_REVIEW_TIME_INVALID", "reviewed_at must be an ISO-8601 timestamp", owner)
        if not self._nonempty(item.get("scope")):
            self.report.error("HUMAN_REVIEW_SCOPE_MISSING", "review scope is missing", owner)
        if not self._nonempty(item.get("comments")):
            self.report.error("HUMAN_REVIEW_COMMENTS_MISSING", "review comments are missing", owner)
        evidence = self._collect_evidence(item, ("evidence", "evidence_paths"))
        self._validate_evidence(evidence, owner, required=True)

    def _check_simulation_waiver_fields(self, item: Mapping[str, Any], owner: str) -> None:
        if not self._nonempty(item.get("scope")):
            self.report.error("HUMAN_REVIEW_SCOPE_MISSING", "waiver scope is missing", owner)
        if not self._nonempty(item.get("comments")):
            self.report.error("HUMAN_REVIEW_COMMENTS_MISSING", "waiver rationale is missing", owner)
        evidence = self._collect_evidence(item, ("evidence", "evidence_paths"))
        self._validate_evidence(evidence, owner, required=True)

    def _nonempty(self, value: Any) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        if isinstance(value, Mapping):
            return bool(value) and any(self._nonempty(item) for item in value.values())
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return bool(value) and any(self._nonempty(item) for item in value)
        return value is not None

    def _check_stage_timestamp(
        self,
        stage: Mapping[str, Any],
        upstream: Sequence[tuple[str, Any]],
        evidence: Sequence[Any],
        owner: str,
    ) -> None:
        raw = stage.get("completed_at") or stage.get("finished_at")
        if not raw:
            return
        completed = self._parse_time(raw)
        if completed is None:
            self.report.error("INVALID_STAGE_TIMESTAMP", f"invalid completed_at: {raw!r}", owner)
            return
        paths: list[Path] = []
        for reference, _ in upstream:
            path = self._reference_path(reference)
            if path is not None:
                paths.append(path)
        for item in evidence:
            path_text = item.get("path") if isinstance(item, dict) else item
            path = self._resolve_project_path(path_text, owner, diagnose=False)
            if path is not None:
                paths.append(path)
        for path in paths:
            try:
                modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if modified > completed:
                self.report.error(
                    "STALE_STAGE",
                    f"stage completion predates {self._label(path)}",
                    owner,
                )

    def _check_root_hash(
        self,
        doc: Mapping[str, Any],
        field: str,
        source: str,
        strip_verification: bool = False,
        composite: tuple[str, ...] | None = None,
    ) -> None:
        expected = normalized_hash(doc.get("root_hash"))
        if expected is None:
            self.report.error("ROOT_HASH_MISSING", "root_hash must be a SHA-256 digest", source)
            return
        if composite is not None and all(key in doc for key in composite):
            # 3coding-visual/build_run_manifest.py hashes a composite material
            # {commands, files, runtime, sources}, not the `files` list alone.
            # Hashing only `files` here produced ROOT_HASH_MISMATCH for every
            # build_run_manifest-produced run_manifest (2026-08 exercise).
            material = {key: doc[key] for key in composite}
        else:
            material = doc.get(field)
            if strip_verification and isinstance(material, list):
                # Shared normalization with 1start-mathmodel/project_guard.py
                # (_input_hash_material): verification bookkeeping fields are
                # mutable append-only metadata and must not change the input
                # fingerprint. Hashing the full input records here produced
                # ROOT_HASH_MISMATCH against every project_guard-created
                # manifest (observed in the 2026-08 three-problem exercise).
                ignored = {
                    "verification",
                    "verification_actor",
                    "verification_note",
                    "verified_at",
                }
                material = [
                    {key: value for key, value in item.items() if key not in ignored}
                    for item in material
                ]
        actual = sha256_bytes(canonical_json_bytes(material))
        if actual != expected:
            self.report.error(
                "ROOT_HASH_MISMATCH",
                f"root_hash does not match canonical {field}",
                source,
            )

    def _check_reference(
        self,
        doc: Mapping[str, Any],
        fields: Sequence[str],
        expected: Any,
        source: str,
        alternate: tuple[str, Any] | None = None,
    ) -> None:
        value = first_present(doc, fields)
        if value is None and isinstance(doc.get("hashes"), dict):
            value = first_present(doc["hashes"], fields)
        if value is not None:
            if normalized_hash(value) != normalized_hash(expected):
                self.report.error(
                    "MANIFEST_REFERENCE_MISMATCH",
                    f"{fields[0]} does not match the current upstream manifest",
                    source,
                )
            return
        if alternate is not None:
            alt_name, alt_expected = alternate
            alt_value = doc.get(alt_name)
            if alt_value is None and isinstance(doc.get("hashes"), dict):
                alt_value = doc["hashes"].get(alt_name)
            if alt_value is not None:
                if normalized_hash(alt_value) != normalized_hash(alt_expected):
                    self.report.error(
                        "MANIFEST_REFERENCE_MISMATCH",
                        f"{alt_name} does not match the current upstream root",
                        source,
                    )
                return
        self.report.error(
            "MANIFEST_REFERENCE_MISSING",
            f"missing upstream reference: {fields[0]}",
            source,
        )

    def _verify_file_record(
        self,
        record: Mapping[str, Any],
        owner: str,
        *,
        resolved: Path | None = None,
        require_hash: bool,
    ) -> Path | None:
        path = resolved or self._resolve_project_path(record.get("path"), owner)
        if path is None:
            return None
        if not path.is_file():
            self.report.error("FILE_MISSING", f"file does not exist: {self._label(path)}", owner)
            return path
        try:
            size = path.stat().st_size
            actual_hash = sha256_file(path)
        except OSError as exc:
            self.report.error("FILE_UNREADABLE", f"cannot read file: {exc}", owner)
            return path
        expected_size = record.get("size")
        if expected_size is None:
            self.report.error("FILE_SIZE_MISSING", "file record has no size", owner)
        else:
            try:
                matches = int(expected_size) == size
            except (TypeError, ValueError):
                matches = False
            if not matches:
                self.report.error(
                    "FILE_SIZE_MISMATCH",
                    f"size mismatch for {self._label(path)}: expected {expected_size}, got {size}",
                    owner,
                )
        expected_hash = normalized_hash(record.get("sha256") or record.get("hash"))
        if require_hash and expected_hash is None:
            self.report.error("FILE_HASH_MISSING", "file record has no valid SHA-256", owner)
        elif expected_hash is not None and expected_hash != actual_hash:
            self.report.error(
                "FILE_HASH_MISMATCH",
                f"SHA-256 mismatch for {self._label(path)}",
                owner,
            )
        if expected_hash is not None:
            self._register_artifact(path, expected_hash, owner)
        return path

    def _register_artifact(self, path: Path, digest: str, owner: str) -> None:
        key = self._label(path)
        normalized = normalized_hash(digest)
        if normalized is None:
            return
        previous = self.artifact_hashes.get(key)
        if previous is not None and previous != normalized:
            self.report.error(
                "CONFLICTING_ARTIFACT_HASH",
                f"conflicting hashes declared for {key}",
                owner,
            )
        else:
            self.artifact_hashes[key] = normalized

    def _validate_evidence(self, items: Sequence[Any], owner: str, *, required: bool) -> int:
        if not items:
            if required:
                self.report.error("EVIDENCE_MISSING", "evidence path is required", owner)
            return 0
        valid = 0
        for index, item in enumerate(items):
            evidence_owner = f"{owner}:evidence[{index}]"
            if isinstance(item, str):
                record: dict[str, Any] = {"path": item}
            elif isinstance(item, dict):
                record = item
            else:
                self.report.error("INVALID_EVIDENCE", "evidence must be a path or object", evidence_owner)
                continue
            path = self._resolve_project_path(record.get("path"), evidence_owner)
            if path is None:
                continue
            if not path.is_file():
                self.report.error(
                    "EVIDENCE_PATH_MISSING",
                    f"evidence file does not exist: {self._label(path)}",
                    evidence_owner,
                )
                continue
            if path.stat().st_size == 0:
                self.report.error("EMPTY_EVIDENCE", f"evidence file is empty: {self._label(path)}", evidence_owner)
            actual = sha256_file(path)
            inline = normalized_hash(record.get("sha256") or record.get("hash"))
            registered = self.artifact_hashes.get(self._label(path))
            expected = inline or registered
            if expected is None:
                self.report.error(
                    "EVIDENCE_UNHASHED",
                    f"evidence is neither inline-hashed nor present in a manifest: {self._label(path)}",
                    evidence_owner,
                )
                continue
            if inline is not None and registered is not None and inline != registered:
                self.report.error(
                    "CONFLICTING_ARTIFACT_HASH",
                    f"inline and manifest hashes disagree for {self._label(path)}",
                    evidence_owner,
                )
            if actual != expected:
                self.report.error(
                    "EVIDENCE_HASH_MISMATCH",
                    f"evidence SHA-256 mismatch: {self._label(path)}",
                    evidence_owner,
                )
                continue
            valid += 1
        return valid

    def _collect_evidence(self, container: Mapping[str, Any], fields: Sequence[str]) -> list[Any]:
        result: list[Any] = []
        for field in fields:
            if field not in container:
                continue
            result.extend(self._flatten_evidence(container[field]))
        unique: list[Any] = []
        seen: set[str] = set()
        for item in result:
            marker = json.dumps(item, ensure_ascii=False, sort_keys=True) if isinstance(item, dict) else str(item)
            if marker not in seen:
                seen.add(marker)
                unique.append(item)
        return unique

    def _flatten_evidence(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            result: list[Any] = []
            for item in value:
                result.extend(self._flatten_evidence(item))
            return result
        if isinstance(value, dict):
            if "path" in value:
                return [value]
            result = []
            for item in value.values():
                result.extend(self._flatten_evidence(item))
            return result
        return [value]

    def _validation_checks(self, validation: Mapping[str, Any]) -> list[dict[str, Any]]:
        raw = validation.get("checks")
        if raw is None:
            raw = validation.get("validation_types")
        if raw is None and ("validation_type" in validation or "type" in validation):
            return [dict(validation)]
        if isinstance(raw, dict):
            checks: list[dict[str, Any]] = []
            for check_type, value in raw.items():
                if isinstance(value, dict):
                    item = dict(value)
                    item.setdefault("type", check_type)
                else:
                    item = {"type": check_type, "configuration": {"value": value}}
                checks.append(item)
            return checks
        checks = []
        for item in as_list(raw):
            if isinstance(item, str):
                checks.append({"type": item})
            elif isinstance(item, dict):
                checks.append(dict(item))
        return checks

    def _parse_profile(self, value: Any, owner: str) -> list[tuple[str, ...]]:
        if isinstance(value, dict):
            value = value.get("required_checks", value.get("required", value.get("groups")))
        groups: list[tuple[str, ...]] = []
        for raw_group in as_list(value):
            if isinstance(raw_group, str):
                names = [raw_group]
            elif isinstance(raw_group, list):
                names = raw_group
            elif isinstance(raw_group, dict):
                if "any_of" in raw_group:
                    names = as_list(raw_group["any_of"])
                elif "all_of" in raw_group:
                    for item in as_list(raw_group["all_of"]):
                        token = normalize_token(item)
                        if token:
                            groups.append((token,))
                    continue
                else:
                    names = []
            else:
                names = []
            normalized = tuple(token for token in (normalize_token(item) for item in names) if token)
            if normalized:
                groups.append(normalized)
            else:
                self.report.error("INVALID_VALIDATION_PROFILE", "empty validation profile group", owner)
        return groups

    def _quantity_records(self, container: Mapping[str, Any], *, contract: bool) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        fields: list[tuple[str, str | None]] = [
            ("quantities", None),
            ("quantity_snapshot", None),
        ]
        if contract:
            fields.extend(
                [
                    ("fixed_quantities", "fixed"),
                    ("decision_variables", "decision"),
                    ("state_variables", "state"),
                    ("control_parameters", "control"),
                    ("derived_quantities", "derived"),
                ]
            )
        else:
            fields.extend(
                [
                    ("fixed_quantities", "fixed"),
                    ("decision_variables", "decision"),
                    ("quantity_bindings", None),
                ]
            )
        for field, default_kind in fields:
            raw = container.get(field)
            if isinstance(raw, dict):
                iterable = []
                for item_id, item_value in raw.items():
                    if isinstance(item_value, dict):
                        item = dict(item_value)
                        item.setdefault("id", item_id)
                    else:
                        item = {"id": item_id, "value": item_value}
                    iterable.append(item)
            else:
                iterable = as_list(raw)
            for item in iterable:
                normalized = self._normalize_quantity(item, default_kind, field)
                if normalized is not None:
                    result.append(normalized)
        return result

    def _normalize_quantity(
        self,
        raw: Any,
        default_kind: str | None,
        owner: str | None,
    ) -> dict[str, Any] | None:
        if isinstance(raw, str):
            raw = {"id": raw}
        if not isinstance(raw, dict):
            return None
        quantity_id = str(raw.get("id") or raw.get("name") or "").strip()
        if not quantity_id:
            return None
        kind = normalize_token(
            raw.get("kind") or raw.get("classification") or raw.get("role") or default_kind
        )
        kind = QUANTITY_KIND_ALIASES.get(kind, kind)
        domain = first_present(raw, ("domain", "bounds", "allowed_values"))
        result = dict(raw)
        result.update({"id": quantity_id, "kind": kind, "domain": self._normalize_domain(domain)})
        return result

    def _check_quantity_binding(self, record: Mapping[str, Any], source: str) -> None:
        quantity_id = str(record.get("id") or "")
        kind = str(record.get("kind") or "")
        if quantity_id not in self.quantities:
            if kind in {"fixed", "decision"}:
                self.report.error(
                    "UNDECLARED_QUANTITY",
                    f"downstream declares an unknown {kind} quantity: {quantity_id}",
                    source,
                )
            return
        contract = self.quantities[quantity_id]
        contract_kind = contract.get("kind")
        if kind and kind != contract_kind:
            code = "FIXED_QUANTITY_DRIFT" if contract_kind == "fixed" else "DECISION_QUANTITY_DRIFT"
            self.report.error(
                code,
                f"quantity {quantity_id} changed kind from {contract_kind} to {kind}",
                source,
            )
        if contract_kind == "fixed":
            if "value" in record and "value" in contract and not self._json_equal(record["value"], contract["value"]):
                self.report.error(
                    "FIXED_QUANTITY_DRIFT",
                    f"fixed quantity value changed: {quantity_id}",
                    source,
                )
        if contract_kind == "decision":
            incoming_domain = record.get("domain")
            contract_domain = contract.get("domain")
            if incoming_domain is not None and contract_domain is not None and not self._json_equal(incoming_domain, contract_domain):
                self.report.error(
                    "DECISION_DOMAIN_DRIFT",
                    f"decision domain changed: {quantity_id}",
                    source,
                )
            if "value" in record and isinstance(contract_domain, dict):
                value = record.get("value")
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    lower = contract_domain.get("min")
                    upper = contract_domain.get("max")
                    lower_ok = not isinstance(lower, (int, float)) or (
                        value >= lower if contract_domain.get("min_inclusive", True) else value > lower
                    )
                    upper_ok = not isinstance(upper, (int, float)) or (
                        value <= upper if contract_domain.get("max_inclusive", True) else value < upper
                    )
                    if not (lower_ok and upper_ok):
                        self.report.error(
                            "DECISION_VALUE_OUTSIDE_DOMAIN",
                            f"decision value lies outside the frozen domain: {quantity_id}",
                            source,
                        )
        if "unit" in record and "unit" in contract:
            if str(record["unit"]).strip() != str(contract["unit"]).strip():
                code = "FIXED_QUANTITY_DRIFT" if contract_kind == "fixed" else "DECISION_QUANTITY_DRIFT"
                self.report.error(code, f"quantity unit changed: {quantity_id}", source)

    def _normalize_domain(self, value: Any) -> Any:
        if isinstance(value, list) and len(value) == 2:
            return {"min": value[0], "max": value[1], "min_inclusive": True, "max_inclusive": True}
        if isinstance(value, dict):
            result = dict(value)
            aliases = {"lower": "min", "lower_bound": "min", "upper": "max", "upper_bound": "max"}
            for old, new in aliases.items():
                if old in result and new not in result:
                    result[new] = result.pop(old)
            if "min" in result:
                result.setdefault("min_inclusive", True)
            if "max" in result:
                result.setdefault("max_inclusive", True)
            return result
        return value

    def _upstream_pairs(self, raw: Any, owner: str) -> list[tuple[str, Any]]:
        if isinstance(raw, dict):
            pairs = []
            for key, value in raw.items():
                if isinstance(value, dict):
                    reference = str(value.get("path") or value.get("name") or key)
                    digest = value.get("sha256") or value.get("hash")
                else:
                    reference = str(key)
                    digest = value
                if normalized_hash(digest) is None:
                    self.report.error("INVALID_UPSTREAM_HASH", f"invalid upstream hash for {reference}", owner)
                else:
                    pairs.append((reference, digest))
            return pairs
        pairs = []
        for item in as_list(raw):
            if not isinstance(item, dict):
                self.report.error("INVALID_UPSTREAM_HASH", "upstream entry must be an object", owner)
                continue
            reference = str(item.get("path") or item.get("name") or item.get("manifest") or "")
            digest = item.get("sha256") or item.get("hash")
            if not reference or normalized_hash(digest) is None:
                self.report.error("INVALID_UPSTREAM_HASH", "invalid upstream hash entry", owner)
            else:
                pairs.append((reference, digest))
        return pairs

    def _current_reference_hash(self, reference: str) -> tuple[str | None, str]:
        token = normalize_token(reference)
        aliases = {
            "problem_manifest": "problem_manifest",
            "problem_manifest_json": "problem_manifest",
            "problem_manifest_sha256": "problem_manifest",
            "problem_contract": "problem_contract",
            "problem_contract_json": "problem_contract",
            "problem_contract_sha256": "problem_contract",
            "contract_sha256": "problem_contract",
            "claim_ledger": "claim_ledger",
            "claim_ledger_json": "claim_ledger",
            "claim_ledger_sha256": "claim_ledger",
            "validation_manifest": "validation_manifest",
            "validation_manifest_json": "validation_manifest",
            "validation_manifest_sha256": "validation_manifest",
            "run_manifest": "run_manifest",
            "run_manifest_json": "run_manifest",
            "run_manifest_sha256": "run_manifest",
            "stage_gates": "stage_gates",
            "stage_gates_json": "stage_gates",
            "current_versions": "current_versions",
            "current_versions_json": "current_versions",
            "current_versions_sha256": "current_versions",
        }
        if token in {"problem_root_hash", "problem_root"}:
            return self.docs["problem_manifest"].get("root_hash"), "problem_manifest"
        if token in {"run_root_hash", "run_root"}:
            return self.docs["run_manifest"].get("root_hash"), "run_manifest"
        logical = aliases.get(token)
        if logical:
            return self.raw_hashes.get(logical), logical
        path = self._reference_path(reference)
        if path is not None and path.is_file():
            for name, known in self.paths.items():
                if path == known:
                    return self.raw_hashes[name], name
            return sha256_file(path), self._label(path)
        return None, reference

    def _reference_path(self, reference: str) -> Path | None:
        token = normalize_token(reference)
        for logical, display in DISPLAY_NAMES.items():
            if token in {normalize_token(logical), normalize_token(display), normalize_token(display + "_sha256")}:
                return self.paths.get(logical)
        return self._resolve_project_path(reference, reference, diagnose=False)

    def _resolve_project_path(
        self,
        raw: Any,
        owner: str,
        *,
        diagnose: bool = True,
    ) -> Path | None:
        if not isinstance(raw, str) or not raw.strip():
            if diagnose:
                self.report.error("PATH_MISSING", "path is missing", owner)
            return None
        candidate = Path(raw.strip())
        if not candidate.is_absolute():
            candidate = self.root / candidate
        try:
            resolved = candidate.resolve()
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            if diagnose:
                self.report.error("PATH_OUTSIDE_ROOT", f"path escapes project root: {raw}", owner)
            return None
        return resolved

    def _resolve_member_path(self, directory: Path, raw: Any, owner: str) -> Path | None:
        if not isinstance(raw, str) or not raw.strip():
            self.report.error("PATH_MISSING", "path is missing", owner)
            return None
        direct = self._resolve_project_path(raw, owner, diagnose=False)
        nested = (directory / raw).resolve()
        candidates = [path for path in (direct, nested) if path is not None]
        for path in candidates:
            try:
                path.relative_to(directory)
            except ValueError:
                continue
            if path.exists():
                return path
        self.report.error("PATH_OUTSIDE_ROOT", f"directory member is outside declared directory: {raw}", owner)
        return None

    def _label(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except (OSError, ValueError):
            return str(path)

    def _source(self, logical: str) -> str:
        return self._label(self.paths[logical])

    def _string_list(self, value: Any) -> list[str]:
        result = []
        for item in as_list(value):
            if isinstance(item, dict):
                item = item.get("id") or item.get("ref") or item.get("req_id")
            text = str(item or "").strip()
            if text:
                result.append(text)
        return result

    def _json_equal(self, left: Any, right: Any) -> bool:
        return canonical_json_bytes(left) == canonical_json_bytes(right) or left == right

    def _parse_time(self, raw: Any) -> datetime | None:
        if not isinstance(raw, str):
            return None
        value = raw.strip()
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


def run_check(
    root: Path | str,
    manifest_overrides: Mapping[str, Path | str] | None = None,
) -> IntegrityReport:
    return IntegrityChecker(Path(root), manifest_overrides).run()


def schema_summary() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "manifests": {
            "PROBLEM_MANIFEST.json": {
                "required": ["schema_version", "manifest_type=problem", "inputs", "root_hash"],
                "input": ["role", "path", "type", "size", "sha256"],
            },
            "PROBLEM_CONTRACT.json": {
                "required": [
                    "schema_version",
                    "status=FROZEN",
                    "problem_manifest_sha256 or problem_root_hash",
                    "requirements",
                    "quantities",
                ],
                "quantity_kinds": ["fixed", "decision", "state", "control", "derived"],
            },
            "claim_ledger.json": {
                "required": ["schema_version", "problem_contract_sha256", "claims"],
                "claim": [
                    "id",
                    "contract_refs",
                    "claim_type",
                    "status",
                    "evidence",
                    "validation_ids (optional inverse link)",
                ],
            },
            "validation_manifest.json": {
                "required": [
                    "schema_version",
                    "problem_contract_sha256",
                    "claim_ledger_sha256",
                    "validations",
                ],
                "validation": ["id", "claim_ids", "status", "checks"],
                "check": ["type", "status", "configuration", "evidence"],
                "extension": "claim_type_profiles adds requirements; it cannot remove built-in minima",
            },
            "run_manifest.json": {
                "required": ["schema_version", "manifest_type=run", "files", "root_hash"],
                "file": ["role", "path", "size", "sha256"],
            },
            "STAGE_GATES.json": {
                "required": [
                    "schema_version",
                    "problem_manifest_sha256 or problem_root_hash",
                    "stages",
                ],
                "completed_stage": [
                    "id",
                    "status",
                    "upstream_hashes",
                    "evidence",
                    "required_artifacts (when applicable)",
                ],
            },
            "reports/HUMAN_REVIEW.json": {
                "required": [
                    "schema_version",
                    "mode",
                    "problem_manifest_sha256 or problem_root_hash",
                    "checkpoints",
                ],
                "checkpoint_ids": list(HUMAN_CHECKPOINTS),
                "approved_checkpoint": [
                    "status=APPROVED",
                    "reviewer",
                    "reviewer_type=human",
                    "reviewed_at",
                    "scope",
                    "evidence",
                    "comments",
                ],
                "authorship": "This file must be entered by a human or controlled review UI; Agents must not fill approval fields.",
                "authorship_object": {
                    "human_mode": {"type": "human or controlled-human-review-ui", "agent_generated": False},
                    "simulation_mode": {"type": "simulation-orchestrator", "agent_generated": True},
                },
                "simulation": "WAIVED_FOR_SIMULATION is allowed only in autonomous-simulation mode and caps the result at UNVERIFIED.",
            },
            "reports/CURRENT_VERSIONS.json": {
                "required": ["schema_version", "problem_root_hash", "selections", "selection_hash"],
                "history": "reports/VERSION_DECISIONS.jsonl is append-only and hash chained",
            },
        },
        "built_in_claim_types": sorted(BUILTIN_PROFILES),
        "canonical_hash": "sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8'))",
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate cross-stage modeling integrity without knowing contest answers."
    )
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Project root")
    parser.add_argument("--problem-manifest", type=Path)
    parser.add_argument("--problem-contract", type=Path)
    parser.add_argument("--claim-ledger", type=Path)
    parser.add_argument("--validation-manifest", type=Path)
    parser.add_argument("--run-manifest", type=Path)
    parser.add_argument("--stage-gates", type=Path)
    parser.add_argument("--current-versions", type=Path)
    parser.add_argument("--json", action="store_true", help="Emit a JSON report")
    parser.add_argument("--print-schema", action="store_true", help="Print the supported schema and exit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.print_schema:
        print(json.dumps(schema_summary(), ensure_ascii=False, indent=2))
        return 0
    overrides = {
        name: value
        for name, value in {
            "problem_manifest": args.problem_manifest,
            "problem_contract": args.problem_contract,
            "claim_ledger": args.claim_ledger,
            "validation_manifest": args.validation_manifest,
            "run_manifest": args.run_manifest,
            "stage_gates": args.stage_gates,
            "current_versions": args.current_versions,
        }.items()
        if value is not None
    }
    report = run_check(args.root, overrides)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for diagnostic in report.diagnostics:
            location = f" [{diagnostic.source}]" if diagnostic.source else ""
            print(f"{diagnostic.severity}: {diagnostic.code}{location}: {diagnostic.message}")
        print(
            f"{report.status}: "
            f"integrity gate; errors={len(report.errors)} warnings={len(report.warnings)}"
        )
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
