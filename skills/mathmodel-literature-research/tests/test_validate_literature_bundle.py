from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_literature_bundle.py"
SPEC = importlib.util.spec_from_file_location("validate_literature_bundle", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CitationParserTests(unittest.TestCase):
    def test_latex_common_citation_commands_are_all_parsed(self) -> None:
        text = r"\cite{S001}\citep[see][p. 2]{S002,S003}\textcite{S004}"
        citations, bibliography = MODULE.extract_paper_citations(text)
        self.assertEqual(citations, {"S001", "S002", "S003", "S004"})
        self.assertEqual(bibliography, set())

    def test_typst_multikey_and_manual_super_are_all_parsed(self) -> None:
        text = '#cite(<S001>, <S002>, form: "prose") and #super("[3,4]")'
        citations, _ = MODULE.extract_paper_citations(text)
        self.assertEqual(citations, {"S001", "S002", "3", "4"})

    def test_search_result_url_is_not_stable_evidence(self) -> None:
        self.assertTrue(MODULE.is_search_result_url("https://www.google.com/search?q=test"))
        self.assertFalse(MODULE.is_search_result_url("https://doi.org/10.1000/example"))

    def test_targeted_mode_does_not_require_false_saturation_claim(self) -> None:
        report = """Gate 状态: PASS
检索模式: targeted
## 问题指纹
## 双语关键词矩阵
## 证据角色覆盖
## 理论到本题的变量映射
## 候选模型路线比较
路线 A：解析基线。
路线 B：数值基线。
## 检索饱和度
饱和判定: 未达到
## 风险、未决证据与补证动作
时间盒到期后将未决外部参数作为敏感性范围。
"""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "report.md"
            path.write_text(report, encoding="utf-8")
            findings = MODULE.Findings()
            _, gate, mode = MODULE.validate_report(path, findings)
        self.assertEqual((gate, mode), ("PASS", "targeted"))
        self.assertFalse(findings.errors, findings.errors)


if __name__ == "__main__":
    unittest.main()
