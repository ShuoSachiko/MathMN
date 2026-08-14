from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
import uuid
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_submission_bundle.py"
# 仓库根 = tests -> 6verity -> skills -> 根。
REPO_ROOT = Path(__file__).resolve().parents[3]
# WHY: DSH 沙箱会把 tempfile 创建的目录（os.mkdir(..., 0o700)）锁成只读 ACL，
# 无法在其中写文件或建子目录，甚至无法 chmod/删除。因此夹具改在工作区内的
# 唯一路径上用 pathlib（默认 0o777）创建，tearDown 用 rmtree(ignore_errors=True)
# 兜底清理——这仍是"tempfile 风格"的隔离夹具，只是绕开沙箱对 0o700 目录的锁。
FIXTURE_BASE = REPO_ROOT / ".tmp-b"


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def default_config(**overrides: object) -> dict:
    config = {
        "required_files": [],
        "paper_pdf": "paper/main.pdf",
        "paper_min_bytes": 10240,
        "support_material_zip": "submission/support_material.zip",
        "require_code_entries": True,
        "code_entry_prefix": "code/",
        "forbid_pdf_in_zip": True,
        "commitment_file": None,
        "ai_usage_log": "reports/AI_USAGE_LOG.jsonl",
        "paper_naming_hint": "建议按当届官方模板命名",
    }
    config.update(overrides)
    return config


def write_default_config(root: Path, **overrides: object) -> Path:
    path = root / "reports" / "submission_bundle_config.json"
    write_json(path, default_config(**overrides))
    return path


@contextmanager
def fixture_root() -> Iterator[Path]:
    root = FIXTURE_BASE / f"sbundle-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        # 忽略清理期权限错误（环境噪音），不影响断言结果。
        shutil.rmtree(root, ignore_errors=True)


def make_paper(root: Path) -> Path:
    path = root / "paper" / "main.pdf"
    path.parent.mkdir(parents=True, exist_ok=True)
    # %PDF- 魔数 + 填充，确保通过大小下限与魔数检查。
    path.write_bytes(b"%PDF-1.4\n" + b"0" * 20000)
    return path


def make_ai_log(root: Path, *, lines: list[object] | None = None) -> Path:
    path = root / "reports" / "AI_USAGE_LOG.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    if lines is None:
        lines = [
            {"time": "2026-01-01T00:00:00Z", "tool": "web_search", "purpose": "检索文献"},
            {"time": "2026-01-01T01:00:00Z", "tool": "codex", "purpose": "求解模型"},
        ]
    path.write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines) + "\n",
        encoding="utf-8",
    )
    return path


def make_zip(root: Path, entries: dict[str, bytes] | None = None) -> Path:
    path = root / "submission" / "support_material.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    if entries is None:
        entries = {"code/main.py": b"print('hello')\n"}
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


class SubmissionBundleValidatorTests(unittest.TestCase):
    def run_validator(
        self,
        root: Path,
        config: Path | None = None,
        *,
        use_json: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, "-B", str(SCRIPT), str(root)]
        if config is not None:
            cmd += ["--config", str(config)]
        if use_json:
            cmd += ["--json"]
        return subprocess.run(cmd, text=True, capture_output=True, check=False)

    def test_complete_bundle_passes_and_json_has_md5(self) -> None:
        with fixture_root() as root:
            write_default_config(root)
            make_paper(root)
            make_zip(root)
            make_ai_log(root)
            result = self.run_validator(root)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "PASS")
            self.assertRegex(report["paper_md5"], r"^[0-9a-f]{32}$")
            written = json.loads(
                (root / "reports" / "submission_bundle_validation.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(written["status"], "PASS")
            self.assertEqual(written["paper_md5"], report["paper_md5"])
            text_report = (root / "reports" / "submission_bundle_validation.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("PASS", text_report)
            self.assertIn(report["paper_md5"], text_report)

    def test_missing_paper_pdf_fails(self) -> None:
        with fixture_root() as root:
            write_default_config(root)
            make_zip(root)
            make_ai_log(root)
            result = self.run_validator(root)
            self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "FAIL")
            paper = next(c for c in report["checks"] if c["check"] == "paper_pdf")
            self.assertEqual(paper["status"], "FAIL")
            self.assertTrue(any(item["check"] == "paper_pdf" for item in report["failures"]))

    def test_zip_with_pdf_entry_fails(self) -> None:
        with fixture_root() as root:
            write_default_config(root)
            make_paper(root)
            make_ai_log(root)
            make_zip(root, {"code/main.py": b"x", "paper/main.pdf": b"%PDF-1.4\nhidden"})
            result = self.run_validator(root)
            self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            zip_check = next(c for c in report["checks"] if c["check"] == "support_material_zip")
            self.assertEqual(zip_check["status"], "FAIL")
            self.assertIn(".pdf", zip_check["detail"])

    def test_ai_log_bad_line_fails(self) -> None:
        with fixture_root() as root:
            write_default_config(root)
            make_paper(root)
            make_zip(root)
            make_ai_log(
                root,
                lines=[
                    {"time": "t", "tool": "web_search", "purpose": "x"},
                    "{not valid json",
                ],
            )
            result = self.run_validator(root)
            self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            ai_check = next(c for c in report["checks"] if c["check"] == "ai_usage_log")
            self.assertEqual(ai_check["status"], "FAIL")

    def test_null_items_are_na_not_fail(self) -> None:
        with fixture_root() as root:
            write_default_config(root, support_material_zip=None, commitment_file=None)
            make_paper(root)
            make_ai_log(root)
            result = self.run_validator(root)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "PASS")
            statuses = {c["check"]: c["status"] for c in report["checks"]}
            self.assertEqual(statuses["support_material_zip"], "N/A")
            self.assertEqual(statuses["commitment_file"], "N/A")

    def test_configured_commitment_missing_fails(self) -> None:
        with fixture_root() as root:
            write_default_config(root, commitment_file="submission/commitment.pdf")
            make_paper(root)
            make_zip(root)
            make_ai_log(root)
            result = self.run_validator(root)
            self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            commitment = next(c for c in report["checks"] if c["check"] == "commitment_file")
            self.assertEqual(commitment["status"], "FAIL")

    def test_invalid_zip_fails(self) -> None:
        with fixture_root() as root:
            write_default_config(root)
            make_paper(root)
            make_ai_log(root)
            zip_path = root / "submission" / "support_material.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            zip_path.write_text("not a zip archive", encoding="utf-8")
            result = self.run_validator(root)
            self.assertEqual(result.returncode, 1, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            zip_check = next(c for c in report["checks"] if c["check"] == "support_material_zip")
            self.assertEqual(zip_check["status"], "FAIL")

    def test_no_applicable_checks_unverified_exit_2(self) -> None:
        with fixture_root() as root:
            write_default_config(
                root,
                paper_pdf=None,
                support_material_zip=None,
                commitment_file=None,
                ai_usage_log=None,
            )
            result = self.run_validator(root)
            self.assertEqual(result.returncode, 2, result.stderr or result.stdout)
            report = json.loads(result.stdout)
            self.assertEqual(report["status"], "UNVERIFIED")

    def test_explicit_config_flag_is_honoured(self) -> None:
        with fixture_root() as root:
            config = root / "custom" / "bundle.json"
            write_json(config, default_config())
            make_paper(root)
            make_zip(root)
            make_ai_log(root)
            result = self.run_validator(root, config=config)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertEqual(json.loads(result.stdout)["status"], "PASS")


if __name__ == "__main__":
    unittest.main()
