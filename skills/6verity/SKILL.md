---
name: 6verity
description: "数学建模竞赛最终验收阶段。逐 ReqID 核对原题语义、文献证据和计算结论，验证来源与引用追溯、输入/合同/运行哈希、Python 或 MATLAB/Octave 干净复现、表格图表、Typst/LaTeX 编译和 PDF 视觉质量，并给出 PASS/FAIL/UNVERIFIED。"
---

# 验证和验收（Typst / LaTeX）

本 skill 是完整工作流的最后一关。它不重新建模、不生成新结果、不代替写作阶段重写论文；它负责发现硬错误、修复可直接修复的问题，并输出 `reports/VERIFY_REPORT.md`。

## 数学建模规范参考

必须读取 `../_references/modeling_integrity.md` 的“两条验证链”“结论—证据账本”“封存、复盘”和“最低硬门禁”；如需领域判断，再读取 `../_references/math_modeling_norms.md` 中的“论文验收与一致性”小节。

## 阶段边界

- 本阶段负责：逐项题意验收、证据与哈希完整性、独立数值验证、结构验收、文本门禁、提交表格和图表检查、代码干净复现、Typst/LaTeX 编译、PDF 视觉检查、提交清单。
- 本阶段不负责：重新设计模型、重新跑大规模实验、重新组织整篇论文。
- 发现硬错误时，优先做小范围修复；如果需要回到前序阶段，写入 `reports/VERIFY_REPORT.md` 并标记为未通过。
- 本阶段不得读取官方答案或优秀论文来修正当前提交。`retrospective-audit` 的外部对照只能在本阶段完成并封存后，由 `7benchmark-mathmodel` 或独立审计上下文执行。

## 输入

由模型先根据当前工作区判断项目布局，再把实际路径传给检查脚本。常见输入包括但不限于：

1. 论文入口文件：`main.typ`（Typst）或 `main.tex`（LaTeX）。
2. 正文章节目录或若干正文文件（`.typ` 或 `.tex`）。
3. 参考文献文件（`references.typ` 或 `references.tex`）。
4. `reports/LITERATURE_RESEARCH_REPORT.md` 与 `references/literature_registry.csv`、`search_log.csv`、`claim_evidence.csv`。
5. `paper/reference_map.csv`，记录正文引用键到已核验 `source_id` 的映射。
6. 前序阶段的分析、建模、结果、图示报告。
7. 原始题面白名单、题意合同、阶段门禁、来源记录和交接文件。
8. claim ledger、validation manifest、run manifest 和论文追踪矩阵。
9. 图表目录和比赛要求的 Excel/CSV 等提交文件。
10. 可复现代码目录。
11. 编译后的 PDF，或可由入口文件编译得到的输出 PDF。

不要假设论文目录一定叫 `paper/`，也不要假设结果文件一定在项目根。若项目使用不同命名，按实际结构传参并在 `reports/VERIFY_REPORT.md` 中说明。

## 工作流程

### Step 0: 完整性与逐 ReqID 语义门禁

若 literature gate 为 `required=true`，先运行：

```bash
python "<本 skill>/scripts/integrity_check.py" \
  --root . --json
```

把 JSON stdout 原样保存为 `reports/integrity_check.json`。脚本检查输入/合同/产物哈希、阶段陈旧、原子要求覆盖、固定量—决策量漂移、结论证据、验证配置和论文映射。脚本 `FAIL` 是硬错误，退出码为 `UNVERIFIED` 时最终也不能写 `PASS`。机器 schema 只能发现缺失和矛盾，不能替代下面的独立语义审查。

同时检查 `CURRENT_VERSIONS.json` 与只追加选择日志：每个被论文/结果依赖的 task 都固定到选中版本，父版本和对象可物化，stage gate 记录的版本索引哈希为当前值。候选分支不得混入最终 run manifest；最近一次 select 必须有对应人工审查引用，模拟豁免则总体至多 `UNVERIFIED`。

逐 `REQ-*` 回到原题和附件，建立验收表：

| ReqID | 原题动作/对象 | 固定量与允许自由度 | 必交输出/单位/精度 | 论文最终回答 | 证据与独立验证 | 结果 |
| --- | --- | --- | --- | --- | --- | --- |

