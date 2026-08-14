#!/usr/bin/env python3
"""Remove bibliography examples from reusable 5writing templates.

The sentinel comments keep templates compilable while making an unfilled
bibliography fail the final literature-bundle validator.
"""

from __future__ import annotations

import argparse
from pathlib import Path


TYPST_SENTINEL = """// REFS_NOT_READY: reusable template intentionally contains no bibliography entries.
// Replace this entire file only with identity_status=verified and citable=yes
// sources from references/literature_registry.csv, and keep paper/reference_map.csv
// synchronized with every rendered entry and in-text citation.
"""

LATEX_SENTINEL = r"""% REFS_NOT_READY: reusable template intentionally contains no bibliography entries.
% Replace this entire file with a thebibliography environment containing only
% identity_status=verified and citable=yes registry sources. Use source_id as
% each bibitem key and synchronize paper/reference_map.csv.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template_root", type=Path)
    parser.add_argument("--check", action="store_true", help="Check only; do not rewrite files.")
    args = parser.parse_args()

    root = args.template_root.resolve()
    typst_files = sorted(root.rglob("references.typ"))
    latex_files = sorted(root.rglob("references.tex"))
    if len(typst_files) != 17 or len(latex_files) != 17:
        raise SystemExit(
            f"Refusing unexpected template set: {len(typst_files)} Typst and "
            f"{len(latex_files)} LaTeX reference files (expected 17 each)."
        )

    changed = []
    for path, expected in [
        *((path, TYPST_SENTINEL) for path in typst_files),
        *((path, LATEX_SENTINEL) for path in latex_files),
    ]:
        current = path.read_text(encoding="utf-8-sig")
        if current != expected:
            changed.append(path)
            if not args.check:
                path.write_text(expected, encoding="utf-8", newline="\n")

    if args.check and changed:
        for path in changed:
            print(f"[FAIL] unsanitized: {path}")
        return 1

    action = "checked" if args.check else "sanitized"
    print(f"[PASS] {action} {len(typst_files)} Typst and {len(latex_files)} LaTeX reference files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
