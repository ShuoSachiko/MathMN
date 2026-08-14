#!/usr/bin/env python3
"""Validate Typst/LaTeX template parity and LaTeX include targets.

Two validation levels are supported:

* ``--check`` (default): static parity — every Typst family has a LaTeX
  counterpart and every LaTeX ``\\input``/``\\include`` target exists.
* ``--compile-check``: compile every Typst ``main.typ`` with ``typst`` and every
  LaTeX ``main.tex`` with ``xelatex`` (two passes), classifying each family as
  PASS / FAIL (template error) / ENV (missing LaTeX package) / SKIP (compiler
  absent).  The exit code is 1 only on template-level compile errors; an
  all-SKIP run returns 0 so CI without a compiler does not go red.

The legacy ``--compile`` / ``--compile-all`` LaTeX-only smoke compile is kept
for backward compatibility.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections import Counter
from pathlib import Path


INPUT_PATTERN = re.compile(r"\\(?:input|include)\{([^}]+)\}")

# LaTeX log markers: a missing file (package/class/font definition) is an
# environment problem, not a template error, so it maps to ENV rather than FAIL.
MISSING_FILE_RE = re.compile(
    r"! LaTeX Error: File [`']([^`']+\.(?:sty|cls|def|fd|cfg|clo))['`] not found",
    re.MULTILINE,
)
ERROR_MARKER_RE = re.compile(
    r"!(?: LaTeX Error| Undefined control sequence| Emergency stop| Illegal parameter)",
    re.MULTILINE,
)


def template_pairs(root: Path) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for language in ("zh", "en"):
        language_root = root / language
        for typst_dir in sorted(
            path
            for path in language_root.iterdir()
            if path.is_dir() and not path.name.endswith("-latex")
        ):
            pairs.append((typst_dir, language_root / f"{typst_dir.name}-latex"))
    return pairs


def validate_static(root: Path) -> list[str]:
    failures: list[str] = []
    pairs = template_pairs(root)
    if len(pairs) != 17:
        failures.append(f"expected 17 Typst template families, found {len(pairs)}")

    for typst_dir, latex_dir in pairs:
        if not (typst_dir / "main.typ").is_file():
            failures.append(f"missing Typst entry: {typst_dir / 'main.typ'}")
        main_tex = latex_dir / "main.tex"
        if not main_tex.is_file():
            failures.append(f"missing LaTeX counterpart: {main_tex}")
            continue

        source = main_tex.read_text(encoding="utf-8")
        for relative in INPUT_PATTERN.findall(source):
            candidate = latex_dir / relative
            if candidate.suffix == "":
                candidate = candidate.with_suffix(".tex")
            if not candidate.is_file():
                failures.append(f"missing include target: {candidate}")
    return failures


def compile_template(template: Path, timeout: int) -> tuple[bool, str]:
    """Legacy in-place LaTeX smoke compile used by ``--compile`` / ``--compile-all``."""
    command = [
        "xelatex",
        "-interaction=nonstopmode",
        "-halt-on-error",
        "main.tex",
    ]
    for _ in range(2):
        try:
            completed = subprocess.run(
                command,
                cwd=template,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return False, "xelatex is not installed"
        except subprocess.TimeoutExpired:
            return False, f"xelatex exceeded {timeout} seconds"
        if completed.returncode != 0:
            output = (completed.stdout + "\n" + completed.stderr)[-4000:]
            return False, output
    pdf = template / "main.pdf"
    return pdf.is_file() and pdf.stat().st_size > 0, str(pdf)


def which_tool(name: str) -> bool:
    return shutil.which(name) is not None


def _make_writable_tmp() -> Path:
    """Create a temp dir that actually accepts file writes.

    Some sandboxes mark ``tempfile.mkdtemp`` directories so that nested writes
    are denied, and expose a system temp dir where file creation fails.  Create
    the directory with ``Path.mkdir`` instead, probe it, and fall back to the
    current working directory when the system temp is unusable.
    """

    def try_at(parent: Path) -> Path | None:
        try:
            path = parent / f"validate_templates_{uuid.uuid4().hex[:12]}"
            path.mkdir(parents=False)
            probe = path / ".probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            return path
        except OSError:
            return None

    for parent in (Path(tempfile.gettempdir()), Path.cwd()):
        path = try_at(parent)
        if path is not None:
            return path
    raise RuntimeError("cannot create a writable temp directory")


def compile_typst_check(typst_dir: Path, out_dir: Path, key: str, timeout: int) -> tuple[str, str]:
    """Compile a Typst entry to a temp dir; return (status, detail)."""
    if not which_tool("typst"):
        return "SKIP", "typst 编译器未安装"
    out = out_dir / f"{key}.pdf"
    try:
        completed = subprocess.run(
            ["typst", "compile", str(typst_dir / "main.typ"), str(out)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return "SKIP", "typst 编译器未安装"
    except subprocess.TimeoutExpired:
        return "FAIL", f"typst 编译超时（>{timeout}s）"
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-1500:]
        return "FAIL", detail
    return "PASS", f"生成 {out.name}"


def compile_latex_check(latex_dir: Path, work_root: Path, key: str, timeout: int) -> tuple[str, str]:
    """Run xelatex twice in a temp copy and classify the outcome.

    A missing package/class/font definition is an environment problem (ENV), not
    a template error; only genuine LaTeX errors map to FAIL.
    """
    if not which_tool("xelatex"):
        return "SKIP", "xelatex 编译器未安装"
    work = work_root / key
    shutil.copytree(latex_dir, work)
    last = None
    for _ in range(2):
        try:
            last = subprocess.run(
                ["xelatex", "-interaction=nonstopmode", "main.tex"],
                cwd=work,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return "SKIP", "xelatex 编译器未安装"
        except subprocess.TimeoutExpired:
            return "FAIL", f"xelatex 编译超时（>{timeout}s）"
    pdf = work / "main.pdf"
    if last is not None and last.returncode == 0 and pdf.is_file() and pdf.stat().st_size > 0:
        return "PASS", "两遍编译通过"

    log_text = ""
    log = work / "main.log"
    if log.is_file():
        log_text = log.read_text(encoding="utf-8", errors="replace")
    missing = MISSING_FILE_RE.findall(log_text)
    if missing:
        names = ", ".join(sorted({name for name in missing}))
        return "ENV", f"环境缺宏包/文件: {names}"
    marker = ERROR_MARKER_RE.search(log_text)
    if marker:
        idx = log_text.find(marker.group(0))
        context = " ".join(log_text[idx : idx + 240].split())
        return "FAIL", context
    tail = " ".join(((last.stdout if last else "") + "\n" + (last.stderr if last else "")).split())
    return "FAIL", (tail[-400:] or "编译失败（未生成 PDF）")


def run_compile_check(root: Path, timeout: int) -> int:
    typst_ok = which_tool("typst")
    xelatex_ok = which_tool("xelatex")
    print(f"compile-check: typst={'可用' if typst_ok else '缺失'}, xelatex={'可用' if xelatex_ok else '缺失'}")

    pairs = template_pairs(root)
    tmp = _make_writable_tmp()
    rows: list[tuple[str, str, str, str]] = []
    try:
        for typst_dir, latex_dir in pairs:
            family = str(typst_dir.relative_to(root))
            key = family.replace("\\", "_").replace("/", "_")
            status, detail = compile_typst_check(typst_dir, tmp, key, timeout)
            rows.append(("typst", family, status, detail))
            status, detail = compile_latex_check(latex_dir, tmp, key, timeout)
            rows.append(("latex", family, status, detail))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    for engine, family, status, detail in rows:
        suffix = f": {detail}" if detail else ""
        print(f"{status:4s} {engine} {family}{suffix}")

    counts = Counter(status for _, _, status, _ in rows)
    print("Summary: " + ", ".join(f"{key}={counts.get(key, 0)}" for key in ("PASS", "FAIL", "ENV", "SKIP")))
    template_errors = counts.get("FAIL", 0)
    if template_errors:
        print(f"FAIL: {template_errors} 个模板级编译错误")
        return 1
    if counts.get("PASS", 0) == 0 and counts.get("ENV", 0) == 0:
        print("SKIP: 无可编译模板（编译器缺失）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--check",
        action="store_true",
        help="Static parity check only (default when no action flag is given).",
    )
    parser.add_argument(
        "--compile-check",
        action="store_true",
        help="Compile every Typst and LaTeX template family and classify PASS/FAIL/ENV/SKIP.",
    )
    parser.add_argument(
        "--compile",
        action="append",
        default=[],
        metavar="LANG/NAME",
        help="Compile a LaTeX family such as zh/cumcm or en/mcm",
    )
    parser.add_argument(
        "--compile-all",
        action="store_true",
        help="Compile all 17 LaTeX template families twice",
    )
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1] / "templates"
    failures = validate_static(root)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1

    print("PASS static parity: 17 Typst families and 17 LaTeX counterparts")

    if args.compile_check:
        return run_compile_check(root, args.timeout)

    compile_keys = list(args.compile)
    if args.compile_all:
        compile_keys.extend(
            str(latex_dir.relative_to(root)).removesuffix("-latex")
            for _, latex_dir in template_pairs(root)
        )
    compile_keys = list(dict.fromkeys(compile_keys))
    compile_failures = 0
    for key in compile_keys:
        template = root / f"{key}-latex"
        if not template.is_dir():
            print(f"FAIL unknown template: {key}")
            compile_failures += 1
            continue
        ok, details = compile_template(template, args.timeout)
        print(f"{'PASS' if ok else 'FAIL'} compile {key}: {details}")
        if not ok:
            compile_failures += 1
    return 1 if compile_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
