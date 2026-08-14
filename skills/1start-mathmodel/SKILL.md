---
name: 1start-mathmodel
description: "数学建模竞赛工作流入口。用于盘点并锁定题面来源，声明竞赛/隔离评测模式，选择 Typst/LaTeX 和 Python/MATLAB，生成可追溯的 plan.md、todo.md 与阶段门禁，并串联文献理论检索、分析建模、编程绘图、论文撰写和验收 skills。"
---

# 数学建模工作流

本 skill 是数学建模竞赛项目的总控入口。它不替代后续阶段 skill，而是负责启动流程、询问偏好、记录决策、生成计划，并按顺序调用各阶段 skill。题面 intake 通过后、正式定模前必须完成文献与理论证据门禁。

## 数学建模规范参考

启动时必须读取 `../_references/modeling_integrity.md` 的“任务模式”“隔离预检”“题意合同”和“上下文压缩”小节；如需领域判断，再读取 `../_references/math_modeling_norms.md` 的相关章节。前者是完整性硬门禁，后者只提供通用建模知识。

## 必须产出

在当前工作目录中创建或更新以下文件：

- `plan.md`：整体流程方案、建模方向、阶段顺序、预期产物和风险控制。
- `todo.md`：具体待办事项列表，记录每个阶段的任务和状态。
- `reports/PROBLEM_MANIFEST.json`：题面、规则和原始附件的白名单、角色、大小与 SHA-256。
- `reports/PROVENANCE.md`：任务模式、允许的信息来源和全部外部信息事件。
- `reports/DECISION_LOG.md`：冻结后决策变更及其失效传播；只追加，不改写历史。
- `reports/HANDOFF.json`：上下文压缩和跨 Agent 交接所需的最小权威状态。
- `reports/STAGE_GATES.json`：可机器检查的阶段状态和上游哈希；`todo.md` 不是门禁证据。
- `reports/HUMAN_REVIEW.json`：六个机器强制人工审查点（intake、contract、model、results、paper、submission；启用文献阶段时另有 literature 检查点）的范围、证据和明确签认。
- `reports/CURRENT_VERSIONS.json` 与 `reports/VERSION_DECISIONS.jsonl`：各分任务当前选择和只追加的选择历史。
- `reports/AI_USAGE_LOG.jsonl`：实际比赛中 AI 工具、版本、用途、关键交互摘要、产物和人类复核/修改的只追加记录；字段服从当届官方规则。
- `reports/LITERATURE_RESEARCH_REPORT.md`：问题指纹、检索过程、证据角色、候选理论路线和门禁结论。
- `references/literature_registry.csv`、`search_log.csv`、`claim_evidence.csv`：可机器检查的来源、检索和主张证据链。

## 工作流

### 0. 盘点题源并建立项目指纹

先列出用户指定的输入目录中的**全部文件**，判断哪些是题面、规则和原始附件，记录无法读取的文件。不要在这一步读取旧代码、旧结果、范文、官方讲评或优秀论文。

确定任务模式：

- `live-competition`：正在进行的比赛，服从当届规则；
- `isolated-benchmark`：解答来源隔离的评测；
- `retrospective-audit`：先隔离求解并封存，再对照外部材料；
- `guided-study`：明确允许利用范文或已知解法学习。

同时记录 `task_provenance`、`runtime_isolation` 和人工监督模式。`live-competition` 必须为 `human-supervised`；模拟演习可显式选择 `autonomous-simulation`，但跳过的人类检查只能标为 `WAIVED_FOR_SIMULATION`，最终不能声称比赛提交就绪。历史公开题只能标为 `historical-public`；即使当前会话没有浏览，也不得声称模型参数从未接触。

使用本 skill 的脚本从**显式列出的输入**初始化完整性文件：

```bash
python "<本 skill>/scripts/project_guard.py" init \
  --project-root . \
  --project-id "<比赛-年份-题号或用户给定 ID>" \
  --mode "<任务模式>" \
  --task-provenance "<private-unreleased|private-parametric|historical-public|current-public|user-provided>" \
  --runtime-isolation "<enforced|declared-only|violated|not-applicable>" \
  --review-mode "<human-supervised|autonomous-simulation>" \
  --input "contest_rules=input/current_official_rules.pdf" \
  --input "problem_statement=input/problem.pdf" \
  --input "attachment=input/data.xlsx" \
  --input "submission_template=input/submission_template.xlsx"
```