逐条回答：

1. 实际模型是否恰好回答原题对象、时域、样本和情景，而不是相邻问题？
2. 代码和结论是否改变了合同中的固定量，或增加了未授权自由度？
3. 未编号小问、指定时点、模板单元格和提交文件是否全部覆盖？
4. 结论强度是否不高于账本；全局、因果、精确、稳健等词是否有相应证据？
5. 重大歧义是否已由原文证据解决或分支报告？

任何一条关键要求失败都必须 `FAIL`，即使论文、JSON、Excel 和图表彼此数值一致。有多 Agent 时，语义审查者只接收原始题面、合同和候选提交，不给官方答案或主解辩护。

### Step 1: 运行文献证据与文本质量门禁

先运行：

```bash
python "<mathmodel-literature-research skill>/scripts/validate_literature_bundle.py" \
  "$ROOT_DIR" --paper-dir "$PAPER_DIR" --strict \
  | tee _tmp/literature_bundle_check.log
```

该命令检查检索通道、来源登记、claim 定位、证据角色和正文引用映射；对已触发的文献门禁，退出码非零即为硬错误。未触发时本项记 N/A，不得反过来要求临时补一套形式化文献包。脚本不联网，因此已登记来源仍要人工抽查，不能仅因 CSV 写了 `verified` 就认定真实。

随后运行本 skill 的文本脚本。脚本按入口文件扩展名自动选择检查逻辑（`.typ` → Typst 检查，`.tex` → LaTeX 检查）：

```bash
set -o pipefail
mkdir -p _tmp
SCRIPT_PATH="<按当前 skill 实际位置确定>/scripts/writing_check.sh"
bash "$SCRIPT_PATH" \
  --paper-dir "$PAPER_DIR" \
  --main "$MAIN_FILE" \
  --sections-dir "$SECTIONS_DIR" \
  --references "$REFERENCES_FILE" \
  --figures-dir "$FIGURES_DIR" \
  --results-file "$RESULTS_FILE" \
  --problem-analysis "$PROBLEM_ANALYSIS_FILE" \
  --all-results "$ALL_RESULTS_FILE" \
  | tee _tmp/writing_check.log
```

如果本 skill 被复制到其他目录，使用实际脚本路径。可以先运行 `bash <script> --help` 查看参数。不要把脚本路径、论文目录或文件名写死在验收逻辑中。

`writing_check.sh` 只扫描论文文本，不能替代证据包校验器。任一脚本的 `FAIL` 都属于硬错误，必须修复后重跑。

### Step 2: 章节数量和标题顺序

**Typst 引擎**检查：

- 入口 `.typ` 文件中 `#include("...")` 的数量是否与实际正文结构匹配。
- include 顺序是否符合文件名前缀顺序，例如 `1_...`, `2_...`, `3_...`。
- 每个 section 是否有明确一级标题（`= 标题`，等号后有空格）。
- 标题顺序是否符合所选论文类型。

**LaTeX 引擎**检查：

- 入口 `.tex` 文件中 `\input{...}` 或 `\include{...}` 的数量是否与实际正文结构匹配。
- 章节顺序是否符合文件名前缀顺序。
- 每个 section 是否有 `\section{}` 或对应级别标题。

通用检查（两种引擎）：

- 章节文件是否缺失、重复引用、未被引用。
- 如果题目不是三问，不强行要求三段问题章节；按 `ANALYSIS_MODELING_REPORT.md` 的子问题数量核对。

### Step 3: 图表和章节匹配

**Typst 引擎**检查：

- 图表目录中的 PDF 是否在正文中被引用。
- `#figure(image(...), caption: [...])` 的图片是否真实存在。图片路径必须相对于 `.typ` 文件。
- 数据图是否放在对应结果/分析章节，非数据流程图是否放在方法/总体思路章节。

**LaTeX 引擎**检查：

- `\includegraphics{}` 引用的图片文件是否真实存在。路径相对于 `.tex` 文件。
- `\caption{}` 是否存在。
- 数据图是否放在对应结果/分析章节。

通用检查（两种引擎）：

