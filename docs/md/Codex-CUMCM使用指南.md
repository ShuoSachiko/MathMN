# MathModelAgent Codex 版：CUMCM 使用指南

本指南适用于本仓库的 Codex-first 数学建模工作流，覆盖赛题分析、数学建模、Python/MATLAB 编程、图表生成、LaTeX 论文撰写和最终验收（排版引擎推荐 LaTeX/xelatex，Typst 仅作可选保留）。

下文 `<仓库根>` 指本仓库实际位置（本机为 E:\MathModelingNJU，请自行替换）。

> 推荐组合：中文论文 + LaTeX + Python。已有 MATLAB 代码积累或需要 MATLAB 工具箱时，选择中文论文 + LaTeX + MATLAB。

## 1. 首次配置

在 PowerShell 中进入仓库：

```powershell
cd <仓库根>
```

只使用 Codex 和 Skills：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1 -CodexOnly
```

该命令在 `.agents/skills` 配置仓库级 Skills。重复运行是安全的。

如果还要使用 WebUI，运行完整安装：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-local.ps1
powershell -ExecutionPolicy Bypass -File scripts\start-local.ps1
```

WebUI 地址：<http://127.0.0.1:5173>  
后端 API 文档：<http://127.0.0.1:8000/docs>

WebUI 需要单独配置模型 API Key。Codex Skills 工作流不要求把 Codex 的凭据保存在本项目中。

### 可选增强检查

MATLAB 用户可先只读检查官方 Agentic Toolkit：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-matlab-agentic.ps1
```

该脚本默认不安装任何内容。决定启用时运行 `powershell -ExecutionPolicy Bypass -File scripts\setup-matlab-agentic.ps1 -Install`；官方源码、MCP 二进制和日志保存在被忽略的 `.runtime/`，机器专属配置保存在被忽略的 `.codex/config.toml`。重启 Codex 后用 `/mcp` 查看 `matlab`。MCP 用于开发和测试，最终仍由独立 batch runner 复现。

## 2. 比赛工作区

不要直接在源码目录混放题面和比赛结果。每道题建立独立工作区：

```powershell
cd <仓库根>
mkdir workspaces\CUMCM-2026-A
mkdir workspaces\CUMCM-2026-A\input
cd workspaces\CUMCM-2026-A
```

将题面和全部附件放入 `input/`：

```text
input/
├── CUMCM-A题.pdf
├── 附件1.xlsx
├── 附件2.csv
└── 其他数据、图片或说明文件
```

建议保留题号和附件编号，不要使用“新建文件1”之类难以辨认的文件名。

## 3. 启动完整工作流

在比赛工作区启动 Codex：

```powershell
codex
```

复制下面的提示词：

```text
$1start-mathmodel

完成 input 目录中的 CUMCM A 题。

竞赛类型：全国大学生数学建模竞赛
论文语言：中文
排版引擎：LaTeX
编程语言：MATLAB

请先读取全部题面和附件，生成 plan.md 和 todo.md。
每完成一个阶段先汇报关键假设、模型、计算结果和风险，得到我们确认后再继续。
所有代码、结果、图表、日志和论文必须保存在当前工作区。
不得编造原始数据、计算结果、引用或参考文献。
```

使用 Python 时，将提示词中的编程语言改为 `Python`。如果没有明确偏好，优先使用 Python。

工作流会依次调用：

1. `$2analysis-modeling`：分析题意并建立数学模型。
2. `$3coding-visual`：编写和运行 Python/MATLAB，生成结果和数据图。
3. `$4drawio`：绘制技术路线、算法流程和模型结构图。
4. `$5writing`：使用比赛模板撰写 LaTeX 论文（Typst 可选保留）。
5. `$6verity`：复现代码、核对数值、编译并验收论文。

## 4. 产物目录

流程执行后应得到：

```text
.
├── input/                         # 原始题面和附件
├── plan.md                        # 总体方案、语言和引擎选择
├── todo.md                        # 阶段进度
├── reports/
│   ├── ANALYSIS_MODELING_REPORT.md
│   ├── RESULTS_REPORT.md
│   ├── DRAWIO_REPORT.md
│   └── VERIFY_REPORT.md
├── code/                          # problem1.py 或 problem1.m 等
├── results/                       # CSV、MAT 和统计结果
├── figures/                       # 数据图、流程图及 PDF
└── paper/
    ├── main.tex 或 main.typ
    └── sections/
