---
name: 5writing
description: "数学建模竞赛论文撰写阶段，支持 Typst/LaTeX 双引擎和 Python/MATLAB 附录。根据已核验文献证据包、冻结题意合同、结论证据账本、分析与结果报告和 figures/*.pdf 选择模板、组织章节并生成不新增事实且可逐 ReqID 与 source_id 追溯的论文。"
---

# 竞赛论文撰写（Typst / LaTeX）

本 skill 承接 `mathmodel-literature-research`、`3coding-visual` 和 `4drawio`。前序阶段只提供已核验文献、真实数据、图表 PDF 和记录文件；本阶段负责选择比赛模板和排版引擎、组织论文结构、建立引用映射，并决定每张图表放入哪个章节。

**Typst 引擎**下可调用 typst-author skill 学习 typst 写法；**LaTeX 引擎**参考本文件末尾的"LaTeX 写作要点"小节。

## 数学建模规范参考

必须读取 `../_references/modeling_integrity.md` 的“题意合同”“结论—证据账本”和“最低硬门禁”；如需领域判断，再读取 `../_references/math_modeling_norms.md` 中的“论文写作”“图表与可视化”和“非数据图工具选择”小节。

## 模板族

本技能内捆绑的模板位于：

```text
templates/zh/<竞赛>/main.typ         # Typst 模板
templates/zh/<竞赛>-latex/main.tex   # LaTeX 模板
templates/en/<竞赛>/main.typ         # Typst 模板
templates/en/<竞赛>-latex/main.tex   # LaTeX 模板
```

**LaTeX 模板覆盖范围**：所有中文模板和英文模板均已提供 LaTeX 版本（`-latex` 后缀），使用 xelatex 编译。

支持的中文模板（Typst + LaTeX 双版本）：

```text
apmcm, changsanjiao, cumcm, default, diangongbei, dongsansheng,
huashubei, huaweibei, huazhongbei, mathorcup, mcm, shuweibei, stats, wuyibei
```

华为杯、华中杯、五一杯统一使用 `huaweibei`、`huazhongbei`、`wuyibei` 作为模板。

支持的英文模板（Typst + LaTeX 双版本）：

```text
apmcm, default, mcm
```

论文中的所有数值、图表和关键结论必须来自 `results/claim_ledger.json` 中状态为 `supported` 的记录，并能回到 `validation_manifest.json` 和实际证据。`RESULTS_REPORT.md` 仅用于解释。不得编造、估算、静默改精度，也不得把 `claim_type=heuristic`、`epistemic_status=best_found` 的结果改写为全局最优。


## 工作流

### 步骤 -1：前置门禁与证据范围

写作前必须读取 `plan.md`、`PROBLEM_CONTRACT.md/.json`、`ANALYSIS_MODELING_REPORT.md`、`RESULTS_REPORT.md`、`claim_ledger.json`、`validation_manifest.json`、`run_manifest.json`、`DECISION_LOG.md` 和 `HANDOFF.json`。若 literature gate 被触发，再读取并校验文献证据包；确认所有必需的 analysis、coding 及已触发上游 gate 为 `PASS` 且哈希未陈旧，再把 writing gate 标为 `RUNNING`。

本阶段只能组织和解释已支持结论，不能：

- 新增模型假设、改变固定量/决策量、重解释题意或补算新结果；
- 用措辞把近似、启发式或未验证结论升级为证明、因果或全局最优；
- 从图像目测、旧论文、模板示例或外部摘要抄入账本不存在的数值；
- 在隔离阶段为补参考文献而联网接触本题答案来源。

确需新结论或合同变更时停止写作，回到对应阶段并按失效传播重跑。

### 多 Agent 写作（长论文推荐）

当章节多、上下文超过单 Agent 可可靠掌握的范围，调用 `$mathmodel-orchestrator`，采用“分节起草 + 双审查 + 单合并者”：