不得让脚本自动遍历整个工作区并把未知文件加入白名单。若隔离评测工作区已经包含答案、旧结果或外部讲评，停止该评测并改用物理隔离的新工作区；改名、隐藏目录或提示词禁止读取都不构成严格隔离。

对 CUMCM，当前届官方参赛规则、AI 使用规定、论文格式规范和题目指定模板都属于题目合同的上游输入，必须在开赛前或首次使用时从官方来源保存并散列，不得依赖 skill 内的旧年份文字。竞赛期间如当届规则禁止在公共平台浏览/讨论当前赛题，Agent 的联网搜索也必须关闭，只允许读取队内白名单资料。每次 AI 协助以 JSONL 追加到 `AI_USAGE_LOG.jsonl`，至少记录时间、工具/模型及版本、用途、涉及 ReqID、输入来源摘要、产生的文件、队员复核与实际修改。日志只记录事实，不写入隐私凭据。

逐页/逐工作表读取全部白名单输入，核对 OCR 易错公式、上下标、单位和表头。不得手工改写清单；用脚本原子记录核验者和说明，再复核散列链：

```bash
python "<本 skill>/scripts/project_guard.py" mark-verified --project-root . \
  --input "problem_statement=input/problem.pdf" \
  --actor "<实际核验者>" --note "逐页核对公式、单位和附件"
python "<本 skill>/scripts/project_guard.py" verify --project-root .
```

输入缺失、散列变化、角色未分类或关键公式无法核对时，intake gate 不得写 `PASS`。`human-supervised` 下，先向人类提供文件/页数/工作表/公式与单位核验表；只有人类明确确认材料完整后才能登记 intake `APPROVED`。Agent 不得把自己“已读取”当成人类签认。

初始化后使用本 skill 的 `scripts/task_versions.py` 管理人类反复讨论产生的候选。每个 ReqID、共享模型或论文模块都可独立 `snapshot` 到内容寻址历史库；用 `diff` 比较、`materialize` 到新目录复核，不能靠覆盖当前文件保留“最新版”。`human-supervised` 下 `select` 必须引用 `HUMAN_REVIEW.json` 中一次明确的人类决定。任何选择变化都要更新 `CURRENT_VERSIONS.json`，使固定旧哈希的下游 gate 变为 `STALE`。

典型命令如下；版本哈希和 `approval_id` 均使用脚本实际输出，不要手填伪造：

```bash
python "<本 skill>/scripts/task_versions.py" snapshot --root . \
  --task-id REQ-2-model --branch baseline --actor team \
  --message "可提交基线" --path model=reports/ANALYSIS_MODELING_REPORT.md
python "<本 skill>/scripts/task_versions.py" diff --root . \
  --task-id REQ-2-model --from-version VERSION_HASH_A --to-version VERSION_HASH_B
python "<本 skill>/scripts/task_versions.py" materialize --root . \
  --task-id REQ-2-model --version VERSION_HASH_B --destination _tmp/review-version-b
python "<本 skill>/scripts/task_versions.py" select --root . \
  --task-id REQ-2-model --version VERSION_HASH_B --actor team-member \
  --message "采用经复核的版本 B" --human-review-ref review:model:APPROVAL_HASH
```

六个人工检查点（启用文献阶段时为七个）通过 `project_guard.py review` 记录。该命令要求明确的审查范围、评语、证据和外部记录 ID；已决定的检查点不可原地覆盖。若需改变决定，先快照当前任务并创建新的审查事件/版本，而不是改写旧记录。

```bash
python "<本 skill>/scripts/project_guard.py" review --root . \
  --checkpoint model --status APPROVED --reviewer "<实际队员>" \
  --reviewer-type human --source-id "<会议记录或受控界面记录 ID>" \
  --scope "模型假设、公式、约束、基线与风险" \
  --comments "<人类审查意见>" --evidence "reports/reviews/model.md"
```

### 1. 询问用户偏好

在规划前，只询问会实质影响流程的问题。问题要少而关键。

优先询问（按重要性排序）：

