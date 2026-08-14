# MathModelAgent repository guidance

## Primary workflow

- Treat Codex skills as the primary product surface. Keep Claude Code compatibility, but do not make new workflows depend on Claude-only tools or frontmatter.
- Canonical skill sources live in `skills/`. Run `powershell -ExecutionPolicy Bypass -File scripts/setup-codex.ps1` once after cloning so Codex discovers them through `.agents/skills`.
- Start a modeling task from a directory under `workspaces/<competition>/` and invoke `$1start-mathmodel`. This keeps generated papers, data, and logs inside the repository without mixing them with source files.
- Support both Python and MATLAB throughout planning, coding, paper appendices, and verification. For MATLAB, prefer the installed MATLAB CLI and use GNU Octave only for compatible code when MATLAB is unavailable.

## Modeling integrity

- Treat the declared problem statement, rules, and original attachments as the only task facts. Hash an explicit input allowlist before modeling; do not silently read old answers, prior workspaces, official reviews, or target values.
- Freeze a machine-readable problem contract before coding. Track every command-like requirement with a ReqID, including unnumbered subquestions, required workbooks, units, precision, time points, and submission locations.
- Separate fixed quantities, decision variables, state variables, derived quantities, and solver controls. A numerically attractive solution that changes a fixed quantity answers a different problem.
- Validate semantics and numerics independently. Same-source JSON, workbook, figure, and paper agreement proves pipeline consistency only.
- Never call a result globally optimal, causal, exact, robust, or independently blind unless the claim ledger contains evidence at that strength.
- Historical public problems are contamination-sensitive regression tests, not proof of performance on unseen future problems. Do not place concrete historical answers or answer-derived fixtures in reusable skills or tests.
- Keep task-type checks modular. Core gates enforce source, contract, evidence, reproducibility, and provenance; optimization, prediction, simulation, ranking, and statistics add only applicable validation profiles.

## Human supervision and versions

- `live-competition` uses `human-supervised` review. An agent may prepare evidence but must never fabricate or self-approve human checkpoints.
- Require explicit human review of intake, contract, model, results, paper, and final submission. Autonomous simulations may waive these only with `WAIVED_FOR_SIMULATION`; such a run is not competition-ready.
- Preserve indecision as history instead of overwriting it. Snapshot material alternatives per ReqID/module, keep branches content-addressed, and append every selection event with actor, evidence, and rationale.
- Changing a selected task version, problem input, frozen contract, code, or claim ledger invalidates downstream hashes. Mark dependent gates `STALE` and rerun them.
- Human confirmation cannot turn an unexecuted or failed technical check into PASS. Use `UNVERIFIED` when an applicable runtime, visual review, full reproduction, or human checkpoint was not performed.

## Source conventions

- Skill frontmatter contains only `name` and `description`.
- Keep reusable, deterministic operations in `scripts/`; keep templates and output boilerplate in `assets/` or the existing template tree.
- Avoid modifying generated shadcn-vue components under `frontend/src/components/ui/`; when build compatibility requires it, keep the fix type-only and minimal.
- Never commit API keys, `backend/.env.dev`, local runtimes, dependency folders, generated contest workspaces, or compiled template artifacts.

## Validation

- Skill metadata: `python C:/Users/<user>/.codex/skills/.system/skill-creator/scripts/quick_validate.py <skill-dir>` when the built-in validator is available.
- LaTeX templates: `python skills/5writing/scripts/validate_templates.py`.
- MATLAB runner: `python skills/3coding-visual/scripts/matlab_runner.py --check`.
- Integrity scripts: run the stdlib unit tests under `skills/1start-mathmodel/scripts/`, `skills/1start-mathmodel/tests/`, `skills/2analysis-modeling/tests/`, `skills/3coding-visual/scripts/`, `skills/3coding-visual/tests/`, `skills/6verity/tests/`, and `skills/7benchmark-mathmodel/tests/`.
- Shell text gate: `bash -n skills/6verity/scripts/writing_check.sh`.
- Generic forward tests must use synthetic tasks from more than one problem family and include both reject and accept cases. They must not encode a real contest answer.
- Backend: `backend/.venv/Scripts/python.exe -m ruff check app` from `backend/`.
- Frontend: run the repository-local pnpm at `.runtime/pnpm/node_modules/.bin/pnpm.cmd --dir frontend run build` after `scripts/setup-local.ps1`.
