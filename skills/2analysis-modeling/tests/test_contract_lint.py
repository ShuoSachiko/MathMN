"""Synthetic tests for skills/2analysis-modeling/scripts/contract_lint.py.

Fixtures are fully synthetic (no real contest statements, answers, or target
values) and cover both accept and reject cases per the repository's test
policy. Uses a workspace-local temp dir with tolerant cleanup so it also runs
inside restricted sandboxes.
"""

import importlib.util
import json
import os
import shutil
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "contract_lint.py"


def load_module():
    spec = importlib.util.spec_from_file_location("contract_lint", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


lint = load_module()


def build_contract(**overrides):
    contract = {
        "schema_version": 1,
        "status": "FROZEN",
        "problem_manifest_sha256": "f" * 64,
        "requirements": [
            {
                "id": "REQ-1",
                "source_ref": "problem.pdf 第1页",
                "action": "计算最短路径",
                "outputs": [{"value": "path_length", "unit": "km", "precision": 2}],
                "acceptance": "数值与穷举核对一致",
                "downstream": "claim C1 / 论文章节 5_problem1",
            }
        ],
        "quantities": [
            {
                "id": "Q1",
                "kind": "fixed",
                "unit": "km",
                "source_req": "REQ-1",
                "definition": "两站点间距离",
            }
        ],
    }
    contract.update(overrides)
    return contract


class ContractLintTests(unittest.TestCase):
    def setUp(self):
        # Plain cwd-local dir: restricted sandboxes deny writes inside
        # tempfile-created directories, while the workspace root is writable.
        self._tmp = Path.cwd() / f".tmp-contract-lint-{os.getpid()}-{id(self)}"
        self.root = self._tmp / "project"
        (self.root / "reports").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write(self, contract, md_text="REQ-1"):
        (self.root / "reports" / "PROBLEM_CONTRACT.json").write_text(
            json.dumps(contract, ensure_ascii=False), encoding="utf-8"
        )
        if md_text is not None:
            (self.root / "reports" / "PROBLEM_CONTRACT.md").write_text(md_text, encoding="utf-8")

    def test_valid_contract_passes(self):
        self.write(build_contract())
        errors, warnings = lint.check(self.root)
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_missing_contract_is_error(self):
        errors, _ = lint.check(self.root)
        self.assertTrue(any("missing" in e for e in errors))

    def test_not_frozen_is_rejected(self):
        self.write(build_contract(status="DRAFT"))
        errors, _ = lint.check(self.root)
        self.assertTrue(any("FROZEN" in e for e in errors))

    def test_missing_acceptance_is_rejected(self):
        contract = build_contract()
        del contract["requirements"][0]["acceptance"]
        self.write(contract)
        errors, _ = lint.check(self.root)
        self.assertTrue(any("acceptance" in e for e in errors))

    def test_placeholder_token_is_rejected(self):
        self.write(build_contract(**{"requirements": [{
            "id": "REQ-1",
            "action": "TODO 待补充",
            "outputs": [{"value": "x", "unit": "m"}],
            "acceptance": "可证伪",
        }], "quantities": [
            {"id": "Q1", "kind": "decision", "unit": "m", "source_req": "REQ-1"}
        ]}))
        errors, _ = lint.check(self.root)
        self.assertTrue(any("placeholder" in e for e in errors))

    def test_bad_quantity_kind_is_rejected(self):
        self.write(build_contract())
        contract = json.loads((self.root / "reports" / "PROBLEM_CONTRACT.json").read_text(encoding="utf-8"))
        contract["quantities"][0]["kind"] = "magic"
        self.write(contract)
        errors, _ = lint.check(self.root)
        self.assertTrue(any("kind" in e for e in errors))

    def test_reqid_missing_from_md_is_rejected(self):
        self.write(build_contract(), md_text="无关内容")
        errors, _ = lint.check(self.root)
        self.assertTrue(any("REQ-1" in e for e in errors))

    def test_missing_manifest_hash_is_rejected(self):
        contract = build_contract()
        contract.pop("problem_manifest_sha256")
        self.write(contract)
        errors, _ = lint.check(self.root)
        self.assertTrue(any("manifest" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
