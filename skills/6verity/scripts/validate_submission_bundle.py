#!/usr/bin/env python3
"""Validate a CUMCM-style submission bundle before it is handed off for upload.

只做通用结构校验 + MD5 + 命名提示，绝不硬编码某一届的命名规则。当届承诺书/
编号页、论文命名与 AI 使用声明以官方最新模板为准，由队员在竞赛客户端人工
核对并上传；本脚本只产出机器清单与哈希。

Configuration schema (all keys optional, unknown keys are rejected so a
misspelling cannot silently disable a gate)::

    {
      "required_files": ["code/main.py", "results/*.csv"],
      "paper_pdf": "paper/main.pdf",
      "paper_min_bytes": 10240,
      "support_material_zip": "submission/support_material.zip",
      "require_code_entries": true,
      "code_entry_prefix": "code/",
      "forbid_pdf_in_zip": true,
      "commitment_file": null,
      "ai_usage_log": "reports/AI_USAGE_LOG.jsonl",
      "paper_naming_hint": null
    }

Defaults and meaning:

    required_files          []                            额外必交文件（相对 root，可含 glob）
    paper_pdf               "paper/main.pdf"              论文 PDF；null 表示不检查该项
    paper_min_bytes         10240                         论文 PDF 最小字节数下限
    support_material_zip    "submission/support_material.zip"
                                                          支撑材料 zip；null 表示无要求
    require_code_entries    true                          是否要求 zip 内含 code/ 相关条目
    code_entry_prefix       "code/"                       判定"代码条目"的名称前缀
    forbid_pdf_in_zip       true                          是否禁止 zip 内出现 .pdf 条目
    commitment_file         null                          承诺书/编号页；非 null 时要求存在
    ai_usage_log            "reports/AI_USAGE_LOG.jsonl"  AI 使用日志；null 表示无要求
    paper_naming_hint       null                          命名提示，仅写入建议，不参与 PASS/FAIL

Per-item status is PASS / FAIL / N/A.  Overall status is PASS when every
applicable item passes, FAIL when any item fails, and UNVERIFIED when no item
is applicable (config missing or every path disabled).  Exit status is 0 for
PASS, 1 for FAIL, and 2 for UNVERIFIED.  The machine report is written to
``reports/submission_bundle_validation.json`` and the human-readable report to
``reports/submission_bundle_validation.txt`` under the project root; stdout
carries JSON when ``--json`` is set and text otherwise.

This script intentionally uses only the Python standard library so it runs in
clean, isolated environments without any dependency install step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_CONFIG_PATH = Path("reports") / "submission_bundle_config.json"
REPORT_JSON = Path("reports") / "submission_bundle_validation.json"
REPORT_TEXT = Path("reports") / "submission_bundle_validation.txt"

# AI 使用日志逐行必须非空的关键字段；少任何一个都视为该行不合法。
AI_LOG_FIELDS = ("time", "tool", "purpose")

# PDF 规范允许魔数出现在文件前 1024 字节内，因此按窗口探测而非只读前 5 字节。
PDF_MAGIC = b"%PDF-"
PDF_MAGIC_WINDOW = 1024

DEFAULT_PAPER_MIN_BYTES = 10 * 1024

ALLOWED_KEYS = {
    "required_files",
    "paper_pdf",
    "paper_min_bytes",
    "support_material_zip",
    "require_code_entries",
    "code_entry_prefix",
    "forbid_pdf_in_zip",
    "commitment_file",
    "ai_usage_log",
    "paper_naming_hint",
}

DEFAULT_CONFIG: dict[str, Any] = {
    "required_files": [],
    "paper_pdf": "paper/main.pdf",
    "paper_min_bytes": DEFAULT_PAPER_MIN_BYTES,
    "support_material_zip": "submission/support_material.zip",
    "require_code_entries": True,
    "code_entry_prefix": "code/",
    "forbid_pdf_in_zip": True,
    "commitment_file": None,
    "ai_usage_log": "reports/AI_USAGE_LOG.jsonl",
    "paper_naming_hint": None,
}

EXIT_CODES = {"PASS": 0, "FAIL": 1, "UNVERIFIED": 2}


def _reject_json_constant(value: str) -> None:
    # 拒绝 NaN/Infinity：JSON 规范不含它们，且出现在配置文件里几乎总是错误。
    raise ValueError(f"non-standard JSON number {value!r} is not allowed")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle, parse_constant=_reject_json_constant)


def _validate_config(config: Any) -> tuple[dict[str, Any], list[str]]:
    """校验用户配置并合并缺省值。

    Args:
        config: 从配置文件读出的原始 JSON 值。

    Returns:
        ``(merged_config, errors)``：合并后的配置字典与错误列表；错误非空时
        合并结果仍为缺省值，但调用方应直接按配置错误处理。
    """
    if not isinstance(config, Mapping):
        return dict(DEFAULT_CONFIG), ["config root must be a JSON object"]
    merged = dict(DEFAULT_CONFIG)
    errors: list[str] = []
    # 拒绝未知键：拼写错误不该被静默忽略，否则可能悄悄关掉某道门禁。
    for key in sorted(config):
        if key not in ALLOWED_KEYS:
            errors.append(f"unknown key {key!r}")
            continue
        value = config[key]
        if key in {
            "paper_pdf",
            "support_material_zip",
            "commitment_file",
            "ai_usage_log",
            "paper_naming_hint",
        }:
            if value is not None and not isinstance(value, str):
                errors.append(f"{key} must be a string or null")
            else:
                merged[key] = value
        elif key == "required_files":
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                errors.append("required_files must be an array of strings")
            else:
                merged[key] = list(value)
        elif key in {"require_code_entries", "forbid_pdf_in_zip"}:
            if not isinstance(value, bool):
                errors.append(f"{key} must be a boolean")
            else:
                merged[key] = value
        elif key == "paper_min_bytes":
            # bool 是 int 的子类，必须显式排除，避免 true 被当成字节数。
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                errors.append("paper_min_bytes must be a non-negative integer")
            else:
                merged[key] = value
        elif key == "code_entry_prefix":
            if not isinstance(value, str) or not value:
                errors.append("code_entry_prefix must be a non-empty string")
            else:
                merged[key] = value
    return merged, errors


def _has_glob(pattern: str) -> bool:
    return any(character in pattern for character in "*?[")


def _file_check(root: Path, relative: str) -> tuple[str, str]:
    """检查相对路径文件存在且非空。

    Args:
        root: 项目根目录。
        relative: 相对 root 的路径。

    Returns:
        ``(status, detail)``，status 为 PASS 或 FAIL。
    """
    path = root / relative
    if not path.is_file():
        return "FAIL", f"文件缺失: {relative}"
    size = path.stat().st_size
    if size == 0:
        return "FAIL", f"文件为空: {relative}"
    return "PASS", f"存在且非空 ({size} bytes): {relative}"


def _md5(path: Path) -> str:
    digest = hashlib.md5()
    # 流式读入，避免把超大 PDF 整份加载进内存。
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _check_required_file(root: Path, pattern: str) -> dict[str, Any]:
    check = {"check": f"required_file:{pattern}", "path": pattern}
    if _has_glob(pattern):
        matches = sorted(path for path in root.glob(pattern) if path.is_file())
        if not matches:
            return {**check, "status": "FAIL", "detail": f"无文件匹配 glob: {pattern}"}
        empty = [str(path.relative_to(root)) for path in matches if path.stat().st_size == 0]
        if empty:
            return {
                **check,
                "status": "FAIL",
                "detail": f"匹配到空文件: {', '.join(empty)}",
            }
        return {**check, "status": "PASS", "detail": f"匹配 {len(matches)} 个非空文件"}
    status, detail = _file_check(root, pattern)
    return {**check, "status": status, "detail": detail}


def _check_paper(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    relative = config.get("paper_pdf")
    check = {"check": "paper_pdf", "path": relative, "md5": None}
    if relative is None:
        return {**check, "status": "N/A", "detail": "未配置论文 PDF"}
    path = root / relative
    if not path.is_file():
        return {**check, "status": "FAIL", "detail": f"论文 PDF 缺失: {relative}"}
    size = path.stat().st_size
    digest = hashlib.md5()
    magic_ok = False
    # 单次流式读取同时算 MD5 并探测 %PDF- 魔数，避免二次打开文件。
    with path.open("rb") as handle:
        head = handle.read(PDF_MAGIC_WINDOW)
        magic_ok = PDF_MAGIC in head
        digest.update(head)
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    md5 = digest.hexdigest()
    problems: list[str] = []
    if size == 0:
        problems.append("文件为空")
    elif size < config["paper_min_bytes"]:
        problems.append(
            f"小于 {config['paper_min_bytes']} bytes 下限（当前 {size} bytes）"
        )
    if not magic_ok:
        problems.append("缺少 %PDF- 魔数，可能不是有效 PDF")
    if problems:
        return {**check, "status": "FAIL", "detail": "; ".join(problems), "md5": md5}
    return {
        **check,
        "status": "PASS",
        "detail": f"存在、非空、通过大小与魔数检查（{size} bytes）",
        "md5": md5,
    }


def _check_zip(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    relative = config.get("support_material_zip")
    check = {"check": "support_material_zip", "path": relative}
    if relative is None:
        return {**check, "status": "N/A", "detail": "未配置支撑材料 zip"}
    path = root / relative
    if not path.is_file():
        return {**check, "status": "FAIL", "detail": f"支撑材料 zip 缺失: {relative}"}
    if not zipfile.is_zipfile(path):
        return {**check, "status": "FAIL", "detail": f"不是有效的 zip 归档: {relative}"}
    problems: list[str] = []
    pdf_entries: list[str] = []
    code_entries = 0
    total_entries = 0
    # zipfile 的 testzip 会逐条读回内容核对 CRC，能发现压缩数据静默损坏。
    try:
        with zipfile.ZipFile(path) as archive:
            names = [info.filename for info in archive.infolist()]
            total_entries = len(names)
            bad = archive.testzip()
            if bad is not None:
                problems.append(f"CRC 校验失败（首个损坏条目: {bad}）")
            if not names:
                problems.append("归档为空，无任何条目")
            for name in names:
                if name.lower().endswith(".pdf"):
                    pdf_entries.append(name)
                if (
                    name.startswith(config["code_entry_prefix"])
                    and not name.endswith("/")
                ):
                    code_entries += 1
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        problems.append(f"无法解压或校验 zip: {exc}")
    # 保守处理：任何 .pdf 条目都视为论文类 PDF 而禁止，避免正文 PDF 混入。
    if config["forbid_pdf_in_zip"] and pdf_entries:
        problems.append("包含 .pdf 条目（视为论文类 PDF，禁止）: " + ", ".join(pdf_entries))
    if config["require_code_entries"] and code_entries == 0:
        problems.append(f"缺少以 {config['code_entry_prefix']!r} 开头的代码条目")
    if problems:
        return {**check, "status": "FAIL", "detail": "; ".join(problems)}
    summary = f"可解压且 CRC 通过，共 {total_entries} 个条目"
    if config["require_code_entries"]:
        summary += f"，含 {code_entries} 个代码条目"
    if config["forbid_pdf_in_zip"]:
        summary += "，无 .pdf 条目"
    return {**check, "status": "PASS", "detail": summary}


def _check_commitment(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    relative = config.get("commitment_file")
    check = {"check": "commitment_file", "path": relative}
    if relative is None:
        return {**check, "status": "N/A", "detail": "未配置承诺书/编号页"}
    status, detail = _file_check(root, relative)
    return {**check, "status": status, "detail": detail}


def _nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _check_ai_log(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    relative = config.get("ai_usage_log")
    check = {"check": "ai_usage_log", "path": relative}
    if relative is None:
        return {**check, "status": "N/A", "detail": "未配置 AI 使用日志"}
    path = root / relative
    if not path.is_file():
        return {**check, "status": "FAIL", "detail": f"AI 使用日志缺失: {relative}"}
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        return {**check, "status": "FAIL", "detail": f"无法读取: {exc}"}
    lines = text.splitlines()
    if not lines:
        return {**check, "status": "FAIL", "detail": "日志为空"}
    bad_lines: list[str] = []
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            bad_lines.append(f"第 {index} 行为空")
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            bad_lines.append(f"第 {index} 行不是合法 JSON: {exc}")
            continue
        if not isinstance(record, dict):
            bad_lines.append(f"第 {index} 行不是 JSON 对象")
            continue
        missing = [
            field
            for field in AI_LOG_FIELDS
            if field not in record or not _nonempty(record[field])
        ]
        if missing:
            bad_lines.append(f"第 {index} 行缺少/为空字段: {', '.join(missing)}")
    if bad_lines:
        preview = "; ".join(bad_lines[:10])
        if len(bad_lines) > 10:
            preview += f"; ...（共 {len(bad_lines)} 行）"
        return {**check, "status": "FAIL", "detail": f"{len(bad_lines)} 行不合法: {preview}"}
    return {
        **check,
        "status": "PASS",
        "detail": f"{len(lines)} 行均合法且 time/tool/purpose 非空",
    }


def validate_submission_bundle(
    root: Path,
    config: Mapping[str, Any],
    *,
    config_path: Path | None = None,
) -> dict[str, Any]:
    """对项目根执行提交包结构校验并返回报告字典。

    Args:
        root: 项目根目录。
        config: 已读取的配置映射（将被合并缺省值）。
        config_path: 配置文件路径，仅写入报告用于追溯。

    Returns:
        报告字典，含 ``status``（PASS/FAIL/UNVERIFIED）、``checks``、
        ``paper_md5``、``failures`` 与 ``suggestions``。
    """
    merged, config_errors = _validate_config(config)
    if config_errors:
        return {
            "status": "FAIL",
            "project_root": str(root),
            "config_path": str(config_path) if config_path else None,
            "config": merged,
            "checks": [],
            "paper_md5": None,
            "failures": [{"check": "config", "message": message} for message in config_errors],
            "suggestions": ["修正配置文件后重新运行。"],
        }
    checks: list[dict[str, Any]] = []
    for pattern in merged["required_files"]:
        checks.append(_check_required_file(root, pattern))
    paper = _check_paper(root, merged)
    checks.append(paper)
    checks.append(_check_zip(root, merged))
    checks.append(_check_commitment(root, merged))
    checks.append(_check_ai_log(root, merged))

    failures = [
        {"check": check["check"], "message": check["detail"]}
        for check in checks
        if check["status"] == "FAIL"
    ]
    applicable = [check for check in checks if check["status"] != "N/A"]
    if failures:
        status = "FAIL"
    elif not applicable:
        status = "UNVERIFIED"
    else:
        status = "PASS"

    suggestions: list[str] = []
    if merged["paper_pdf"] is not None:
        hint = merged["paper_naming_hint"]
        if hint:
            suggestions.append(
                f"论文命名以当届官方模板为准（配置提示: {hint}）；本脚本不硬编码当届规则，请人工核对。"
            )
        else:
            suggestions.append(
                "论文命名以当届官方模板为准；本脚本不硬编码当届规则，请人工核对。"
            )
    suggestions.append(
        "承诺书/编号页与 AI 使用声明以当届官方模板为准；Agent 只生成清单与哈希，上传由队员在竞赛客户端完成。"
    )
    return {
        "status": status,
        "project_root": str(root),
        "config_path": str(config_path) if config_path else None,
        "config": merged,
        "checks": checks,
        "paper_md5": paper.get("md5"),
        "failures": failures,
        "suggestions": suggestions,
    }


def _format_text_report(report: Mapping[str, Any]) -> str:
    lines = [
        "提交包验收报告",
        "=" * 20,
        f"项目根: {report['project_root']}",
        f"配置: {report['config_path'] or '(默认)'}",
        f"总体: {report['status']}",
        "",
        "检查项:",
    ]
    for check in report["checks"]:
        path_note = f" {check['path']}" if check.get("path") else ""
        lines.append(f"[{check['status']}] {check['check']}{path_note}: {check['detail']}")
    if report["paper_md5"]:
        lines.append("")
        lines.append(f"论文 MD5: {report['paper_md5']}")
    if report["suggestions"]:
        lines.append("")
        lines.append("建议:")
        for suggestion in report["suggestions"]:
            lines.append(f"- {suggestion}")
    return "\n".join(lines) + "\n"


def _write_reports(root: Path, report: Mapping[str, Any]) -> None:
    json_path = root / REPORT_JSON
    text_path = root / REPORT_TEXT
    # 两份报告都落盘，便于后续阶段直接引用机器结果与人类可读结论。
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    json_path.write_text(payload, encoding="utf-8")
    text_path.write_text(_format_text_report(report), encoding="utf-8")


def _missing_config_report(
    root: Path, config_path: Path, *, explicit: bool
) -> dict[str, Any]:
    status = "FAIL" if explicit else "UNVERIFIED"
    detail = "显式指定的配置文件缺失" if explicit else "默认配置文件缺失，未执行任何检查"
    check_status = "FAIL" if explicit else "N/A"
    failures = [] if not explicit else [{"check": "config", "message": detail}]
    return {
        "status": status,
        "project_root": str(root),
        "config_path": str(config_path),
        "config": dict(DEFAULT_CONFIG),
        "checks": [
            {"check": "config", "path": None, "status": check_status, "detail": detail}
        ],
        "paper_md5": None,
        "failures": failures,
        "suggestions": ["提供配置文件后重新运行。"],
    }


def _config_error_report(root: Path, config_path: Path, exc: BaseException) -> dict[str, Any]:
    return {
        "status": "FAIL",
        "project_root": str(root),
        "config_path": str(config_path),
        "config": dict(DEFAULT_CONFIG),
        "checks": [],
        "paper_md5": None,
        "failures": [{"check": "config", "message": f"无法读取或解析配置: {exc}"}],
        "suggestions": ["修正配置文件后重新运行。"],
    }


def main(argv: Sequence[str] | None = None) -> int:
    """解析命令行参数并执行提交包验收。

    Args:
        argv: 命令行参数列表；缺省时使用 sys.argv[1:]。

    Returns:
        退出码：0=PASS，1=FAIL，2=UNVERIFIED。
    """
    # Windows 控制台默认编码（GBK/cp1252）无法编码输出中的中文与特殊字符，
    # 会导致脚本在写入 stdout 时崩溃（2026-08 CI 实测）。统一按 UTF-8 输出。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path, help="项目根目录")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="配置 JSON 路径（默认 <root>/reports/submission_bundle_config.json）",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="stdout 输出 JSON 而非人类可读文本",
    )
    args = parser.parse_args(argv)
    root = args.project_root
    config_path = args.config if args.config is not None else root / DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        # 默认配置缺失 → 无适用检查 → UNVERIFIED；显式指定的配置缺失是调用错误 → FAIL。
        report = _missing_config_report(root, config_path, explicit=args.config is not None)
    else:
        try:
            config = _load_json(config_path)
        except (OSError, ValueError) as exc:
            report = _config_error_report(root, config_path, exc)
        else:
            report = validate_submission_bundle(root, config, config_path=config_path)
    _write_reports(root, report)
    if args.json:
        sys.stdout.write(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        )
    else:
        sys.stdout.write(_format_text_report(report))
    return EXIT_CODES[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
