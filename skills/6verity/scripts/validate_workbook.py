#!/usr/bin/env python3
"""Validate an XLSX submission against an explicit, problem-specific schema.

The implementation intentionally uses only the Python standard library.  It
reads the XLSX ZIP/XML package directly and never asks Excel, LibreOffice, or
``openpyxl`` to recalculate formulas.  Consequently, constraints on a formula
cell's value apply to the cached value stored in the workbook.

Schema version 1 (unknown keys are rejected so misspellings cannot silently
disable a check)::

    {
      "schema_version": 1,
      "allow_extra_sheets": false,
      "sheet_order": "exact",
      "sheets": [{
        "name": "Results",
        "state": "visible",
        "merged_cells": ["A1:B1"],
        "allow_extra_merged_cells": true,
        "cells": [{
          "ref": "B2", "required": true, "type": "number",
          "value": 3.5, "abs_tol": 1e-8, "rel_tol": 0,
          "finite": true, "minimum": 0, "maximum": 10,
          "formula": false, "number_format": "0.000"
        }, {"ref": "C2", "empty": true}]
      }]
    }

``sheet_order`` is ``exact``, ``relative``, or ``ignore``.  Exact order is
incompatible with extra sheets.  A formula requirement may be ``true``,
``false``, or the exact formula text (an optional leading ``=`` is ignored).

Exit status is 0 for PASS, 1 for FAIL, and 2 for invocation/I/O errors.  The
machine-readable report is written to stdout and optionally to ``--output``.
Workbook rendering, clipping, print areas, and other visual properties remain
explicitly UNVERIFIED.
"""

from __future__ import annotations

import argparse
import json
import math
import posixpath
import re
import sys
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET


SCHEMA_VERSION = 1
MAX_XML_BYTES = 64 * 1024 * 1024
MAX_PACKAGE_BYTES = 512 * 1024 * 1024
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
DOC_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
CELL_RE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]*)$")
RANGE_RE = re.compile(r"^[A-Z]{1,3}[1-9][0-9]*:[A-Z]{1,3}[1-9][0-9]*$")

TOP_KEYS = {"schema_version", "allow_extra_sheets", "sheet_order", "sheets"}
SHEET_KEYS = {
    "name",
    "state",
    "merged_cells",
    "allow_extra_merged_cells",
    "cells",
}
CELL_KEYS = {
    "ref",
    "required",
    "empty",
    "type",
    "value",
    "abs_tol",
    "rel_tol",
    "finite",
    "minimum",
    "maximum",
    "min_inclusive",
    "max_inclusive",
    "formula",
    "number_format",
    "number_format_regex",
}
CELL_TYPES = {"number", "string", "boolean", "error", "date", "blank"}
SHEET_STATES = {"visible", "hidden", "veryHidden"}

# Common built-in formats.  Custom formats are read from styles.xml.  Unknown
# built-ins are still represented by their stable ``builtin:<id>`` label.
BUILTIN_FORMATS = {
    0: "General",
    1: "0",
    2: "0.00",
    3: "#,##0",
    4: "#,##0.00",
    9: "0%",
    10: "0.00%",
    11: "0.00E+00",
    12: "# ?/?",
    13: "# ??/??",
    14: "mm-dd-yy",
    15: "d-mmm-yy",
    16: "d-mmm",
    17: "mmm-yy",
    18: "h:mm AM/PM",
    19: "h:mm:ss AM/PM",
    20: "h:mm",
    21: "h:mm:ss",
    22: "m/d/yy h:mm",
    37: "#,##0 ;(#,##0)",
    38: "#,##0 ;[Red](#,##0)",
    39: "#,##0.00;(#,##0.00)",
    40: "#,##0.00;[Red](#,##0.00)",
    49: "@",
}


@dataclass(frozen=True)
class Issue:
    code: str
    location: str
    message: str


@dataclass
class CellValue:
    ref: str
    value: Any
    value_type: str
    formula: str | None
    number_format: str

    @property
    def empty(self) -> bool:
        return self.formula is None and (self.value is None or self.value == "")


@dataclass
class SheetData:
    name: str
    state: str
    cells: dict[str, CellValue]
    merged_cells: set[str]


@dataclass
class WorkbookData:
    sheets: list[SheetData]


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON number {value!r} is not allowed")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, parse_constant=_reject_json_constant)