1. **排版引擎**：Typst 还是 LaTeX？— 决定 5writing 使用哪套模板和编译命令。两套引擎均覆盖全部模板（14 中 + 3 英）。Typst 使用 `typst` 命令编译；LaTeX 使用 `xelatex` 命令编译（需跑两遍解决交叉引用）。
2. **竞赛类型**：国赛/华为杯/华中杯/MCM/...— 决定模板选择，见 5writing 的模板族清单。
3. **编程语言**：Python 还是 MATLAB？— Python 使用当前环境；MATLAB 优先调用 `matlab -batch`，没有 MATLAB 时可在代码兼容的前提下使用 GNU Octave。默认 Python。
4. **论文语言**：中文/英文 — MCM/ICM/COMAP 强制英文，其他默认中文。
5. **子问题数量是否已知**：影响章节文件生成数量。若未知，由 2analysis-modeling 阶段根据题面确定。
6. **人工监督模式**：实际比赛必须 `human-supervised`；模拟才可选择 `autonomous-simulation`。用户已经明确时直接记录，不重复询问。
7. **总时间预算与硬截止时间**：用于安排并行、人工审查和降级策略；不能用缩短时间豁免题意、复现或提交检查。
8. **Agent 编排策略**：长题、多附件、多子问或用户要求交叉复核时启用 `$mathmodel-orchestrator`；记录允许的 provider/model、外部 API 数据边界、总 token/费用预算和哪些门禁要求异构审查。真实比赛默认 Codex 总控、最小上下文包、AI 不得自批。

将用户的选择记录到 `plan.md` 的"方案"小节中。


### 2. 制定方案

按以下结构编写 `plan.md`：

```markdown
# 方案

要依次调用这些 skill，按照里面要求完成任务。

用户偏好：
- 排版引擎：<Typst / LaTeX>
- 编程语言：<Python / MATLAB>
- MATLAB 运行时：<MATLAB / Octave / 不适用>
- 竞赛类型：<国赛 / 华为杯 / MCM / ...>
- 论文语言：<中文 / 英文>
- 子问题数量：<已知 N 个 / 待分析确定>
- 任务模式：<live-competition / isolated-benchmark / retrospective-audit / guided-study>
- 任务来源：<private-unreleased / private-parametric / historical-public / current-public / user-provided>
- 运行隔离：<enforced / declared-only / violated / not-applicable>
- 人工监督：<human-supervised / autonomous-simulation>
- 项目指纹：<PROBLEM_MANIFEST.json 的 SHA-256>
- 总时间预算/截止：<小时与绝对时间>
- 验收预留：<建议不少于总预算的 20%，按题目风险调整>

workflow（文献与 DrawIO 按触发条件启用）：
   step      skills
0. 可选文献、理论与方法检索 - `mathmodel-literature-research`
2. 赛题分析与建模设计 - `2analysis-modeling`
3. 编程实现和图表生成 - `3coding-visual`
4. 流程与架构图绘制 - `4drawio`
5. 竞赛论文撰写 - `5writing`
6. 验证和验收 - `6verity`
```

## 项目目录结构

各阶段按此骨架创建和填充文件：