```

`plan.md` 是整个项目的配置来源。需要改变语言、排版引擎或模型路线时，先修改 `plan.md`，再要求 Codex 继续。

## 5. 七个人工检查点

`$1start-mathmodel` 与 `$6verity` 共用同一套人工检查点，共七个，Agent 不得自批。其中 **intake** 与 **literature（按需）** 在启动阶段完成，其余 **contract、model、results、paper、submission** 五个是流程中的确认节点：

| 检查点 | 阶段 | 何时确认 |
|--------|------|----------|
| intake | 启动 | 题面、规则、附件已盘点并核验，材料完整 |
| literature | 启动（按需） | 仅启用文献阶段时需要；未启用记 N/A |
| contract | 分析建模 | 题意合同、ReqID、固定量/决策量已冻结 |
| model | 分析建模 | 模型假设、公式、约束、基线与风险已确认 |
| results | 编程计算 | 计算结果、图表与异常项已确认 |
| paper | 论文撰写 | 论文结构、表述、引用与排版已确认 |
| submission | 最终验收 | 提交包、哈希与官方格式已确认 |

关系：`intake` 与 `literature` 是启动阶段的两个前置门禁；`contract`、`model`、`results`、`paper`、`submission` 是贯穿流程的五个确认节点。二者合计为七个检查点。

### 5.1 最少操作：三段可复制提示词

如果时间紧张，至少保留下面三段提示词（即原来的“三个人工确认节点”）。

#### 5.1.1 建模方案确认

分析阶段完成后输入：

```text
暂停后续流程。用一页内容解释每个子问题的模型、选择理由、
关键假设、约束、风险和备选方案，等待队员确认。
```

重点检查题意、量纲、约束、假设合理性和模型复杂度。

#### 5.1.2 计算结果确认

首次计算完成后输入：

```text
暂时不要写论文。检查 reports/RESULTS_REPORT.md、results 和 figures，
列出异常值、约束违反、模型不稳定性、缺失实验和需要人工确认的数据。
```

所有论文数字都必须能够追溯到结果文件或程序输出。

#### 5.1.3 提交前确认

论文完成后输入：

```text
$6verity