- outline editor 只生成章节—ReqID—claim—图表映射；
- section drafter 每次只接收本节所需的 frozen contract、supported claims、图表和已核验来源；
- equation reviewer 只检查符号、公式、量纲、目标/约束和论文与模型报告一致性；
- evidence reviewer 只检查数值、引用、认知等级和越界表述；
- paper merger 是唯一能写最终 `paper/sections/` 的 Agent，逐条处理审查意见，不让并行 Agent 同时改同一文件。

先建立写作团队计划：

```bash
python "<本 skill>/scripts/writing_team.py" init --project-root . \
  --require-heterogeneous-review \
  --section paper/sections/1_restatement.tex \
  --section paper/sections/2_analysis.tex \
  --section paper/sections/5_problem1.tex
```

计划中的每个 task ID 通过 `$mathmodel-orchestrator` 签发独立 `CONTEXT_PACKET`。起草 Agent 不看其他草稿；两个 reviewer 在草稿封存后只读审查。完成后运行：

```bash
python "<本 skill>/scripts/writing_team.py" audit --project-root .
```

`--require-heterogeneous-review` 要求每节至少有一个 reviewer 与 drafter 使用不同的 provider/model；未配置外部模型时可先省略该参数，但审计会记录异构复核覆盖数。同一 provider/model 的自审不能被宣称为独立复核。`READY_FOR_HUMAN_MERGE` 只代表结果齐全，不是 writing PASS。最终仍需编译、视觉检查、`$6verity` 和真实队员 paper checkpoint。

### 步骤 0：确定排版引擎

**撰写论文前必须让用户选择排版引擎。** 引擎决定后续所有步骤（模板路径、章节文件扩展名、图片插入语法、编译命令），选错会导致整篇论文格式错误。

使用 AskUserQuestion 工具向用户询问："撰写论文使用哪种排版引擎？"

- 选项 1：LaTeX（xelatex 编译，数学建模竞赛主流，模板已全部就绪）— 推荐选项放第一位
- 选项 2：Typst（typst 编译，调用 typst-author skill 辅助写作）

询问前先读取 `plan.md` 的"用户偏好 → 排版引擎"字段作为预选项：
- 若 plan.md 已记录引擎选择，向用户确认："检测到之前选择的引擎是 <LaTeX/Typst>，是否沿用？"
- 若 plan.md 不存在或未记录引擎选择，直接询问用户选择。
- 若用户未明确指定或跳过，**默认使用 LaTeX**。

根据确定的引擎选择对应模板族：

- **Typst 引擎**：使用 `templates/<lang>/<竞赛>/main.typ`，调用 typst-author skill。编译命令 `typst compile main.typ`。
- **LaTeX 引擎**：使用 `templates/<lang>/<竞赛>-latex/main.tex`，xelatex 编译（中文和英文均需跑两遍解决交叉引用）。编译命令 `xelatex -interaction=nonstopmode main.tex`（执行两次）。

**后续步骤中的所有代码示例、文件扩展名、图片插入语法都必须按所选引擎选择对应版本，不要混用。**

### 步骤 1：选择语言和模板


除非用户明确要求中文，否则 MCM/ICM/COMAP 一律使用英文。所有中文竞赛名称使用中文。

模板键示例（Typst 引擎）：

```text
长三角 -> zh/changsanjiao
APMCM 英文版 -> en/apmcm
全国赛/国赛/CUMCM -> zh/cumcm
统计建模 -> zh/stats
MCM/ICM/COMAP -> en/mcm
```

模板键示例（LaTeX 引擎）：

```text
全国赛/国赛/CUMCM -> zh/cumcm-latex
MCM/ICM/COMAP -> en/mcm-latex
```

### 步骤 2：准备模板

用以下命令检查捆绑模板是否可访问（`SKILL_DIR` 为本 skill 所在目录）：

维护或更新模板后还要运行：

```bash
python "$SKILL_DIR/scripts/sanitize_reference_templates.py" "$SKILL_DIR/templates" --check
```

该检查必须确认 17 套 Typst 和 17 套 LaTeX 的参考文献文件均为无示例的 `REFS_NOT_READY` 安全模板。普通写作流程只使用 `--check`，不要在项目论文目录运行模板维护脚本。

**Typst 模板**：

```bash
ls "$SKILL_DIR/templates/zh/<竞赛>/main.typ" 2>/dev/null && echo "OK" || echo "MISSING"
```