- 连续图表之间是否有足够解释文字。
- caption 是否过长、过泛或与图意不一致。
- 图表编号、正文引用和章节语义是否一致。

不要生成 `*_typst_includes.typ` 或 `*_latex_includes.tex`；图表必须直接嵌在对应 section 中。

### Step 4: 写作质量和泄露检查

检查并修复：

- `TODO`、`PLACEHOLDER`、`待补充`、`待续写`、`示例数据` 等占位符。
- 论文正文出现内部工作流文件名、临时目录名、代码目录名或结果 JSON 路径。
- 过多列表式写作（Typst 中大量 `#list`、`enum`，LaTeX 中大量 `\begin{itemize}`、`\begin{enumerate}`）。
- 段落反复以"如图""由图""图 X 展示了"开头。
- 图表后没有解释、公式后没有变量含义、结论只报数不解释。

### Step 5: 数值和结果一致性

检查：

- 论文中的每个关键数值必须按 claim ID 来自 `claim_ledger.json`，而不是“任意一个结果文件里出现过”。
- 逐 key 检查值、类型、单位、符号、容差、舍入规则、排名、权重、阈值和置信区间；任何缺失或冲突均为 FAIL。
- `PAPER_TRACEABILITY.json` 中每个 supported key 必须能定位到正文；论文中的新数值或新强结论也必须反向找到 claim。
- 公式中的符号应在符号说明或正文首次出现处解释。

发现数值冲突时，不要自行发明新结果；应回到结果记录或代码输出修正论文。

同源产物全都相等仍不构成正确性。至少复核 validation manifest 中一项独立 oracle/极限/变形/留出验证的实际证据，并确认它不是简单读取生产者生成的中间文件。

### Step 6: 引用和模板规范

检查：

- literature gate 是否为 `PASS`，evidence bundle 和 literature 人工 checkpoint 是否当前且未陈旧。
- 每个正文引用标记（Typst 的 `#cite`/`#super`，LaTeX 的 `\cite{}`）是否都出现在 `paper/reference_map.csv`，并映射到 registry 中 `verified + citable` 的来源。
- 参考文献列表是否存在映射外条目、搜索结果页、S4 发现线索、题名/作者/DOI 猜测、模板占位、`REFS_NOT_READY` 或未核验示例。
- 外部公式、定理、算法、参数和评价指标是否在实际使用处引用，并能回到 `claim_evidence.csv` 的 `claim_id`、原文定位、变量映射和适用边界。
- 人工抽查全部核心来源和适量非核心来源的稳定页面与原文定位；把抽查比例、对象和结果写入验收报告，不能只核对参考文献格式。
- 中文论文 caption、表题、摘要语言保持中文；英文论文保持英文。
- 选定的模板入口是否保留所选比赛模板的必要封面、摘要、编号、页眉页脚或提交格式。
- 不要把模板结构误删成普通空白文档。


### Step 7: 编译

**Typst 编译**：

```bash
command -v typst >/dev/null 2>&1 && typst compile "$MAIN_FILE" "$OUTPUT_PDF"
```

**LaTeX 编译**：

```bash
command -v xelatex >/dev/null 2>&1 && xelatex -interaction=nonstopmode "$MAIN_FILE" && xelatex -interaction=nonstopmode "$MAIN_FILE"
```

xelatex 需跑两遍解决目录和交叉引用。

编译失败必须修复语法、路径、图片引用或模板问题后重跑。编译通过后确认输出 PDF 非空。

### Step 7.5: 代码复现检查

读取 `plan.md` 的编程语言，并检查 `code/` 中的扩展名、论文附录语言标记和实际运行方式一致。

