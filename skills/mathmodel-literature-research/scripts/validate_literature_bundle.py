#!/usr/bin/env python3
"""Validate the audit trail between searches, sources, claims, and paper citations.

This script validates consistency only.  It does not contact publishers or DOI
registries and therefore cannot prove that a source is genuine.
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


REGISTRY_FIELDS = {
    "source_id",
    "title",
    "authors",
    "year",
    "venue",
    "source_type",
    "source_tier",
    "doi",
    "stable_url",
    "identity_status",
    "content_access",
    "peer_review_status",
    "evidence_role",
    "claim_ids",
    "locator",
    "retrieved_at",
    "citable",
    "adopted",
    "notes",
}

SEARCH_FIELDS = {
    "query_id",
    "search_round",
    "retrieved_at",
    "database",
    "channel",
    "language",
    "exact_query",
    "filters",
    "result_count_scanned",
    "included_source_ids",
    "exclusion_summary",
    "new_high_value_source_ids",
    "decision_impact",
    "followup_of",
}

CLAIM_FIELDS = {
    "claim_id",
    "subproblem",
    "claim_text",
    "claim_type",
    "source_ids",
    "locators",
    "variable_mapping",
    "model_decision",
    "verification_test",
    "status",
}

MAP_FIELDS = {"citation_key", "source_id", "rendered_reference"}

SEARCH_CHANNELS = {
    "direct_problem",
    "mathematical_analogue",
    "core_theory",
    "algorithm",
    "validation_benchmark",
    "limitations",
}

OPTIONAL_SEARCH_CHANNELS = {"data_standard"}

EVIDENCE_ROLES = {
    "analogous_problem",
    "core_theory_model",
    "algorithm_solver",
    "validation_metric_baseline",
    "data_parameter_standard",
    "limitation_failure",
}

IDENTITY_VALUES = {"verified", "unverified"}
ACCESS_VALUES = {"full_text", "abstract", "metadata_only", "unavailable"}
TIER_VALUES = {"S1", "S2", "S3", "S4"}
YES_NO_VALUES = {"yes", "no"}
CLAIM_STATUS_VALUES = {"supported", "conditional", "rejected", "pending"}
PEER_REVIEW_VALUES = {
    "peer_reviewed",
    "preprint",
    "official",
    "book",
    "thesis",
    "not_applicable",
}
DECISION_IMPACT_VALUES = {
    "none",
    "model",
    "assumption",
    "algorithm",
    "validation",
    "baseline",
    "limitation",
}

SEARCH_HOST_FRAGMENTS = (
    "google.",
    "bing.com",
    "baidu.com",
    "duckduckgo.com",
    "search.yahoo.com",
    "scholar.google.",
)

PLACEHOLDER_PATTERNS = (
    r"\bTODO\b",
    r"\bPLACEHOLDER\b",
    r"REFS_NOT_READY",
    r"尚未加入已核验参考文献",
    r"No verified references have been added",
    r"待补(?:充|全|写)?",
    r"示例文献",
    r"作者[，,。\s]+题名",
    r"期刊名[，,。\s]+年份",
    r"Author\.\s*[\"“]Title",
)


@dataclass
class Findings:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passes: list[str] = field(default_factory=list)

    def fail(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)

    def ok(self, message: str) -> None:
        self.passes.append(message)


def split_multi(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;；|]", value or "") if part.strip()]


def is_date_like(value: str) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:[ T].*)?", value.strip()))


def normalized_doi(value: str) -> str:
    value = value.strip()
    value = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", value, flags=re.I)
    return value


def valid_doi(value: str) -> bool:
    if not value.strip():
        return False
    return bool(re.fullmatch(r"10\.\d{4,9}/\S+", normalized_doi(value), flags=re.I))


def valid_http_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def is_search_result_url(value: str) -> bool:
    if not valid_http_url(value):
        return False
    parsed = urlparse(value.strip())
    host = parsed.netloc.lower()
    if any(fragment in host for fragment in SEARCH_HOST_FRAGMENTS):
        return True
    path = parsed.path.lower()
    return path.startswith("/search") and bool(parsed.query)


def normalized_text(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def read_csv_rows(path: Path, required: set[str], findings: Findings) -> list[dict[str, str]]:
    if not path.is_file():
        findings.fail(f"缺少文件: {path}")
        return []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            missing = sorted(required - fields)
            if missing:
                findings.fail(f"{path} 缺少字段: {', '.join(missing)}")
                return []
            rows = []
            for raw in reader:
                row = {key: (value or "").strip() for key, value in raw.items() if key is not None}
                if any(row.values()):
                    rows.append(row)
            return rows
    except (OSError, UnicodeError, csv.Error) as exc:
        findings.fail(f"无法读取 {path}: {exc}")
        return []


def find_gate(report: str) -> str | None:
    match = re.search(
        r"(?im)(?:gate\s*(?:状态|status)|门禁状态)\s*[:：]\s*(PASS|CONDITIONAL|FAIL|BLOCKED)\b",
        report,
    )
    return match.group(1).upper() if match else None


def find_research_mode(report: str) -> str:
    match = re.search(
        r"(?im)(?:research\s*mode|检索模式)\s*[:：]\s*(targeted|full)\b",
        report,
    )
    return match.group(1).lower() if match else "full"


def role_is_na(report: str, role: str) -> bool:
    pattern = rf"(?im)^\s*[-|]?\s*`?{re.escape(role)}`?\s*[:：|]\s*N/?A\s*[-—:：]\s*\S+"
    return bool(re.search(pattern, report))


def report_reached_saturation(report: str) -> bool:
    match = re.search(r"(?im)^\s*饱和判定\s*[:：]\s*([^\r\n]+)", report)
    if not match:
        return False
    value = match.group(1).strip().lower()
    return value.startswith("达到") or value.startswith("pass") or value.startswith("yes")


def validate_report(path: Path, findings: Findings) -> tuple[str, str | None, str]:
    if not path.is_file():
        findings.fail(f"缺少文件: {path}")
        return "", None, "full"
    try:
        report = path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        findings.fail(f"无法读取 {path}: {exc}")
        return "", None, "full"

    gate = find_gate(report)
    research_mode = find_research_mode(report)
    if not gate:
        findings.fail("文献报告缺少 `Gate 状态: PASS/CONDITIONAL/FAIL`。")
    elif gate in {"FAIL", "BLOCKED"}:
        findings.fail(f"文献阶段门禁为 {gate}。")
    elif gate == "CONDITIONAL":
        findings.warn("文献阶段门禁为 CONDITIONAL；必须核对缺口未支配核心模型。")
    else:
        findings.ok("文献阶段门禁声明为 PASS。")

    required_sections = (
        "问题指纹",
        "双语关键词矩阵",
        "证据角色覆盖",
        "理论到本题的变量映射",
        "候选模型路线比较",
        "检索饱和度",
        "风险、未决证据与补证动作",
    )
    for section in required_sections:
        if section not in report:
            findings.fail(f"文献报告缺少章节或内容标记: {section}")

    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, report, flags=re.I):
            findings.fail(f"文献报告仍含占位内容: {pattern}")

    if research_mode == "full" and gate == "PASS" and not report_reached_saturation(report):
        findings.fail("门禁为 PASS，但报告没有把“饱和判定”标为达到。")

    route_tokens = set(re.findall(r"路线\s*([A-Z甲乙丙一二三12])\b", report, flags=re.I))
    if len(route_tokens) < 2:
        findings.fail("候选模型比较没有明确标识至少两条路线（如“路线 A”“路线 B”）。")

    findings.ok(f"文献检索模式为 {research_mode}。")
    return report, gate, research_mode


def validate_registry(rows: list[dict[str, str]], findings: Findings) -> dict[str, dict[str, str]]:
    sources: dict[str, dict[str, str]] = {}
    if not rows:
        findings.fail("literature_registry.csv 没有来源记录。")
        return sources

    for number, row in enumerate(rows, start=2):
        prefix = f"literature_registry.csv 第 {number} 行"
        source_id = row["source_id"]
        if not source_id:
            findings.fail(f"{prefix}: source_id 为空。")
            continue
        if source_id in sources:
            findings.fail(f"{prefix}: source_id 重复: {source_id}")
            continue
        sources[source_id] = row

        for field_name in (
            "title",
            "authors",
            "year",
            "venue",
            "source_type",
            "peer_review_status",
            "retrieved_at",
        ):
            if not row[field_name]:
                findings.fail(f"{prefix}: {field_name} 为空。")
        if row["year"] and not re.fullmatch(r"\d{4}[a-z]?", row["year"], flags=re.I):
            findings.warn(f"{prefix}: year 不是常见四位年份: {row['year']}")
        if row["retrieved_at"] and not is_date_like(row["retrieved_at"]):
            findings.fail(f"{prefix}: retrieved_at 应为 YYYY-MM-DD 或 ISO 日期时间。")

        if row["source_tier"] not in TIER_VALUES:
            findings.fail(f"{prefix}: source_tier 必须是 S1/S2/S3/S4。")
        if row["identity_status"] not in IDENTITY_VALUES:
            findings.fail(f"{prefix}: identity_status 必须是 verified/unverified。")
        if row["content_access"] not in ACCESS_VALUES:
            findings.fail(f"{prefix}: content_access 值无效。")
        if row["peer_review_status"] not in PEER_REVIEW_VALUES:
            findings.fail(
                f"{prefix}: peer_review_status 必须是 "
                "peer_reviewed/preprint/official/book/thesis/not_applicable。"
            )
        if row["peer_review_status"] == "preprint" and row["source_tier"] != "S3":
            findings.fail(f"{prefix}: 预印本必须标为 S3，不能充当 S1/S2。")
        if row["citable"] not in YES_NO_VALUES or row["adopted"] not in YES_NO_VALUES:
            findings.fail(f"{prefix}: citable/adopted 必须是 yes/no。")

        roles = set(split_multi(row["evidence_role"]))
        unknown_roles = roles - EVIDENCE_ROLES
        if unknown_roles:
            findings.fail(f"{prefix}: 未知 evidence_role: {', '.join(sorted(unknown_roles))}")

        doi_ok = valid_doi(row["doi"])
        url_ok = valid_http_url(row["stable_url"])
        if row["doi"] and not doi_ok:
            findings.fail(f"{prefix}: DOI 格式无效: {row['doi']}")
        if row["stable_url"] and not url_ok:
            findings.fail(f"{prefix}: stable_url 不是有效 HTTP(S) URL。")

        if row["identity_status"] == "verified" and not (doi_ok or url_ok):
            findings.fail(f"{prefix}: verified 来源缺少有效 DOI 或稳定链接。")

        if row["citable"] == "yes":
            if row["identity_status"] != "verified":
                findings.fail(f"{prefix}: 可引用来源的身份尚未 verified。")
            if row["content_access"] not in {"full_text", "abstract"}:
                findings.fail(f"{prefix}: 可引用来源必须实际访问全文或摘要。")
            if row["source_tier"] == "S4":
                findings.fail(f"{prefix}: S4 发现线索不得标为可引用。")
            if not (doi_ok or url_ok):
                findings.fail(f"{prefix}: 可引用来源缺少 DOI 或稳定链接。")
            if is_search_result_url(row["stable_url"]):
                findings.fail(f"{prefix}: 搜索结果 URL 不得作为稳定引用链接。")

        if row["adopted"] == "yes":
            if row["citable"] != "yes":
                findings.fail(f"{prefix}: adopted=yes 但 citable 不是 yes。")
            if not split_multi(row["claim_ids"]):
                findings.fail(f"{prefix}: 被采用来源缺少 claim_ids。")
            if not row["locator"]:
                findings.fail(f"{prefix}: 被采用来源缺少原文 locator。")

    return sources


def validate_claims(
    rows: list[dict[str, str]],
    sources: dict[str, dict[str, str]],
    findings: Findings,
) -> dict[str, dict[str, str]]:
    claims: dict[str, dict[str, str]] = {}
    if not rows:
        findings.fail("claim_evidence.csv 没有 claim 记录。")
        return claims

    supported_count = 0
    for number, row in enumerate(rows, start=2):
        prefix = f"claim_evidence.csv 第 {number} 行"
        claim_id = row["claim_id"]
        if not claim_id:
            findings.fail(f"{prefix}: claim_id 为空。")
            continue
        if claim_id in claims:
            findings.fail(f"{prefix}: claim_id 重复: {claim_id}")
            continue
        claims[claim_id] = row

        for field_name in ("subproblem", "claim_text", "claim_type", "model_decision", "status"):
            if not row[field_name]:
                findings.fail(f"{prefix}: {field_name} 为空。")
        if row["status"] not in CLAIM_STATUS_VALUES:
            findings.fail(f"{prefix}: status 必须是 supported/conditional/rejected/pending。")

        linked_ids = split_multi(row["source_ids"])
        missing_ids = [source_id for source_id in linked_ids if source_id not in sources]
        if missing_ids:
            findings.fail(f"{prefix}: 引用了未登记 source_id: {', '.join(missing_ids)}")

        if row["status"] == "supported":
            supported_count += 1
            if not linked_ids:
                findings.fail(f"{prefix}: supported claim 缺少 source_ids。")
            if not row["locators"]:
                findings.fail(f"{prefix}: supported claim 缺少原文 locators。")
            if not row["variable_mapping"]:
                findings.fail(f"{prefix}: supported claim 缺少本题变量映射。")
            if not row["verification_test"]:
                findings.fail(f"{prefix}: supported claim 缺少 verification_test。")
            for source_id in linked_ids:
                source = sources.get(source_id)
                if source and source["citable"] != "yes":
                    findings.fail(f"{prefix}: supported claim 使用了不可引用来源 {source_id}。")

            strong_types = ("formula", "theorem", "algorithm", "model", "公式", "定理", "算法", "模型")
            if any(token in row["claim_type"].lower() for token in strong_types):
                full_text_sources = [
                    source_id
                    for source_id in linked_ids
                    if source_id in sources
                    and sources[source_id]["content_access"] == "full_text"
                    and sources[source_id]["source_tier"] in {"S1", "S2"}
                ]
                if not full_text_sources:
                    findings.fail(
                        f"{prefix}: 公式/定理/算法/模型 claim 缺少 S1/S2 全文证据。"
                    )

        if row["status"] in {"conditional", "pending"}:
            findings.warn(f"{prefix}: claim {claim_id} 仍为 {row['status']}。")

    if supported_count == 0:
        findings.fail("claim_evidence.csv 没有 supported claim。")

    for source_id, source in sources.items():
        for claim_id in split_multi(source["claim_ids"]):
            if claim_id not in claims:
                findings.fail(f"来源 {source_id} 指向不存在的 claim_id: {claim_id}")
            elif source_id not in split_multi(claims[claim_id]["source_ids"]):
                findings.fail(f"来源 {source_id} 与 claim {claim_id} 的双向映射不一致。")

    return claims


def validate_search_log(
    rows: list[dict[str, str]],
    sources: dict[str, dict[str, str]],
    findings: Findings,
    gate: str | None,
    research_mode: str,
) -> set[str]:
    if not rows:
        findings.fail("search_log.csv 没有检索记录。")
        return set()

    query_ids: set[str] = set()
    channels: set[str] = set()
    logged_sources: set[str] = set()
    round_impacts: dict[int, list[str]] = {}
    languages: set[str] = set()
    allowed_channels = SEARCH_CHANNELS | OPTIONAL_SEARCH_CHANNELS

    for number, row in enumerate(rows, start=2):
        prefix = f"search_log.csv 第 {number} 行"
        query_id = row["query_id"]
        if not query_id:
            findings.fail(f"{prefix}: query_id 为空。")
        elif query_id in query_ids:
            findings.fail(f"{prefix}: query_id 重复: {query_id}")
        query_ids.add(query_id)

        for field_name in (
            "search_round",
            "retrieved_at",
            "database",
            "channel",
            "language",
            "exact_query",
            "decision_impact",
        ):
            if not row[field_name]:
                findings.fail(f"{prefix}: {field_name} 为空。")
        try:
            search_round = int(row["search_round"])
            if search_round <= 0:
                raise ValueError
        except ValueError:
            findings.fail(f"{prefix}: search_round 必须是正整数。")
            search_round = -1
        impact = row["decision_impact"]
        if impact not in DECISION_IMPACT_VALUES:
            findings.fail(
                f"{prefix}: decision_impact 必须是 "
                "none/model/assumption/algorithm/validation/baseline/limitation。"
            )
        elif search_round > 0:
            round_impacts.setdefault(search_round, []).append(impact)

        language = row["language"].strip().lower()
        if language:
            languages.add(language)
        if row["retrieved_at"] and not is_date_like(row["retrieved_at"]):
            findings.fail(f"{prefix}: retrieved_at 应为 YYYY-MM-DD 或 ISO 日期时间。")
        if row["channel"] not in allowed_channels:
            findings.fail(f"{prefix}: 未知 channel: {row['channel']}")
        else:
            channels.add(row["channel"])

        try:
            scanned = int(row["result_count_scanned"])
            if scanned < 0:
                raise ValueError
        except ValueError:
            findings.fail(f"{prefix}: result_count_scanned 必须是实际扫描的非负整数。")

        included_ids = split_multi(row["included_source_ids"])
        for source_id in included_ids:
            logged_sources.add(source_id)
            if source_id not in sources:
                findings.fail(f"{prefix}: included_source_ids 含未登记来源 {source_id}。")

        high_value_ids = split_multi(row["new_high_value_source_ids"])
        for source_id in high_value_ids:
            if source_id not in sources:
                findings.fail(f"{prefix}: new_high_value_source_ids 含未登记来源 {source_id}。")
            if source_id not in included_ids:
                findings.fail(f"{prefix}: 高价值来源 {source_id} 未同时列入 included_source_ids。")
        if impact == "none" and high_value_ids:
            findings.fail(f"{prefix}: decision_impact=none 但记录了新增高价值来源。")
        if impact != "none" and not high_value_ids:
            findings.fail(f"{prefix}: decision_impact={impact} 但没有记录新增高价值来源。")

    missing_channels = sorted(SEARCH_CHANNELS - channels)
    if missing_channels and research_mode == "full":
        findings.fail(f"缺少强制检索通道: {', '.join(missing_channels)}")
    elif missing_channels:
        findings.warn(f"targeted 模式未覆盖通道（应在报告说明 N/A）: {', '.join(missing_channels)}")
    else:
        findings.ok("六个强制检索通道均有精确检索记录。")

    has_chinese = any(lang in {"zh", "cn", "chinese", "中文"} or lang.startswith("zh-") for lang in languages)
    has_english = any(lang in {"en", "english", "英文"} or lang.startswith("en-") for lang in languages)
    if not (has_chinese and has_english) and research_mode == "full":
        findings.fail("检索日志必须同时包含中文和英文检索记录。")
    elif not (has_chinese and has_english):
        findings.warn("targeted 模式仅使用了一种检索语言。")

    sorted_rounds = sorted(round_impacts)
    saturated = (
        len(sorted_rounds) >= 2
        and all(impact == "none" for round_id in sorted_rounds[-2:] for impact in round_impacts[round_id])
    )
    if research_mode == "full" and gate == "PASS" and not saturated:
        findings.fail("门禁为 PASS，但最后两个不同 search_round 并非全部 decision_impact=none。")
    elif saturated:
        findings.ok(f"最后两轮检索（{sorted_rounds[-2]}, {sorted_rounds[-1]}）满足可审计饱和条件。")
    else:
        findings.warn("尚未记录连续两个无决策影响的增量检索轮次。")

    for source_id, source in sources.items():
        if source["citable"] == "yes" and source_id not in logged_sources:
            findings.fail(f"可引用来源 {source_id} 未被任何 search_log 记录纳入。")

    return channels


def validate_role_coverage(
    sources: dict[str, dict[str, str]],
    report: str,
    findings: Findings,
) -> None:
    covered: set[str] = set()
    for source in sources.values():
        if source["citable"] == "yes":
            covered.update(split_multi(source["evidence_role"]))

    missing = []
    for role in sorted(EVIDENCE_ROLES - covered):
        if role_is_na(report, role):
            findings.warn(f"证据角色 {role} 标为 N/A；需人工核对理由。")
        else:
            missing.append(role)
    if missing:
        findings.fail(f"缺少可引用来源覆盖的证据角色: {', '.join(missing)}")
    else:
        findings.ok("证据角色均被来源覆盖或显式标为 N/A。")


def extract_paper_citations(text: str) -> tuple[set[str], set[str]]:
    citation_keys: set[str] = set()
    bibliography_keys: set[str] = set()

    latex_cite_commands = (
        "cite|citep|citet|parencite|textcite|autocite|footcite|smartcite|supercite"
    )
    latex_pattern = rf"\\(?:{latex_cite_commands})(?:\[[^\]]*\]){{0,2}}\{{([^}}]+)\}}"
    for group in re.findall(latex_pattern, text):
        citation_keys.update(key.strip() for key in group.split(",") if key.strip())
    bibliography_keys.update(
        key.strip()
        for key in re.findall(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}", text)
        if key.strip()
    )
    for arguments in re.findall(r"#cite\(([^)]*)\)", text, flags=re.S):
        citation_keys.update(key.strip() for key in re.findall(r"<([^>]+)>", arguments) if key.strip())
    for group in re.findall(r"#super\(\s*[\"']\s*\[([^\]]+)\]", text):
        citation_keys.update(re.findall(r"\d+", group))
    return citation_keys, bibliography_keys


def validate_paper(
    root: Path,
    paper_arg: str,
    sources: dict[str, dict[str, str]],
    findings: Findings,
) -> None:
    paper_dir = Path(paper_arg)
    if not paper_dir.is_absolute():
        paper_dir = root / paper_dir
    if not paper_dir.is_dir():
        findings.fail(f"论文目录不存在: {paper_dir}")
        return

    map_rows = read_csv_rows(paper_dir / "reference_map.csv", MAP_FIELDS, findings)
    if not map_rows:
        findings.fail("paper/reference_map.csv 没有引用映射记录。")
        return

    text_files = sorted(list(paper_dir.rglob("*.tex")) + list(paper_dir.rglob("*.typ")))
    if not text_files:
        findings.fail(f"论文目录中没有 .tex 或 .typ 文件: {paper_dir}")
        return
    chunks = []
    for path in text_files:
        try:
            chunks.append(path.read_text(encoding="utf-8-sig"))
        except (OSError, UnicodeError) as exc:
            findings.fail(f"无法读取论文文件 {path}: {exc}")
    paper_text = "\n".join(chunks)

    for pattern in PLACEHOLDER_PATTERNS:
        if re.search(pattern, paper_text, flags=re.I):
            findings.fail(f"论文或参考文献仍含占位内容: {pattern}")

    citation_keys, bibliography_keys = extract_paper_citations(paper_text)
    mapping: dict[str, dict[str, str]] = {}
    mapped_source_ids: set[str] = set()
    for number, row in enumerate(map_rows, start=2):
        prefix = f"reference_map.csv 第 {number} 行"
        key = row["citation_key"]
        source_id = row["source_id"]
        rendered = row["rendered_reference"]
        if not key or not source_id or not rendered:
            findings.fail(f"{prefix}: citation_key/source_id/rendered_reference 均不得为空。")
            continue
        if key in mapping:
            findings.fail(f"{prefix}: citation_key 重复: {key}")
        mapping[key] = row
        mapped_source_ids.add(source_id)

        source = sources.get(source_id)
        if not source:
            findings.fail(f"{prefix}: source_id 未在 registry 登记: {source_id}")
            continue
        if source["citable"] != "yes" or source["identity_status"] != "verified":
            findings.fail(f"{prefix}: source_id {source_id} 未核验或不可引用。")
        title_norm = normalized_text(source["title"])
        rendered_norm = normalized_text(rendered)
        probe = title_norm[: min(16, len(title_norm))]
        if probe and probe not in rendered_norm:
            findings.fail(f"{prefix}: rendered_reference 与登记题名不匹配。")
        if rendered_norm and rendered_norm not in normalized_text(paper_text):
            findings.warn(f"{prefix}: rendered_reference 未能在论文参考文献文本中精确定位。")

    for key in sorted(citation_keys - set(mapping)):
        findings.fail(f"正文引用键未出现在 reference_map.csv: {key}")
    for key in sorted(bibliography_keys - set(mapping)):
        findings.fail(f"参考文献键未出现在 reference_map.csv: {key}")
    for key in sorted(set(mapping) - citation_keys):
        findings.warn(f"reference_map.csv 中的引用键未在正文使用: {key}")

    adopted_ids = {source_id for source_id, row in sources.items() if row["adopted"] == "yes"}
    unused_adopted = adopted_ids - mapped_source_ids
    if unused_adopted:
        findings.warn(f"已采用来源未进入论文引用映射: {', '.join(sorted(unused_adopted))}")

    if not citation_keys:
        findings.fail("没有识别到正文引用标记（LaTeX cite、Typst cite 或 #super）。")
    else:
        findings.ok(f"识别到 {len(citation_keys)} 个正文引用键并检查映射。")


def print_findings(findings: Findings, strict: bool) -> None:
    for message in findings.passes:
        print(f"[PASS] {message}")
    for message in findings.warnings:
        print(f"[WARN] {message}")
    for message in findings.errors:
        print(f"[FAIL] {message}")
    status = "FAIL" if findings.errors or (strict and findings.warnings) else "PASS"
    print(
        f"[{status}] summary: {len(findings.errors)} error(s), "
        f"{len(findings.warnings)} warning(s), {len(findings.passes)} passed check(s)."
    )


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate literature search, evidence, and citation traceability for a math-modeling project."
    )
    parser.add_argument("root", nargs="?", default=".", help="Project root (default: current directory).")
    parser.add_argument(
        "--paper-dir",
        help="Optional paper directory, absolute or relative to root; enables citation-map checks.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return failure when any warning remains; recommended for final verification.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = parse_args(argv or sys.argv[1:])
    root = Path(args.root).expanduser().resolve()
    findings = Findings()

    if not root.is_dir():
        findings.fail(f"项目根目录不存在: {root}")
        print_findings(findings, args.strict)
        return 1

    report, gate, research_mode = validate_report(
        root / "reports" / "LITERATURE_RESEARCH_REPORT.md", findings
    )
    registry_rows = read_csv_rows(
        root / "references" / "literature_registry.csv", REGISTRY_FIELDS, findings
    )
    search_rows = read_csv_rows(root / "references" / "search_log.csv", SEARCH_FIELDS, findings)
    claim_rows = read_csv_rows(root / "references" / "claim_evidence.csv", CLAIM_FIELDS, findings)

    sources = validate_registry(registry_rows, findings)
    validate_claims(claim_rows, sources, findings)
    validate_search_log(search_rows, sources, findings, gate, research_mode)
    validate_role_coverage(sources, report, findings)

    if args.paper_dir:
        validate_paper(root, args.paper_dir, sources, findings)

    print_findings(findings, args.strict)
    return 1 if findings.errors or (args.strict and findings.warnings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