- **文件存在（OK）**：直接将 `templates/zh/<竞赛>/` 整目录复制到 `paper/`。这些模板是自包含入口文件，不依赖额外共享样式文件。
- **文件不存在（MISSING）**：说明 skill 未完整安装或在沙箱中，此时依照本 SKILL.md 步骤 3 列出的对应节文件结构，从零重建最小可编译 Typst 框架，并在 `paper/` 内注明"重建自 default 结构"。

存在匹配模板时，绝不从零开始写论文。

**LaTeX 模板**：

```bash
ls "$SKILL_DIR/templates/zh/<竞赛>-latex/main.tex" 2>/dev/null && echo "OK" || echo "MISSING"
```

- **文件存在（OK）**：将 `templates/zh/<竞赛>-latex/` 整目录复制到 `paper/`。
- **文件不存在（MISSING）**：说明 skill 未完整安装或在沙箱中，此时依照本 SKILL.md 步骤 3 列出的对应节文件结构，从零重建最小可编译 LaTeX 框架，并在 `paper/` 内注明"重建自 default-latex 结构"。

**LaTeX 宏包依赖与编译预检**：

- 每个 LaTeX `main.tex` 顶部都有一行"依赖宏包清单"注释，列出该模板实际加载的全部宏包。xelatex 缺宏包会编译失败：MiKTeX 用户确保首次编译可联网（自动安装）；TeX Live 用户预先安装 `texlive-latex-extra`（含 titlesec/tocloft/float/enumitem/lastpage）与 `texlive-fonts-extra`。
- 赛前 dry run 必须**实际编译**所选模板验证本机环境（静态对应检查不覆盖宏包问题）：
  ```bash
  python "$SKILL_DIR/scripts/validate_templates.py" --compile zh/cumcm   # 单个模板
  python "$SKILL_DIR/scripts/validate_templates.py" --compile-all        # 全部 17 套，较慢
  ```
  编译失败时按"依赖宏包清单"补装宏包后重试，不得在比赛期间才第一次编译。

**模板哨兵（防误提交）**：

- 模板正文中的 `【示例数值·待替换】`（中文模板）与 `[EXAMPLE-VALUE]`（英文模板）是防误提交哨兵，标记模板自带的虚构示例数值（如 $R^2=0.896$ 一类），**必须全部替换为 `results/claim_ledger.json` 中的真实数值**；`sections/A_code.*` 顶部的"示例代码（模板占位）"注释同理，必须连同整段示例代码一起替换为本题真实可复现代码。
- 残留任何哨兵都会被 `$6verity` 的 `writing_check.sh` 判为 `FAIL`——这正是设计目的，不要试图绕过。


### 步骤 3：构建图表规划

在写正文各节之前先生成 `reports/PAPER_TRACEABILITY.json`：逐 `REQ-*` 记录覆盖它的 supported claim、正文文件/章节、表格/图和最终回答句。内部 ID 不必出现在论文成稿，但映射必须完整；一项要求可以由多条 claim 支持，不能用“该问题已讨论”代替精确映射。

再根据 claim ledger、`figures/*.pdf`、`reports/RESULTS_REPORT.md`，以及 `reports/DRAWIO_REPORT.md`（如果存在）构建图表规划。每张结果图必须关联至少一个 claim/ReqID 和 run manifest 中的新鲜源数据：

```text
图表规划
fig_roadmap.pdf -> 引言/问题重述
fig_flow_q1.pdf -> 问题一模型构建
fig_flow_q2.pdf -> 问题二模型构建
fig_pipeline.pdf -> 数据预处理/方法节
结果图 -> 对应的结果节
```

图片路径解析规则（两种引擎不同，勿混用）：

- **Typst**：相对路径按**引用图片的文件**解析——写在 `paper/sections/*.typ` 用 `../../figures/xxx.pdf`，写在 `paper/main.typ` 用 `../figures/xxx.pdf`；
- **LaTeX**：相对路径按**编译工作目录**解析。规定统一从 `paper/` 目录执行 `xelatex main.tex`，因此**所有文件（含 sections）一律用 `../figures/xxx.pdf`**（2026-08 实战演习中两题因 sections 写 `../../figures/` 而编译失败，已修正该口径；writing_check 对两种解析都接受）。