```text
.
├── plan.md                      # 1: 本文件
├── todo.md                      # 1: 待办事项
├── reports/                     # 各阶段文档报告
│   ├── PROBLEM_MANIFEST.json        # 0: 原始输入白名单与指纹
│   ├── PROBLEM_CONTRACT.md          # 1: 可读题意合同
│   ├── PROBLEM_CONTRACT.json        # 1: 可机器检查的原子要求
│   ├── PROVENANCE.md                # 全流程: 信息来源记录
│   ├── DECISION_LOG.md              # 全流程: 冻结后变更，只追加
│   ├── HANDOFF.json                 # 全流程: 最小交接状态
│   ├── STAGE_GATES.json             # 全流程: 阶段状态与上游哈希
│   ├── HUMAN_REVIEW.json            # 全流程: 关键人工签认
│   ├── CURRENT_VERSIONS.json         # 全流程: 分任务当前版本
│   ├── VERSION_DECISIONS.jsonl       # 全流程: 只追加选择历史
│   ├── AI_USAGE_LOG.jsonl             # 全流程: CUMCM AI 使用与人工修改记录
│   ├── reviews/                      # 全流程: Agent 生成、供人核验的短审查包
│   │   ├── intake.md
│   │   ├── literature.md
│   │   ├── contract.md
│   │   ├── model.md
│   │   ├── results.md
│   │   ├── paper.md
│   │   └── submission.md
│   ├── LITERATURE_RESEARCH_REPORT.md # 1: 文献与理论检索报告
│   ├── ANALYSIS_MODELING_REPORT.md  # 2: 赛题分析-建模报告（2analysis-modeling）
│   ├── RESULTS_REPORT.md            # 3: 结果报告（3coding-visual）
│   ├── DRAWIO_REPORT.md             # 4: 非数据图说明（4drawio）
│   ├── VERIFY_REPORT.md             # 6: 验收报告（6verity）
├── references/                  # 1: 检索、来源与 claim 证据链
│   ├── literature_registry.csv
│   ├── search_log.csv
│   └── claim_evidence.csv
├── code/                        # 3: 代码（3coding-visual）
│   ├── problem1.py / problem1.m
│   ├── problem2.py / problem2.m
│   ├── problem3.py / problem3.m  # 扩展名由编程语言决定，数量按题目调整
│   ├── ... 
│   └── utils.py / utils.m
├── results/                     # 3: 结果记录（3coding-visual）
│   ├── claim_ledger.json        #     原子要求—结论—证据映射
│   ├── validation_manifest.json #     独立验证记录
│   └── run_manifest.json        #     代码、输入与产物指纹
├── figures/                     # 3+4: 所有图表（3coding-visual + 4drawio）
│   ├── *.pdf                    #     数据图 + 非数据图 PDF
│   ├── *.drawio                 #     非数据图源文件
├── paper/                       # 5: 论文（5writing）
│   ├── main.typ / main.tex      #     论文主文件（按用户选择的引擎）
│   ├── reference_map.csv        #     引用键到已核验 source_id 的映射
│   └── sections/                #     各节文件（.typ 或 .tex）
```

方案必须明确每个阶段由哪个下游 skill 负责，以及该阶段应产出什么文件。

### 可选的时间受限竞赛剖面

只有用户明确给出短硬截止时才启用本节；正常 CUMCM 辅助不以压缩到 10 小时为目标。若必须在约 10 小时内形成强候选稿，使用“可随时提交”的滚动策略，而不是最后一小时才集成：

1. 前 15%：完成输入核验、问题指纹、必要的定向文献证据、ReqID 合同和相应人工审查；
2. 中间约 45%：简单基线先贯通全部必交输出，再并行改进高价值模型和独立验证；
3. 接着约 15%：从 supported claims 生成论文和提交表格，边写边编译；
4. 最后至少 25%：完整复现、语义红队、人工逐问/PDF/表格审查与修订。

比例是可调整预算，不是题型规律。若进度落后，优先降低模型复杂度、实验数量和装饰图；不得删掉题意合同、必交输出、独立验证、人工检查或最终复现。每个阶段都保留一个已通过的基线版本，高风险改进在独立分支进行，失败时可安全回退。

短硬截止（约 ≤10 小时）默认关闭文献严格模式（`mathmodel-literature-research`）与多 Agent 编排（`$mathmodel-orchestrator`），除非用户明确要求；关闭后不得引用未核验外部来源，且对应门禁记 N/A。

### 3. 生成待办

将 `todo.md` 写成阶段性 checklist，格式如下：

```markdown
# 待办事项

- [ ] 0. 按需文献、理论与方法检索 - `mathmodel-literature-research`（未触发时 N/A）
- [ ] 2. 赛题分析与建模设计 - `2analysis-modeling`
- [ ] 3. 编程实现和图表生成 - `3coding-visual`
- [ ] 4. 流程与架构图绘制 - `4drawio`
- [ ] 5. 竞赛论文撰写 - `5writing`
- [ ] 6. 验证和验收 - `6verity`
```

每完成一个阶段，都要更新 `todo.md` 中对应任务的状态。

`todo.md` 只用于向人展示。阶段进入条件、退出条件和失效状态以 `reports/STAGE_GATES.json` 为准，状态只能是 `NOT_STARTED`、`RUNNING`、`BLOCKED`、`PASS`、`FAIL`、`STALE` 或 `UNVERIFIED`。输入或上游合同散列变化时，所有依赖阶段必须改为 `STALE` 并重跑，不能沿用旧 checkbox。

### 4. 依次执行阶段

按以下顺序调用下游 skills：