执行最终验收：重新运行全部代码，核对论文中的数字、表格和图表，
双遍编译 LaTeX，检查 PDF 和提交文件，生成 reports/VERIFY_REPORT.md。
遇到可疑结果先报告，不要静默篡改。
```

只有 `VERIFY_REPORT.md` 没有阻断问题时，才制作提交包（见第 13 节）。

## 6. 单独重跑某个阶段

不需要每次从头开始。示例：

```text
$2analysis-modeling
重新检查第二问，比较整数规划、动态规划和启发式算法的适用性。
只更新建模报告，先不要编程。
```

```text
$3coding-visual
根据已确认的建模报告重新实现第三问，只修改 code、results、figures
和 RESULTS_REPORT.md，不要改动原始数据。
```

```text
$5writing
使用 CUMCM LaTeX 模板，根据已有报告和计算结果重新生成论文。
论文中不得出现结果文件里不存在的数字。
```

## 7. MATLAB 工作流

MATLAB 脚本应满足：

- 固定随机种子；
- 使用相对路径读取 `input/`；
- 将关键结果写入 `results/*.csv` 和 `results/*.mat`；
- 将论文图表导出为 `figures/*.pdf`；
- 在标准输出中打印可检索的 `RESULT`；
- 不依赖未保存的 MATLAB Workspace 变量。

手动运行单个脚本：

```powershell
python <仓库根>\skills\3coding-visual\scripts\matlab_runner.py `
    code\problem1.m `
    --project-root <仓库根>\workspaces\CUMCM-2026-A `
    --log results\problem1.log
```

检测 MATLAB 或 Octave：

```powershell
python <仓库根>\skills\3coding-visual\scripts\matlab_runner.py --check
```

## 8. 环境诊断

在 Codex 中输入：

```text
$doctor
检查当前工作区所需的 Python、MATLAB、LaTeX 和 PDF 工具。
```

也可以直接运行：

```powershell
powershell -ExecutionPolicy Bypass -File <仓库根>\skills\doctor\scripts\doctor.ps1
```

本机目前适合使用 LaTeX 和 MATLAB/Python；DrawIO 不可用时允许工作流跳过非必要的 DrawIO 导出。排版引擎推荐 LaTeX（xelatex），Typst 仅作可选保留。

## 9. WebUI 服务管理

WebUI 是可选入口；完整 Codex Skills 工作流仍建议从比赛工作区运行 Codex。

```powershell
# 启动
powershell -ExecutionPolicy Bypass -File <仓库根>\scripts\start-local.ps1

# 状态
powershell -ExecutionPolicy Bypass -File <仓库根>\scripts\status-local.ps1

# 停止
powershell -ExecutionPolicy Bypass -File <仓库根>\scripts\stop-local.ps1
```

## 10. 队友协作

`workspaces/` 已被主项目忽略，不会误上传赛题、比赛数据和中间结果。可以在具体工作区中建立单独的队伍私有仓库：

```powershell
cd <仓库根>\workspaces\CUMCM-2026-A
git init
git add .
git commit -m "Initialize CUMCM workspace"
```

推荐分工：

- 队员 A：审查题意、假设、模型和公式；
- 队员 B：运行代码、检查数据、结果和灵敏度；
- 队员 C：检查论文结构、表述、引用和排版；
- Codex：维护跨阶段文件、执行程序和进行一致性验收。

题面和比赛数据不要上传到本项目的公开仓库。项目本身遵守 `docs/md/License.md`：个人非商业使用、禁止闭源分发、不可在其基础上提供商业服务。

## 11. 比赛中应避免的做法

- 不经队员检查就接受模型假设；
- 只生成代码而不实际运行；
- 手工修改论文数字但不更新结果文件；
- 在 MATLAB 和 Python 之间无说明地混用同一子问题；
- 使用无法核实的参考文献；
- 将 API Key、`.env.dev`、日志或未公开赛题上传到公开仓库；
- 只检查源文件，不检查最终 PDF；
- 在提交前跳过 `$6verity`。

## 12. 多 Agent、空间问题和高性能计算

长题或希望交叉复核时，在工作区对 Codex 输入：

```text
$mathmodel-orchestrator
以 Codex 为总控，为题意核对、两个独立建模候选、挑战者、数值验证和论文双审查建立隔离上下文包。不得让任何 AI 自批门禁。
```

DeepSeek 等外部 API 暂时只适合做 reviewer。不要把 Key 写入文件；需要时只在当前 PowerShell 会话设置：

```powershell
$env:MATHMODEL_REVIEWER_API_KEY = "由队员自行粘贴，不要提交"
```

空间题触发：

```text
$mathmodel-spatial
从题面冻结二维/三维坐标、单位、轴顺序、距离度量、角度制和容差，随后审计距离矩阵、邻接、轨迹、覆盖和图表语义。
```

需要 PSO 或大量计算时：

```text
$mathmodel-algorithm-lab
先按变量、约束、凸性、是否可微和评估成本比较精确法、基线和元启发式，再建立多种子实验计划。当前电脑只用 weak-dev，迁移 i9 + RTX 4060 后按实际探测能力扩大。
```

> 注意：`mathmodel-algorithm-lab` 的示例 plan（`assets/experiment_plan.example.json`）中脚本路径指向 skill 目录（`skills/mathmodel-algorithm-lab/scripts/pso_runner.py`），而 `experiment_runner.py` 要求任务脚本在 `--project-root`（项目根）之内。使用示例 plan 时，需先把 skill 的 `scripts/pso_runner.py` 复制到工作区 `code/` 下并把 argv 改为该路径，或直接以仓库根为项目根运行。

算法候选文件还会生成与题型相关的 `research_queries`。先让文献检索阶段核验原始论文、适用条件和失败模式，再让实验运行器按 `weak-dev`、`balanced` 或 `i9-4060` 预算执行；高配常规档最多允许 12 个 worker、512 个显式 run 和单 run 两小时。另有人工显式选择的 `i9-4060-long`（20 worker、2048 run、单 run 六小时），仅适合散热、内存、磁盘和 MATLAB 许可检查通过后的无人值守长跑。完成后必须运行 `aggregate_experiments.py` 汇总成功率、可行率、中位数、四分位数和耗时，不能只挑最佳 seed。

长论文希望 DeepSeek 等模型参与独立复核时，初始化写作团队加上 `--require-heterogeneous-review`。没有配置第二模型时不要伪造异构审查，保留为 `UNVERIFIED`，由队员人工复核。

PSO 是随机黑箱优化器，不代表自动理解了题目，也不能单独证明全局最优。无论使用多少算力，都要保留小规模精确核对、约束复算和所有失败/超时记录。

> 短硬截止（约 ≤10 小时）默认关闭文献严格模式（`mathmodel-literature-research`）与多 Agent 编排（`$mathmodel-orchestrator`），除非用户明确要求；关闭后不得引用未核验的外部来源，且对应门禁记 N/A。

## 13. 提交包制作与检查

CUMCM 客户端提交的要素以**当届官方提交说明与模板**为准，Agent 不得用往年模板或自行拼装。至少核对以下四项：

- **论文 PDF 命名规范**：按当届官方要求命名（通常与队号或指定编号相关），不要用 `main.pdf` 之类的裸文件名；
- **承诺书 / 编号页**：使用当届官方模板生成的 PDF，不自行改写版式；
- **支撑材料 zip**：可含代码、数据与说明，但**不得包含论文正文 PDF**；
- **AI 使用声明**：按当届规则填报，并保留 `reports/AI_USAGE_LOG.jsonl` 作为依据。

提交包门禁由 `$6verity` 的 `skills/6verity/scripts/validate_submission_bundle.py` 承担。它只读声明的提交文件、检查结构并记录每个文件的 SHA-256，**Agent 只生成清单与哈希，上传与最终提交由队员在官方客户端完成**。

先写一份配置文件，例如 `reports/submission_bundle_config.json`：

```json
{
  "schema_version": 1,
  "competition": "cumcm",
  "paper_pdf": "paper/main.pdf",
  "paper_pdf_name_pattern": "^[0-9]{8,}[_-].*\\.pdf$",
  "commitment_page": "submission/commitment.pdf",
  "numbering_page": "submission/numbering.pdf",
  "support_material_zip": "submission/support.zip",
  "support_material_forbid_extensions": [".pdf"],
  "ai_usage_declaration": "reports/AI_USAGE_LOG.jsonl"
}
```

运行命令（在比赛工作区目录内执行）：

```powershell
python <仓库根>\skills\6verity\scripts\validate_submission_bundle.py `
    --root . `
    --config reports\submission_bundle_config.json `
    --output reports\SUBMISSION_BUNDLE.json
```

脚本返回 `PASS` 且 `reports/VERIFY_REPORT.md` 无阻断问题时，把生成的清单与哈希交给队员，由队员在官方客户端上传；Agent 不得代为上传或勾选。

## 14. 赛前全流程演练（强烈建议）

正式比赛前，用一道历史真题在 `workspaces/` 下新建目录，把 `1start → 2analysis → 3coding → 5writing → 6verity` 全流程完整跑一遍并计时，用于校准真实时间预算：

1. `mkdir workspaces\dry-run && cd workspaces\dry-run`，放入历史真题与附件；
2. 调用 `$1start-mathmodel`，按真实偏好选择引擎与语言；
3. 依次跑完 `$2analysis-modeling`、`$3coding-visual`、`$5writing`、`$6verity`；
4. 至少完成一次 LaTeX（xelatex）编译验证，确认论文模板在**本机**能出 PDF；
5. 记录每阶段实测耗时，形成自己的时间预算（验收至少预留总预算 20%）。

LaTeX 模板已修复（宏包已齐），推荐直接使用 LaTeX（xelatex）；Typst 仅作可选保留。**选择前先实测编译**——不要默认引擎可用，以 `$doctor` 与本机首次编译结果为准。演练中暴露的路径、字体、宏包或运行时问题，正式比赛前都要解决并记录到演练报告。

## 15. 最短操作清单

```text
1. 赛前 dry run：历史真题跑通 1start→…→6verity 并计时
2. 新建 workspaces/CUMCM-年份-题号/input
3. 放入题面和附件
4. 在工作区运行 codex
5. 调用 $1start-mathmodel
6. 确认 plan.md 和建模报告
7. 确认计算结果和图表
8. 生成论文
9. 调用 $6verity
10. 队员人工通读最终 PDF
11. 提交包检查：运行 validate_submission_bundle.py 生成清单与哈希
12. 队员在官方客户端上传正式提交包
```