def _unknown_keys(value: Mapping[str, Any], allowed: set[str]) -> list[str]:
    return sorted(str(key) for key in value if key not in allowed)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _column_number(label: str) -> int:
    result = 0
    for character in label:
        result = result * 26 + ord(character) - ord("A") + 1
    return result


def _normalise_cell_ref(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper().replace("$", "")
    match = CELL_RE.fullmatch(candidate)
    if match is None:
        return None
    if _column_number(match.group(1)) > 16384 or int(match.group(2)) > 1_048_576:
        return None
    return candidate


def _normalise_range(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().upper().replace("$", "")
    if not RANGE_RE.fullmatch(candidate):
        return None
    left, right = candidate.split(":", 1)
    if _normalise_cell_ref(left) is None or _normalise_cell_ref(right) is None:
        return None
    return candidate


def _validate_schema(schema: Any) -> list[Issue]:
    errors: list[Issue] = []
    if not isinstance(schema, Mapping):
        return [Issue("SCHEMA_TYPE", "$", "schema root must be a JSON object")]
    for key in _unknown_keys(schema, TOP_KEYS):
        errors.append(Issue("SCHEMA_UNKNOWN_KEY", "$", f"unknown key {key!r}"))
    if schema.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            Issue(
                "SCHEMA_VERSION",
                "$.schema_version",
                f"expected integer {SCHEMA_VERSION}",
            )
        )
    allow_extra = schema.get("allow_extra_sheets", False)
    if not isinstance(allow_extra, bool):
        errors.append(
            Issue("SCHEMA_TYPE", "$.allow_extra_sheets", "must be boolean")
        )
    order = schema.get("sheet_order", "exact")
    if order not in {"exact", "relative", "ignore"}:
        errors.append(
            Issue(
                "SCHEMA_ENUM",
                "$.sheet_order",
                "must be exact, relative, or ignore",
            )
        )
    if order == "exact" and allow_extra is True:
        errors.append(
            Issue(
                "SCHEMA_CONFLICT",
                "$",
                "sheet_order='exact' cannot be combined with allow_extra_sheets=true",
            )
        )
    sheets = schema.get("sheets")
    if not isinstance(sheets, list) or not sheets:
        errors.append(Issue("SCHEMA_TYPE", "$.sheets", "must be a non-empty array"))
        return errors
    names: set[str] = set()
    for sheet_index, sheet in enumerate(sheets):
        location = f"$.sheets[{sheet_index}]"
        if not isinstance(sheet, Mapping):
            errors.append(Issue("SCHEMA_TYPE", location, "must be an object"))
            continue
        for key in _unknown_keys(sheet, SHEET_KEYS):
            errors.append(Issue("SCHEMA_UNKNOWN_KEY", location, f"unknown key {key!r}"))
        name = sheet.get("name")
        if not isinstance(name, str) or not name:
            errors.append(Issue("SCHEMA_TYPE", f"{location}.name", "must be a non-empty string"))
        elif name in names:
            errors.append(Issue("SCHEMA_DUPLICATE", f"{location}.name", f"duplicate sheet {name!r}"))
        else:
            names.add(name)
        state = sheet.get("state")
        if state is not None and state not in SHEET_STATES:
            errors.append(
                Issue("SCHEMA_ENUM", f"{location}.state", "must be visible, hidden, or veryHidden")
            )
        allow_extra_merges = sheet.get("allow_extra_merged_cells", True)
        if not isinstance(allow_extra_merges, bool):
            errors.append(
                Issue(
                    "SCHEMA_TYPE",
                    f"{location}.allow_extra_merged_cells",
                    "must be boolean",
                )
            )
        merges = sheet.get("merged_cells", [])
        if not isinstance(merges, list):
            errors.append(Issue("SCHEMA_TYPE", f"{location}.merged_cells", "must be an array"))
        else:
            normalised_merges: set[str] = set()
            for merge_index, merge in enumerate(merges):
                normalised = _normalise_range(merge)
                merge_location = f"{location}.merged_cells[{merge_index}]"
                if normalised is None:
                    errors.append(Issue("SCHEMA_RANGE", merge_location, "invalid Excel range"))
                elif normalised in normalised_merges:
                    errors.append(Issue("SCHEMA_DUPLICATE", merge_location, f"duplicate range {normalised}"))
                else:
                    normalised_merges.add(normalised)
        cells = sheet.get("cells", [])
        if not isinstance(cells, list):
            errors.append(Issue("SCHEMA_TYPE", f"{location}.cells", "must be an array"))
            continue
        refs: set[str] = set()
        for cell_index, cell in enumerate(cells):
            cell_location = f"{location}.cells[{cell_index}]"
            if not isinstance(cell, Mapping):
                errors.append(Issue("SCHEMA_TYPE", cell_location, "must be an object"))
                continue
            for key in _unknown_keys(cell, CELL_KEYS):
                errors.append(Issue("SCHEMA_UNKNOWN_KEY", cell_location, f"unknown key {key!r}"))
            ref = _normalise_cell_ref(cell.get("ref"))
            if ref is None:
                errors.append(Issue("SCHEMA_CELL_REF", f"{cell_location}.ref", "invalid Excel cell reference"))
            elif ref in refs:
                errors.append(Issue("SCHEMA_DUPLICATE", f"{cell_location}.ref", f"duplicate cell {ref}"))
            else:
                refs.add(ref)
            for boolean_key in ("required", "empty", "finite", "min_inclusive", "max_inclusive"):
                if boolean_key in cell and not isinstance(cell[boolean_key], bool):
                    errors.append(
                        Issue("SCHEMA_TYPE", f"{cell_location}.{boolean_key}", "must be boolean")
                    )
            if cell.get("empty") is True and any(
                key in cell
                for key in (
                    "type",
                    "value",
                    "finite",
                    "minimum",
                    "maximum",
                    "formula",
                    "number_format",
                    "number_format_regex",
                )
            ):
                errors.append(
                    Issue(
                        "SCHEMA_CONFLICT",
                        cell_location,
                        "empty=true cannot be combined with value, type, formula, range, or format constraints",
                    )
                )
            value_type = cell.get("type")
            if value_type is not None and value_type not in CELL_TYPES:
                errors.append(
                    Issue("SCHEMA_ENUM", f"{cell_location}.type", f"must be one of {sorted(CELL_TYPES)}")
                )
            formula = cell.get("formula")
            if formula is not None and not isinstance(formula, (bool, str)):
                errors.append(
                    Issue("SCHEMA_TYPE", f"{cell_location}.formula", "must be boolean or string")
                )
            for numeric_key in ("abs_tol", "rel_tol", "minimum", "maximum"):
                if numeric_key in cell and not _is_number(cell[numeric_key]):
                    errors.append(
                        Issue("SCHEMA_TYPE", f"{cell_location}.{numeric_key}", "must be a JSON number")
                    )
                elif numeric_key in {"abs_tol", "rel_tol"} and cell.get(numeric_key, 0) < 0:
                    errors.append(
                        Issue("SCHEMA_RANGE", f"{cell_location}.{numeric_key}", "must be non-negative")
                    )
            if "minimum" in cell and "maximum" in cell:
                if _is_number(cell["minimum"]) and _is_number(cell["maximum"]):
                    if cell["minimum"] > cell["maximum"]:
                        errors.append(
                            Issue("SCHEMA_RANGE", cell_location, "minimum must not exceed maximum")
                        )
            if ("abs_tol" in cell or "rel_tol" in cell) and not _is_number(cell.get("value")):
                errors.append(
                    Issue(
                        "SCHEMA_CONFLICT",
                        cell_location,
                        "numeric tolerances require a numeric value",
                    )
                )
            for format_key in ("number_format", "number_format_regex"):
                if format_key in cell and not isinstance(cell[format_key], str):
                    errors.append(Issue("SCHEMA_TYPE", f"{cell_location}.{format_key}", "must be string"))
            if "number_format" in cell and "number_format_regex" in cell:
                errors.append(
                    Issue(
                        "SCHEMA_CONFLICT",
                        cell_location,
                        "use either number_format or number_format_regex, not both",
                    )
                )
            if isinstance(cell.get("number_format_regex"), str):
                try:
                    re.compile(cell["number_format_regex"])
                except re.error as exc:
                    errors.append(
                        Issue(
                            "SCHEMA_REGEX",
                            f"{cell_location}.number_format_regex",
                            f"invalid regular expression: {exc}",
                        )
                    )
    return errors


class XlsxPackage:
    def __init__(self, path: Path):
        self.path = path
        self.archive = zipfile.ZipFile(path)
        total = sum(info.file_size for info in self.archive.infolist())
        if total > MAX_PACKAGE_BYTES:
            self.archive.close()
            raise ValueError(f"uncompressed XLSX package exceeds {MAX_PACKAGE_BYTES} bytes")

    def close(self) -> None:
        self.archive.close()

    def read_xml(self, part: str, *, required: bool = True) -> ET.Element | None:
        normalised = str(PurePosixPath(part))
        try:
            info = self.archive.getinfo(normalised)
        except KeyError:
            if required:
                raise ValueError(f"required XLSX part {normalised!r} is missing") from None
            return None
        if info.file_size > MAX_XML_BYTES:
            raise ValueError(f"XLSX XML part {normalised!r} exceeds {MAX_XML_BYTES} bytes")
        try:
            return ET.fromstring(self.archive.read(info))
        except ET.ParseError as exc:
            raise ValueError(f"invalid XML in XLSX part {normalised!r}: {exc}") from exc


def _resolve_part(base_part: str, target: str) -> str:
    if not target or "\\" in target:
        raise ValueError(f"unsafe XLSX relationship target {target!r}")
    if target.startswith("/"):
        candidate = posixpath.normpath(target.lstrip("/"))
    else:
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))
    if candidate == ".." or candidate.startswith("../") or not candidate.startswith("xl/"):
        raise ValueError(f"XLSX relationship escapes the xl package tree: {target!r}")
    return candidate