- 在空的 staging 输出目录从原始白名单输入重跑，不能先加载旧 MAT/JSON/图片；开始时间前的产物或未在新 run manifest 中声明的产物不得计入成功。
- Python：在项目声明的虚拟环境中从项目根运行主脚本，检查退出码、`RESULT` schema、预期产物新鲜度和图表是否可重建。
- MATLAB：运行 `python <3coding-visual skill>/scripts/matlab_runner.py --check`，再用同一脚本以 `--require-result` 和逐项 `--expected-artifact` 执行每个主 `.m` 文件。必须记录实际 runtime（MATLAB/Octave）、版本、工具箱依赖、退出码、独立偏好目录和日志路径。
- 不要仅凭代码“看起来正确”判定可复现。若完整求解耗时过长，至少运行数据读取、核心函数和小规模固定种子 smoke test，并把未覆盖范围标为 WARN。
- 若用户选择 MATLAB 而运行时可用但 `.m` 主程序执行失败，判定 FAIL；运行时不可用则明确记录为未验证，不能写成已通过。

重跑后重新生成 run manifest 并与封存清单逐 key 比较。允许的浮点差异必须有容差和随机性说明；缺产物、旧产物、源码哈希变化或失败后仍沿用 canonical 输出均为 FAIL。只完成 smoke test 的总体结论是 `UNVERIFIED`，不是 PASS with warning。

### Step 7.6: 提交表格与模板验收

若题目要求 Excel/CSV 等指定文件，按原始空白模板和题意合同建立 schema，独立回读并检查：

- sheet 名称与顺序、标签、合并/隐藏单元格、公式、数字格式和不可改模板结构；
- 每个必填/应空单元格、行列对象、单位、有限性、范围和小数位；
- 每个目标单元格逐 key 映射到 claim ledger，而不是只核对数值块大小；
- 不得新增隐藏 sheet、额外内容或把值写到错误行列；
- 用非生产路径的读取器复核；可视化渲染关键 sheet 检查截断、错位和乱码。

没有提交表格要求时记为 N/A；有要求但缺模板、独立读取器或视觉复核时为 `UNVERIFIED`，结构/值/位置错误为 `FAIL`。

对 `.xlsx` 提交物，先按冻结合同建立逐 sheet、逐单元格 schema，再运行纯标准库检查器；它校验结构和缓存值，但明确不替代渲染后的视觉复核：

```bash
python "<本 skill>/scripts/validate_workbook.py" \
  --workbook submission.xlsx --schema reports/submission_workbook_schema.json \
  --output reports/submission_workbook_validation.json
```

检查器的 `visual_status` 为 `UNVERIFIED` 属于预期提醒，只有人工或独立渲染检查完成并留下证据后，工作簿视觉门禁才可通过。

### Step 7.7: 提交包验收（CUMCM 等）

对 CUMCM 等需要打包上传的竞赛，在 Step 8/9 前用纯标准库脚本做通用结构校验 + 文件哈希 + 命名提示，不硬编码某一届的命名规则：

```bash
python "<本 skill>/scripts/validate_submission_bundle.py" "$ROOT_DIR" [--config "$CONFIG"] [--json]
```

默认读取 `<root>/reports/submission_bundle_config.json`；缺省等价于：

```json
{
  "required_files": [],
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
```

脚本逐项输出 PASS/FAIL/N/A 并写入两份报告：

- `reports/submission_bundle_validation.json`（机器可读，含论文 MD5）
- `reports/submission_bundle_validation.txt`（人类可读，逐项结论 + 建议）

检查内容：必交文件存在且非空（`required_files` 可含 glob）；论文 PDF 存在/非空/大于大小下限并给出 MD5；支撑材料 zip 可解压且 CRC 通过、不含 `.pdf` 论文类条目（当 `forbid_pdf_in_zip`）、含 `code/` 相关条目（当 `require_code_entries`）；承诺书文件存在且非空（当配置了 `commitment_file`）；`AI_USAGE_LOG.jsonl` 每行合法 JSON 且 `time`/`tool`/`purpose` 非空。退出码：0=全部适用项 PASS，1=存在 FAIL，2=无适用检查（UNVERIFIED）。

当届承诺书/编号页与 AI 使用声明以官方最新模板为准，不保存旧年份文字；脚本只生成清单与哈希并提示命名，上传由队员在竞赛客户端完成，Agent 不得代传。

### Step 8: PDF 视觉检查

如果模型有视觉能力，必须把编译后的 PDF 每页导出为 PNG 并逐页查看。这个步骤用于发现纯文本扫描和编译器无法发现的版式错误。

优先使用系统已有工具导出页面 PNG；不要为了视觉检查引入沉重依赖。可选命令示例：

