from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "validate_workbook.py"


def make_xlsx(path: Path, value: float = 3.5) -> None:
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="Results" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    relationships = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    worksheet = f"""<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>score</t></is></c><c r="B1"><v>{value}</v></c></row></sheetData>
</worksheet>"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", relationships)
        archive.writestr("xl/worksheets/sheet1.xml", worksheet)


class WorkbookValidatorTests(unittest.TestCase):
    def run_validator(self, workbook: Path, schema: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPT), "--workbook", str(workbook), "--schema", str(schema)],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_accepts_exact_schema_and_rejects_wrong_cell(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "submission.xlsx"
            schema_path = root / "schema.json"
            make_xlsx(workbook)
            schema = {
                "schema_version": 1,
                "allow_extra_sheets": False,
                "sheet_order": "exact",
                "sheets": [
                    {
                        "name": "Results",
                        "state": "visible",
                        "cells": [
                            {"ref": "A1", "type": "string", "value": "score"},
                            {"ref": "B1", "type": "number", "value": 3.5, "abs_tol": 1e-12},
                            {"ref": "C1", "empty": True},
                        ],
                    }
                ],
            }
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            accepted = self.run_validator(workbook, schema_path)
            self.assertEqual(accepted.returncode, 0, accepted.stderr or accepted.stdout)
            self.assertEqual(json.loads(accepted.stdout)["status"], "PASS")

            schema["sheets"][0]["cells"][1]["value"] = 9.0
            schema_path.write_text(json.dumps(schema), encoding="utf-8")
            rejected = self.run_validator(workbook, schema_path)
            self.assertEqual(rejected.returncode, 1, rejected.stderr or rejected.stdout)
            report = json.loads(rejected.stdout)
            self.assertEqual(report["status"], "FAIL")
            self.assertTrue(any(item["code"] == "CELL_VALUE" for item in report["errors"]))

    def test_rejects_hidden_extra_sheet_policy_by_schema(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workbook = root / "submission.xlsx"
            schema_path = root / "schema.json"
            make_xlsx(workbook)
            schema_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "allow_extra_sheets": False,
                        "sheet_order": "exact",
                        "sheets": [{"name": "Expected", "cells": []}],
                    }
                ),
                encoding="utf-8",
            )
            result = self.run_validator(workbook, schema_path)
            self.assertEqual(result.returncode, 1)
            codes = {item["code"] for item in json.loads(result.stdout)["errors"]}
            self.assertIn("SHEET_ORDER", codes)


if __name__ == "__main__":
    unittest.main()