def _read_shared_strings(package: XlsxPackage) -> list[str]:
    root = package.read_xml("xl/sharedStrings.xml", required=False)
    if root is None:
        return []
    return ["".join(node.text or "" for node in item.findall(f".//{{{MAIN_NS}}}t")) for item in root.findall(f"{{{MAIN_NS}}}si")]


def _read_formats(package: XlsxPackage) -> list[str]:
    root = package.read_xml("xl/styles.xml", required=False)
    if root is None:
        return ["General"]
    custom: dict[int, str] = {}
    num_formats = root.find(f"{{{MAIN_NS}}}numFmts")
    if num_formats is not None:
        for entry in num_formats.findall(f"{{{MAIN_NS}}}numFmt"):
            try:
                identifier = int(entry.attrib["numFmtId"])
            except (KeyError, ValueError):
                continue
            custom[identifier] = entry.attrib.get("formatCode", f"custom:{identifier}")
    result: list[str] = []
    cell_formats = root.find(f"{{{MAIN_NS}}}cellXfs")
    if cell_formats is None:
        return ["General"]
    for entry in cell_formats.findall(f"{{{MAIN_NS}}}xf"):
        try:
            identifier = int(entry.attrib.get("numFmtId", "0"))
        except ValueError:
            identifier = 0
        result.append(custom.get(identifier, BUILTIN_FORMATS.get(identifier, f"builtin:{identifier}")))
    return result or ["General"]