| 阶段 | Skill | 作用 | 主要产物 |
| --- | --- | --- | --- |
| 按需文献、理论与方法检索 | `mathmodel-literature-research` | 仅在核心外部事实、公式出处、数据口径或方法选择确需证据时定向检索；不得挤占建模和计算。 | `LITERATURE_RESEARCH_REPORT.md`, `references/*.csv` |
| 赛题分析与建模设计 | `2analysis-modeling` | 解析题意、识别变量/约束/数据/评价指标，并建立数学模型、目标函数、约束条件和求解策略。 | `ANALYSIS_MODELING_REPORT.md` |
| 编程实现和图表生成 | `3coding-visual` | 实现可复现代码，运行实验，生成结果表和多种多样的图表。 | `code/`, `results/` ,  `RESULTS_REPORT.md`, `figures/图表` |
| 流程与架构图绘制 | `4drawio` | 在论文确实需要时，绘制方法流程图、架构图和非数据型概念图。 | `figures/*.drawio`, `figures/*.pdf`, `DRAWIO_REPORT.md` |
| 竞赛论文撰写 | `5writing` | 基于分析、建模、代码结果和图表撰写最终竞赛论文，并按章节直接插入图表。 | `paper/` |
| 验证和验收 | `6verity` | 检查可复现性、一致性、产物完整性、格式规范和提交就绪状态。 | `VERIFY_REPORT.md` |

高风险触发器：

- 长上下文、多附件、多子问、并行候选或异构模型复核：在 intake 后调用 `$mathmodel-orchestrator`，初始化 `reports/agents/`；下游 Agent 只交换哈希固定的 packet/result。
- 出现坐标、经纬度、距离、邻接、轨迹、覆盖、视线、路径或二维/三维几何：analysis 前调用 `$mathmodel-spatial`，冻结 `SPATIAL_CONTRACT.json`。
- 需要搜索/比较 PSO、差分进化、数学规划、预测模型或大规模参数实验：调用 `$mathmodel-algorithm-lab`；弱机使用 `weak-dev` smoke test，高性能机再扩大种子和预算。
- 选择 MATLAB：检查官方 Agentic Toolkit；MCP 负责开发测试，独立 batch runner 负责最终复现。

每个阶段开始前先核对上游 gate、当前输入哈希和 `reports/HANDOFF.json`，结束时写入产物哈希、覆盖的 `REQ-*`、验证命令与退出码。`human-supervised` 下，intake、contract、model、results、paper、submission 六个机器强制检查点（启用文献阶段时增加 literature）都先生成一页审查包，汇报关键决策、正反证据、计算结果、最高风险和可复核命令；只有人类明确确认后才能在 `HUMAN_REVIEW.json` 如实登记 `APPROVED`。Agent 不得替人批准。确认不能豁免事实错误；用户明确接受的范围缩减应记录为 waiver，但最终状态不得因此把未验证项写成已通过。

## 阶段边界

- `mathmodel-literature-research` 是条件式门禁：题目完全由附件和通用数学自洽求解时可不启用；涉及外部参数、行业口径、非平凡理论出处或必须引用的方法时才启用。启用后必须服从 `PROVENANCE.md` 的来源边界，且门禁 `FAIL` 时不得使用相应外部主张。
- 每个阶段完成后先生成相应审查包，汇报关键假设、理论/模型选择、检索或计算结果和风险；获得用户明确确认后才推进 human-supervised 流程。
- `3coding-visual` 负责生成所有依赖计算结果或实验输出的数据图表。
- `3coding-visual` 必须遵循 `plan.md` 中的编程语言选择；不要在同一子问题中无说明地混用 Python 和 MATLAB。
- `4drawio` 只负责概念图、算法流程图、架构图、路线图等非数据型图示。
- 不要让 `4drawio` 重复绘制 `3coding-visual` 已经生成的统计图或数据图。
- `5writing` 负责决定图表在论文中的位置，并按所选引擎写入图表代码：
  - Typst：`#figure(image("../../figures/xxx.pdf", width: 85%), caption: [...])`
  - LaTeX：`\begin{figure}[H]\centering\includegraphics[width=0.85\textwidth]{../../figures/xxx.pdf}\caption{...}\label{fig:xxx}\end{figure}`
- 不要让 `5writing` 编造数值结论。论文中的数值必须来自 `RESULTS_REPORT.md`、结果表或已生成图表的数据。