```bash
mkdir -p _tmp/pdf-pages
if command -v pdftoppm >/dev/null 2>&1; then
  pdftoppm -png -r 160 "$OUTPUT_PDF" _tmp/pdf-pages/page
elif command -v mutool >/dev/null 2>&1; then
  mutool draw -r 160 -o _tmp/pdf-pages/page-%03d.png "$OUTPUT_PDF"
elif command -v magick >/dev/null 2>&1; then
  magick -density 160 "$OUTPUT_PDF" _tmp/pdf-pages/page-%03d.png
else
  echo "No PDF rasterizer found; record visual check as not run."
fi
```

导出后逐页检查：

- 页面是否空白、缺页、页数异常或页面尺寸异常。
- 标题、摘要、正文、页眉页脚、页码是否被裁切或位置明显错误。
- 表格是否超出页边距，单元格文字是否重叠、溢出、被截断。
- 图片、图题、表题、公式、编号是否与正文重叠。
- 公式是否越界，长公式是否压到页边距或下一段文字。
- 列表、段落、脚注、参考文献是否出现异常大空白、重叠或孤立残行。
- 中文/英文/数学符号字体是否明显缺字、乱码或 fallback 异常。
- 封面、摘要页、目录、附录等模板关键页面是否保留比赛要求的视觉结构。

如果是模板转换或已有参考 PDF 的项目，还应将不同引擎的 PDF 都逐页导出 PNG，按页对比版式差异；页数或页面尺寸不一致必须记录为硬错误或明确说明原因。

如果模型没有视觉能力，必须在 `reports/VERIFY_REPORT.md` 中明确写出“未执行视觉检查”的原因，并至少完成 PDF 非空、页数、页面尺寸等可程序化检查。

### Step 9: 写验收报告

先生成最终人工提交审查包：待提交文件精确列表与哈希、提交包验证结果（`reports/submission_bundle_validation.json`）与论文 MD5、逐 ReqID 覆盖摘要、未解决 WARN、完整复现命令/状态、PDF 每页缩略图、工作簿关键 sheet 预览和竞赛规则清单。`human-supervised` 下必须由人类实际查看并明确确认 submission checkpoint；Agent 不上传、不提交，也不能替人勾选。若人类要求修改，记录 `CHANGES_REQUESTED`，快照当前版本并回到相应阶段。

创建 `reports/VERIFY_REPORT.md`：

```markdown
# 验证和验收报告

## 结论
PASS / FAIL / UNVERIFIED

## 检查项
| 检查项 | 结果 | 说明 |
| --- | --- | --- |

## 章节结构

## 图表引用

## 数值一致性

## 逐 ReqID 语义与证据

## 人工审查与版本选择

## 文本质量门禁

## 文献身份、证据与引用追溯

## 编译

## PDF 视觉检查

## 仍需处理的问题
```

只有当硬错误都修复、文献证据与引用追溯通过、全部 ReqID 语义与计算证据通过、完整代码复现、文本门禁、提交表格、编译和视觉检查全部适用项实际通过，版本哈希未陈旧，并且 `human-supervised` 的六个机器强制人工检查点（intake、contract、model、results、paper、submission；启用文献阶段时另含 literature）均明确 `APPROVED` 时，才写 `PASS`。环境缺失、只做 smoke test、人工检查模拟豁免或适用验证未执行时写 `UNVERIFIED`；说明原因不能把它升级为 PASS。

## 硬错误标准

以下问题必须判定 `FAIL`：