def _cell_value(
    element: ET.Element,
    shared_strings: Sequence[str],
    formats: Sequence[str],
) -> CellValue:
    ref = _normalise_cell_ref(element.attrib.get("r"))
    if ref is None:
        raise ValueError(f"worksheet contains invalid cell reference {element.attrib.get('r')!r}")
    formula_element = element.find(f"{{{MAIN_NS}}}f")
    formula = None if formula_element is None else (formula_element.text or "")
    value_element = element.find(f"{{{MAIN_NS}}}v")
    raw_value = None if value_element is None else (value_element.text or "")
    cell_type = element.attrib.get("t", "n")
    value: Any = None
    value_type = "blank"
    if cell_type == "inlineStr":
        inline = element.find(f"{{{MAIN_NS}}}is")
        value = "" if inline is None else "".join(
            node.text or "" for node in inline.findall(f".//{{{MAIN_NS}}}t")
        )
        value_type = "string"
    elif raw_value is not None:
        if cell_type == "s":
            try:
                index = int(raw_value)
                value = shared_strings[index]
            except (ValueError, IndexError) as exc:
                raise ValueError(f"cell {ref} has invalid shared-string index {raw_value!r}") from exc
            value_type = "string"
        elif cell_type == "b":
            if raw_value not in {"0", "1", "false", "true"}:
                raise ValueError(f"cell {ref} has invalid boolean value {raw_value!r}")
            value = raw_value in {"1", "true"}
            value_type = "boolean"
        elif cell_type in {"str", "inlineStr"}:
            value = raw_value
            value_type = "string"
        elif cell_type == "e":
            value = raw_value
            value_type = "error"
        elif cell_type == "d":
            value = raw_value
            value_type = "date"
        elif cell_type in {"n", ""}:
            try:
                value = float(raw_value)
            except ValueError as exc:
                raise ValueError(f"cell {ref} has invalid numeric value {raw_value!r}") from exc
            value_type = "number"
        else:
            raise ValueError(f"cell {ref} uses unsupported XLSX type {cell_type!r}")
    try:
        style_index = int(element.attrib.get("s", "0"))
    except ValueError as exc:
        raise ValueError(f"cell {ref} has invalid style index") from exc
    if style_index < 0 or style_index >= len(formats):
        raise ValueError(f"cell {ref} references missing style index {style_index}")
    return CellValue(ref, value, value_type, formula, formats[style_index])