**Typst 引擎**图片插入：

```typst
#figure(
  image("../../figures/fig_q1_error_dist.pdf", width: 85%),
  caption: [问题一预测误差分布],
)
```

**LaTeX 引擎**图片插入：

```latex
\begin{figure}[H]
  \centering
  \includegraphics[width=0.85\textwidth]{../../figures/fig_q1_error_dist.pdf}
  \caption{问题一预测误差分布}
  \label{fig:q1_error}
\end{figure}
```

英文论文使用英文图注。

### 步骤 4：撰写各节

每个响应单元先直接回答题面动作，再给模型、推导和解释。逐项检查对象、范围、指定时点、单位、精度和必交文件；未编号小问同样要有最终回答句。论文可以采用不同于模板示例的章节组织，但不得漏掉 `PAPER_TRACEABILITY.json` 中的要求。

**以下章节文件名按所选引擎使用 `.typ`（Typst）或 `.tex`（LaTeX）扩展名。** 例如 Typst 引擎用 `1_restatement.typ`，LaTeX 引擎用 `1_restatement.tex`。文件名主体保持一致。

中文数学建模通用模板各节文件（`changsanjiao`、`diangongbei`、`huashubei`、`mathorcup`、`wuyibei`）：

```text
1_restatement.typ  - 问题重述与分析
2_analysis.typ     - 数据理解与总体思路
3_assumptions.typ  - 模型假设
4_symbols.typ      - 符号说明
5_problem1.typ     - 问题一建模与求解
6_problem2.typ     - 问题二建模与求解
7_problem3.typ     - 问题三建模与求解
...         - 根据题目调整问题数量  
8_evaluation.typ   - 灵敏度分析、模型评价与推广
A_code.typ         - 附录代码
```

国赛/华中杯/华为杯（`cumcm`、`huazhongbei`、`huaweibei`）按以下章节结构：

```text
1_restatement.typ
2_analysis.typ
3_assumptions.typ
4_symbols.typ
5_problem1.typ
6_problem2.typ
7_problem3.typ
...        - 根据题目调整问题数量
8_sensitivity.typ
9_evaluation.typ
A_code.typ
```

东三省模板（`dongsansheng`）额外使用单独摘要文件：

```text
abstract.typ
1_restatement.typ
2_analysis.typ
3_assumptions.typ
4_symbols.typ
5_problem1.typ
6_problem2.typ
7_problem3.typ
...       - 根据题目调整问题数量
8_evaluation.typ
A_code.typ
```

数维杯模板（`shuweibei`）保留原 LaTeX 的示例入口命名：

```text
Abstract.typ
Introduction.typ
2_analysis.typ
3_assumptions.typ
4_symbols.typ
5_problem1.typ
6_problem2.typ
7_problem3.typ
...      - 根据题目调整问题数量
8_evaluation.typ
Appendices1.typ
A_code.typ
```

中文默认模板（`default`）：

```text
1_restatement.typ
2_assumptions.typ
3_symbols.typ
4_problem1.typ
5_problem2.typ
6_problem3.typ
...      - 根据题目调整问题数量
7_sensitivity.typ
8_evaluation.typ
A_code.typ
```

中文统计建模各节文件：

```text
1_introduction.typ
2_method.typ
3_data.typ
4_analysis.typ
5_results.typ
6_conclusion.typ
A_code.typ
```

英文 MCM/APMCM 各节文件（`en/mcm`、`en/apmcm`、`zh/mcm`、`zh/apmcm`）：

```text
1_introduction.typ
2_assumptions.typ
3_model_design.typ
4_solution.typ
5_sensitivity.typ
6_strengths_weaknesses.typ
7_conclusions.typ
A_code.typ
```

**LaTeX 模板章节文件**（对应 `-latex` 后缀模板，结构与 Typst 版本一一对应）：

国赛 LaTeX 模板（`zh/cumcm-latex`，对应 `cumcm` Typst 版本）：