- 原始输入哈希失配、题意合同未冻结、阶段/分任务版本哈希陈旧或依赖旧产物。
- 任一原子要求漏答，或回答对象、范围、时点、单位、精度、提交位置与原题不符。
- 代码/模型改变合同固定量、开放未授权自由度，或重大歧义被静默选定。
- 关键 claim 没有证据/独立验证、`changed_fixed_quantities` 非空，或论文结论强度高于账本。
- 经验搜索边界、单一起点或粗采样没有覆盖证据，却声称首次事件或全局最优。
- 隔离评测封存前访问答案来源，仍宣称独立结果；或把封存后修正合并回原提交。
- `HUMAN_REVIEW.json` 有 `CHANGES_REQUESTED` 却继续推进，或 Agent 伪造人工 `APPROVED`。
- 文献 evidence bundle 校验失败，literature gate/人工 checkpoint 缺失或陈旧，或仍为 `FAIL`/`CONDITIONAL` 却进入最终论文。
- 正文引用、参考文献条目和 `reference_map.csv` 之间存在未映射项。
- 被引用来源未登记、身份未核验、不可引用，或只是搜索结果、S4 发现线索、模板占位/未核验示例。
- 核心公式、定理、算法、参数或评价指标缺少 claim、原文定位、变量映射或适用边界。
- 抽查发现登记元数据、DOI/稳定链接、原文内容或定位与实际来源不一致。
- 缺少选定的论文入口文件（`main.typ` 或 `main.tex`）或核心正文。
- 论文入口引用的章节文件不存在。
- Typst 入口缺少 `#include`；LaTeX 入口缺少 `\input`/`\include`。
- 正文章节缺少一级标题（Typst `= ` 后缺空格，LaTeX `\section{}` 缺失）。
- 章节顺序明显错误或重复。
- 正文仍有占位符。
- 正文泄露内部工作流文件名。
- 引用的图片不存在。
- 关键数值与结果记录冲突。
- 编译器可用但论文编译失败。
- 计划选择的 Python/MATLAB 主程序在对应运行时可用时执行失败，或论文附录语言与源代码不一致。
- 编译后的 PDF 为空、缺页、页数异常或页面尺寸异常且无法解释。
- 视觉检查发现正文、表格、图片、公式、页眉页脚、页码等关键元素重叠、裁切、越界或乱码。
- 题目要求的提交表格结构、单元格映射、值或模板保护范围错误。
- 配置为必交的提交包文件缺失或损坏（论文 PDF、承诺书、AI 使用日志、必交附件等）。
- 支撑材料 zip 包含论文正文 PDF，或无法解压、CRC 校验失败、缺少要求的代码条目。

## 多 Agent、空间与大规模实验附加验收

若存在 `reports/agents/ORCHESTRATION.json`，必须运行 `$mathmodel-orchestrator` 的 `agent_protocol.py verify`。逐一核对 packet 输入哈希、provider/model、角色和 result；AI result 中出现 PASS、自称人工审批或引用 packet 外材料即 FAIL。未解决的 `DISAGREEMENT_REPORT` 必须保持 `UNVERIFIED` 或有可追溯的确定性实验/人工决定。

若存在 `reports/SPATIAL_CONTRACT.json`，检查轴顺序、单位、坐标系、距离度量和容差是否在代码、图表和论文一致；核验所有 `spatial_*_audit.json`。二维投影支持不了的三维结论、离散点覆盖冒充连续区域覆盖、原始经纬度欧氏距离或轨迹时间不递增均为 FAIL。

若存在 `reports/ALGORITHM_CANDIDATES.json` 或 experiment manifest，核对候选筛选依据、相同评估预算、多种子全量记录、可行率、超时/失败和小规模证书。只报告最佳 seed、把 PSO/DE 等启发式 best-found 写成全局最优、或迁移高性能机器后未重新固定环境与运行哈希均为 FAIL。

若存在 `reports/WRITING_TEAM_PLAN.json`，运行 `5writing/scripts/writing_team.py audit`。其 `READY_FOR_HUMAN_MERGE` 不是 writing gate；还需单写者合并、编译/视觉检查、证据追溯和真实 paper 人工 checkpoint。

## 警告标准

以下问题可判定为 `WARN`，但应尽量修复：

- 未引用的备用图片。
- 某章节过短或明显不均衡。
- caption 偏长。
- 非核心证据角色覆盖偏弱但不影响核心模型，且已有明确补强建议；参考文献总数本身不是判定标准。
- 图表后解释文字不足。

以下情况不是 WARN，而是 `UNVERIFIED`：视觉检查工具不可用、编译器/运行时缺失、只完成 smoke test、适用的提交表格检查未执行、任何必需人工检查为 `PENDING` 或 `WAIVED_FOR_SIMULATION`。