def _read_sheet(
    package: XlsxPackage,
    name: str,
    state: str,
    part: str,
    shared_strings: Sequence[str],
    formats: Sequence[str],
) -> SheetData:
    root = package.read_xml(part)
    assert root is not None
    cells: dict[str, CellValue] = {}
    for element in root.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row/{{{MAIN_NS}}}c"):
        cell = _cell_value(element, shared_strings, formats)
        if cell.ref in cells:
            raise ValueError(f"worksheet {name!r} contains duplicate cell {cell.ref}")
        cells[cell.ref] = cell
    merges: set[str] = set()
    merge_root = root.find(f"{{{MAIN_NS}}}mergeCells")
    if merge_root is not None:
        for element in merge_root.findall(f"{{{MAIN_NS}}}mergeCell"):
            normalised = _normalise_range(element.attrib.get("ref"))
            if normalised is None:
                raise ValueError(f"worksheet {name!r} contains invalid merged-cell range")
            merges.add(normalised)
    return SheetData(name, state, cells, merges)


def read_xlsx(path: Path) -> WorkbookData:
    if not path.is_file():
        raise ValueError(f"workbook does not exist or is not a regular file: {path}")
    if path.suffix.lower() != ".xlsx":
        raise ValueError("workbook must use the .xlsx format")
    if not zipfile.is_zipfile(path):
        raise ValueError("workbook is not a valid ZIP-based XLSX package")
    package = XlsxPackage(path)
    try:
        workbook = package.read_xml("xl/workbook.xml")
        relationships = package.read_xml("xl/_rels/workbook.xml.rels")
        assert workbook is not None and relationships is not None
        relationship_map: dict[str, str] = {}
        for entry in relationships.findall(f"{{{PKG_REL_NS}}}Relationship"):
            identifier = entry.attrib.get("Id")
            target = entry.attrib.get("Target")
            if not identifier or not target:
                continue
            if entry.attrib.get("TargetMode", "Internal") != "Internal":
                continue
            relationship_map[identifier] = _resolve_part("xl/workbook.xml", target)
        shared_strings = _read_shared_strings(package)
        formats = _read_formats(package)
        result: list[SheetData] = []
        sheets_root = workbook.find(f"{{{MAIN_NS}}}sheets")
        if sheets_root is None:
            raise ValueError("workbook contains no sheets collection")
        seen_names: set[str] = set()
        for sheet in sheets_root.findall(f"{{{MAIN_NS}}}sheet"):
            name = sheet.attrib.get("name")
            relationship_id = sheet.attrib.get(f"{{{DOC_REL_NS}}}id")
            if not name or not relationship_id:
                raise ValueError("workbook has a sheet without name or relationship")
            if name in seen_names:
                raise ValueError(f"workbook contains duplicate sheet name {name!r}")
            seen_names.add(name)
            part = relationship_map.get(relationship_id)
            if part is None:
                raise ValueError(f"worksheet relationship {relationship_id!r} cannot be resolved")
            state = sheet.attrib.get("state", "visible")
            if state not in SHEET_STATES:
                raise ValueError(f"worksheet {name!r} has invalid state {state!r}")
            result.append(
                _read_sheet(package, name, state, part, shared_strings, formats)
            )
        if not result:
            raise ValueError("workbook contains no worksheets")
        return WorkbookData(result)
    finally:
        package.close()


def _formula_text(value: str) -> str:
    return value.strip().removeprefix("=").strip()