```text
1_restatement.tex
2_analysis.tex
3_assumptions.tex
4_symbols.tex
5_problem1.tex
6_problem2.tex
7_problem3.tex
8_sensitivity.tex
9_evaluation.tex
A_code.tex
```

MCM/ICM LaTeX 模板（`en/mcm-latex`）：

```text
1_introduction.tex
2_assumptions.tex
3_model_design.tex
4_solution.tex
5_sensitivity.tex
6_strengths_weaknesses.tex
7_conclusions.tex
A_code.tex
```

其余 LaTeX 模板（`changsanjiao-latex`、`default-latex`、`huashubei-latex`、`mathorcup-latex`、`wuyibei-latex`、`huazhongbei-latex`、`huaweibei-latex`、`diangongbei-latex`、`dongsansheng-latex`、`shuweibei-latex`、`stats-latex`、`apmcm-latex`、`mcm-latex`、`en/apmcm-latex`、`en/default-latex`）的章节文件命名与上述结构类似，以 `main.tex` 中 `\input{}` 引用的文件名为准。

英文默认模板（`en/default`）：

```text
1_introduction.typ
2_assumptions.typ
3_notations.typ
4_model.typ
5_sensitivity.typ
6_evaluation.typ
7_conclusions.typ
A_code.typ
```

**正文写作应使用连贯的学术段落。避免在最终论文中出现工作流内部名称，如 `reports/`、`figures/` 或 `CLAUDE.md`。**

代码附录必须与 `plan.md` 的编程语言一致：

- Typst：Python 使用语言标记为 `python` 的代码块，MATLAB 使用语言标记为 `matlab` 的代码块。
- LaTeX：Python 使用 `\begin{lstlisting}[language=Python]`，MATLAB 使用 `\begin{lstlisting}[language=Matlab]`。
- 若模板的 `A_code` 仍是另一种语言的占位代码，替换语言标记和内容，不得把 Python 示例误标为 MATLAB 或反之。
- 附录只收录可复现主程序和关键函数；版本、随机种子、工具箱/依赖与运行命令写入附录说明。

### 步骤 5：参考文献

参考文献只能来自 `references/literature_registry.csv` 中同时满足 `identity_status=verified`、`citable=yes` 的来源，并且正文表述必须能回到 `claim_evidence.csv` 的原文定位和适用边界。每条来源还须存在于 `PROVENANCE.md`。任务模式不允许联网时，只能使用隔离环境中合法提供且可核验的资料；外部文献不能替代本题计算证据或把外部答案回填为独立结果。

需要新增来源时停止写作，返回 `mathmodel-literature-research` 完成精确检索、身份核验、内容访问、claim 映射和门禁更新；如果新证据改变模型、假设、算法、验证或基线，按失效传播重跑分析和后续阶段。不得在写作阶段凭记忆补论文。

模板中的 `REFS_NOT_READY` 是阻止误提交的哨兵，不是参考文献。复制模板后必须用真实条目整体替换哨兵；不得保留模板示例、占位或猜测缺失的作者、年份、卷期、页码、DOI。文件名按引擎选择：Typst 用 `paper/references.typ`，LaTeX 用 `paper/references.tex`。

同时创建 `paper/reference_map.csv`：

```csv
citation_key,source_id,rendered_reference
1,S001,"由 registry 已核验元数据排出的完整条目"
S002,S002,"由 registry 已核验元数据排出的完整条目"
```

`citation_key` 必须与正文实际引用一致；`source_id` 必须回到 registry；`rendered_reference` 必须与论文参考文献列表一致。Typst 手工上标编号可使用 `1,2,...`，LaTeX 推荐直接使用 `S001,S002,...` 作为 `\bibitem` 键。

**Typst 引擎**：

```typst
#set enum(numbering: "[1]")
#enum[
  // 仅写 registry 中 verified + citable 的真实条目。
]
```

正文上标引用示例：`该公式的成立条件见原始来源#super("[1]")。`，并在 `reference_map.csv` 中把键 `1` 映射到对应 `source_id`。

**LaTeX 引擎**：