def _validate_cell(
    sheet: SheetData,
    specification: Mapping[str, Any],
    errors: list[Issue],
) -> None:
    ref = _normalise_cell_ref(specification.get("ref"))
    assert ref is not None
    location = f"{sheet.name}!{ref}"
    cell = sheet.cells.get(ref)
    if cell is None:
        cell = CellValue(ref, None, "blank", None, "General")
    if specification.get("empty") is True:
        if not cell.empty:
            errors.append(Issue("CELL_NOT_EMPTY", location, "cell must be empty"))
        return
    if specification.get("required") is True and cell.empty:
        errors.append(Issue("CELL_REQUIRED", location, "required cell is empty"))
    expected_type = specification.get("type")
    if expected_type is not None and cell.value_type != expected_type:
        errors.append(
            Issue(
                "CELL_TYPE",
                location,
                f"expected {expected_type}, observed {cell.value_type}",
            )
        )
    formula_requirement = specification.get("formula")
    if formula_requirement is True and cell.formula is None:
        errors.append(Issue("CELL_FORMULA", location, "formula is required"))
    elif formula_requirement is False and cell.formula is not None:
        errors.append(Issue("CELL_FORMULA", location, "formula is forbidden"))
    elif isinstance(formula_requirement, str):
        if cell.formula is None:
            errors.append(Issue("CELL_FORMULA", location, "expected a formula but cell has none"))
        elif _formula_text(cell.formula) != _formula_text(formula_requirement):
            errors.append(
                Issue(
                    "CELL_FORMULA",
                    location,
                    f"formula differs: expected {_formula_text(formula_requirement)!r}, observed {_formula_text(cell.formula)!r}",
                )
            )
    if "value" in specification:
        expected = specification["value"]
        if _is_number(expected):
            if cell.value_type != "number":
                errors.append(Issue("CELL_VALUE", location, "numeric expected value requires a cached numeric value"))
            elif not math.isclose(
                float(cell.value),
                float(expected),
                rel_tol=float(specification.get("rel_tol", 0.0)),
                abs_tol=float(specification.get("abs_tol", 0.0)),
            ):
                errors.append(
                    Issue(
                        "CELL_VALUE",
                        location,
                        f"expected {expected!r} within tolerance, observed {cell.value!r}",
                    )
                )
        elif cell.value != expected:
            errors.append(
                Issue("CELL_VALUE", location, f"expected {expected!r}, observed {cell.value!r}")
            )
    if specification.get("finite") is True:
        if cell.value_type != "number" or not math.isfinite(float(cell.value)):
            errors.append(Issue("CELL_NONFINITE", location, "cell must contain a finite numeric value"))
    if "minimum" in specification or "maximum" in specification:
        if cell.value_type != "number" or not math.isfinite(float(cell.value)):
            errors.append(Issue("CELL_RANGE", location, "range constraint requires a finite numeric value"))
        else:
            value = float(cell.value)
            if "minimum" in specification:
                minimum = float(specification["minimum"])
                inclusive = specification.get("min_inclusive", True)
                if value < minimum or (not inclusive and value == minimum):
                    relation = ">=" if inclusive else ">"
                    errors.append(Issue("CELL_RANGE", location, f"value {value} must be {relation} {minimum}"))
            if "maximum" in specification:
                maximum = float(specification["maximum"])
                inclusive = specification.get("max_inclusive", True)
                if value > maximum or (not inclusive and value == maximum):
                    relation = "<=" if inclusive else "<"
                    errors.append(Issue("CELL_RANGE", location, f"value {value} must be {relation} {maximum}"))
    if "number_format" in specification and cell.number_format != specification["number_format"]:
        errors.append(
            Issue(
                "CELL_NUMBER_FORMAT",
                location,
                f"expected {specification['number_format']!r}, observed {cell.number_format!r}",
            )
        )
    if "number_format_regex" in specification:
        if re.fullmatch(specification["number_format_regex"], cell.number_format) is None:
            errors.append(
                Issue(
                    "CELL_NUMBER_FORMAT",
                    location,
                    f"format {cell.number_format!r} does not match the required expression",
                )
            )


def validate_workbook(workbook_path: Path, schema: Mapping[str, Any]) -> dict[str, Any]:
    schema_errors = _validate_schema(schema)
    if schema_errors:
        return {
            "status": "FAIL",
            "visual_status": "UNVERIFIED",
            "workbook": str(workbook_path),
            "checked_sheets": 0,
            "checked_cells": 0,
            "errors": [asdict(issue) for issue in schema_errors],
            "warnings": [
                "Workbook rendering and visual layout were not inspected."
            ],
        }
    try:
        workbook = read_xlsx(workbook_path)
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        return {
            "status": "FAIL",
            "visual_status": "UNVERIFIED",
            "workbook": str(workbook_path),
            "checked_sheets": 0,
            "checked_cells": 0,
            "errors": [asdict(Issue("WORKBOOK_READ", "$", str(exc)))],
            "warnings": [
                "Workbook rendering and visual layout were not inspected."
            ],
        }
    errors: list[Issue] = []
    expected_sheets = list(schema["sheets"])
    expected_names = [str(sheet["name"]) for sheet in expected_sheets]
    actual_names = [sheet.name for sheet in workbook.sheets]
    actual_by_name = {sheet.name: sheet for sheet in workbook.sheets}
    for name in expected_names:
        if name not in actual_by_name:
            errors.append(Issue("SHEET_MISSING", "$", f"required sheet {name!r} is missing"))
    extras = [name for name in actual_names if name not in expected_names]
    if extras and not schema.get("allow_extra_sheets", False):
        errors.append(Issue("SHEET_EXTRA", "$", f"unexpected sheets: {extras!r}"))
    order = schema.get("sheet_order", "exact")
    if order == "exact" and actual_names != expected_names:
        errors.append(
            Issue(
                "SHEET_ORDER",
                "$",
                f"expected exact sheet order {expected_names!r}, observed {actual_names!r}",
            )
        )
    elif order == "relative":
        observed_required = [name for name in actual_names if name in expected_names]
        if observed_required != expected_names:
            errors.append(
                Issue(
                    "SHEET_ORDER",
                    "$",
                    f"required sheet order is {expected_names!r}, observed {observed_required!r}",
                )
            )
    checked_cells = 0
    for sheet_specification in expected_sheets:
        sheet = actual_by_name.get(sheet_specification["name"])
        if sheet is None:
            continue
        expected_state = sheet_specification.get("state")
        if expected_state is not None and sheet.state != expected_state:
            errors.append(
                Issue(
                    "SHEET_STATE",
                    sheet.name,
                    f"expected {expected_state}, observed {sheet.state}",
                )
            )
        expected_merges = {
            _normalise_range(value) for value in sheet_specification.get("merged_cells", [])
        }
        missing_merges = sorted(expected_merges - sheet.merged_cells)
        if missing_merges:
            errors.append(
                Issue("MERGE_MISSING", sheet.name, f"required merged ranges are missing: {missing_merges!r}")
            )
        if not sheet_specification.get("allow_extra_merged_cells", True):
            extra_merges = sorted(sheet.merged_cells - expected_merges)
            if extra_merges:
                errors.append(
                    Issue("MERGE_EXTRA", sheet.name, f"unexpected merged ranges: {extra_merges!r}")
                )
        for cell_specification in sheet_specification.get("cells", []):
            checked_cells += 1
            _validate_cell(sheet, cell_specification, errors)
    return {
        "status": "PASS" if not errors else "FAIL",
        "visual_status": "UNVERIFIED",
        "workbook": str(workbook_path),
        "observed_sheet_order": actual_names,
        "checked_sheets": len(expected_sheets),
        "checked_cells": checked_cells,
        "errors": [asdict(issue) for issue in errors],
        "warnings": [
            "Workbook rendering, clipping, print layout, charts, and recalculation were not visually inspected.",
            "Formula value checks use the cached values stored in the XLSX package.",
        ],
    }


def _write_report(report: Mapping[str, Any], output: Path | None) -> None:
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    sys.stdout.write(payload)
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(payload, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, required=True, help="XLSX file to validate")
    parser.add_argument("--schema", type=Path, required=True, help="explicit JSON validation schema")
    parser.add_argument("--output", type=Path, help="optional path for the JSON report")
    args = parser.parse_args(argv)
    try:
        schema = _load_json(args.schema)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        report = {
            "status": "FAIL",
            "visual_status": "UNVERIFIED",
            "workbook": str(args.workbook),
            "errors": [asdict(Issue("SCHEMA_READ", "$", str(exc)))],
            "warnings": ["Workbook rendering and visual layout were not inspected."],
        }
        _write_report(report, args.output)
        return 2
    report = validate_workbook(args.workbook, schema)
    _write_report(report, args.output)
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