```latex
\begin{thebibliography}{99}
  \bibitem{S001} <由 registry 的已核验元数据生成的完整条目>
\end{thebibliography}
```

正文引用用 `\cite{S001}`。外部公式、定理、算法、参数和评价指标要在首次实际使用处引用，不能只在研究现状中集中罗列。摘要或仅元数据来源不得被扩写成正文细节。

论文完成后运行：

```bash
python <mathmodel-literature-research skill>/scripts/validate_literature_bundle.py \
  <项目根目录> --paper-dir paper --strict
```

### 步骤 6：最后撰写摘要或总结

在所有章节完成后撰写中文摘要或英文 Summary Sheet。必须包含每个子问题的方法和精确的数值结果。

摘要中的每个关键结论也必须在 `PAPER_TRACEABILITY.json` 中映射到 supported claim，并沿用账本的认知等级和舍入。对人类尚在权衡的表述或结构保存为分任务候选，使用 diff 审查后再 select，不覆盖早期版本。完成写作后更新 `CURRENT_VERSIONS.json`、writing gate 和 `HANDOFF.json`；只有全部 ReqID 有最终回答、全部论文 claim 可追溯且无新增事实，并取得 paper 人工签认时才可 `PASS`。模拟豁免只能 `UNVERIFIED`。

### 步骤 7：质量评审环（写完后自己读一遍）

章节与摘要完成后、`$6verity` 之前，调用 `$mathmodel-review-polish` 执行一轮质量评审与打磨：

- 独立评审（优先异构模型）按评分卡打分并产出排序改进清单；
- 摘要至少生成 3 个候选版本（方法流/结果流/创新流），快照、diff、比较后由队员选择；
- 按改进清单迭代一轮（默认一轮；高风险题两轮）；改动只限表述、结构与完整性，不得新增事实、不得引入账本外数值；
- 时间紧张时至少保留摘要评审，可跳过全文章节重写；评审报告 `reports/REVIEW_REPORT.md` 纳入 paper 检查点审查范围。

## LaTeX 写作要点

以下要点供 **LaTeX 引擎**使用。Typst 引擎请调用 typst-author skill 获取语法帮助。

### 编译命令

```bash
# 中文模板（xelatex，跑两遍解决交叉引用）
xelatex main.tex && xelatex main.tex

# 英文模板（xelatex，同样跑两遍）
xelatex main.tex && xelatex main.tex
```

### 文档结构

```latex
\documentclass[a4paper,12pt]{article}   % 英文
\documentclass[a4paper,12pt]{ctexart}   % 中文

\usepackage{...}   % 宏包加载
\usepackage{graphicx}   % 图片支持
\usepackage{booktabs}   % 三线表
\usepackage{amsmath,amssymb}   % 数学公式
\usepackage{hyperref}   % 交叉引用（需两遍编译）
```

### 图表插入

```latex
\begin{figure}[H]
  \centering
  \includegraphics[width=0.85\textwidth]{../../figures/fig_q1.pdf}
  \caption{图注}
  \label{fig:q1}
\end{figure}

% 三线表
\begin{table}[htbp]
  \centering
  \caption{表注}
  \begin{tabular}{ccc}
    \toprule
    \textbf{列1} & \textbf{列2} & \textbf{列3} \\
    \midrule
    数据 & 数据 & 数据 \\
    \bottomrule
  \end{tabular}
\end{table}
```

### 交叉引用

```latex
如图~\ref{fig:q1}所示，...   % 图片引用
式~(\ref{eq:objective}) 给出...   % 公式引用
见第~\pageref{fig:q1} 页   % 页码引用
```

### 数学公式

```latex
行内公式：$f(x) = \sum_{i=1}^n \theta_i \phi_i(x)$

行间公式：
\begin{equation}
  \mathcal{L}(\theta) = \frac{1}{N}\sum_{i=1}^N (y_i - \hat{y}_i)^2
  \label{eq:objective}
\end{equation}
```

### 章节和强调

```latex
\section{问题重述}
\subsection{问题背景}
\textbf{问题一：} xxx   % 对应 Typst 的 #strong
```
