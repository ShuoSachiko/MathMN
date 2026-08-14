---
name: 3coding-visual
description: "数学建模编程求解与数据图表阶段。根据冻结的题意合同和建模报告使用 Python 或 MATLAB/Octave 实现可复现代码，逐 ReqID 验证语义、约束和数值证据，生成结论账本、验证与运行清单、RESULTS_REPORT.md 和论文可用图表。"
---

# 编程实现与数据图表生成

本 skill 承接 `2analysis-modeling`。目标是把 `reports/ANALYSIS_MODELING_REPORT.md` 里的模型和算法落实为可复现程序，跑出可信结果，并生成论文中需要的数据型图表。

## 数学建模规范参考

必须读取 `../_references/modeling_integrity.md` 的“两条验证链”“优化、搜索与全局最优”“结论—证据账本”和“上下文压缩”；如需领域判断，再读取 `../_references/math_modeling_norms.md` 中与当前题型相关的章节。

## 阶段边界

- 本阶段负责：代码、实验运行、结果、结果表、数据驱动图表。
- 本阶段不负责：技术路线图、算法流程图、系统架构图、概念示意图。这些交给 `4drawio`。
- 本阶段不写论文正文，只为 `5writing` 提供可信数值和图表资产。
- 本阶段必须生成 `results/claim_ledger.json`、`results/validation_manifest.json` 和 `results/run_manifest.json`；`RESULTS_REPORT.md` 是可读解释，不能替代机器账本。

### Step -1: 前置门禁

开始前确认：

- `reports/PROBLEM_MANIFEST.json` 哈希有效，`PROBLEM_CONTRACT.json` 状态为 `FROZEN`；
- intake 与 analysis gate 均为 `PASS`，合同哈希与 analysis gate 记录一致；
- `HANDOFF.json` 没有把当前模型或数据标为作废；
- 当前模式允许使用的输入和工具已在 `PROVENANCE.md` 登记。

任一条件缺失、`STALE` 或 `FAIL` 时不得求解。先把 coding gate 标为 `RUNNING`。


### Step 0: 确定语言和运行时

先读取 `plan.md` 的“编程语言”和“MATLAB 运行时”。未记录时询问用户；用户跳过时默认 Python。

- Python：生成 `code/problemN.py`，使用当前 Python 环境运行。
- MATLAB：生成 `code/problemN.m`。先运行 `python <本 skill>/scripts/matlab_runner.py --check`；优先 MATLAB，只有代码不依赖 MATLAB 专属工具箱且通过 Octave 实测时才使用 Octave。
- 混合语言：仅在确有必要且用户同意时使用，并在 `RESULTS_REPORT.md` 记录每个文件的语言、运行时和数据交换格式。

MATLAB 运行时不可用时不得伪造执行结果。报告缺失项并调用 `$doctor` 给出安装指引。

### Step 1: 代码结构

按 `plan.md` 中“项目目录结构”创建 `code/`、`code/outputs/`、`results/` 和 `figures/` 骨架，再开始写代码。子问题数不一定是 3，按赛题实际数量调整。MATLAB 项目可从 `assets/matlab/problem_template.m` 复制起步，但必须替换示例计算和输出文件名。


### Step 2: 逐子问题实现

按 `REQ-*` 的依赖顺序实现，不要一次性写完不跑。每个程序入口或配置文件都要声明覆盖的 ReqID、读取的白名单输入、使用的决策变量和输出 schema。

每个子问题必须完成：

1. 读取所需数据。
2. 实现模型或算法。
3. 验证约束。
4. 输出核心结果。
5. 绘制丰富的图表。
6. 在 `reports/RESULTS_REPORT.md` 中写清楚方法、关键数值和校验结果。

每次实现还必须执行题意合同测试：

- 代码中的决策变量集合不得超出合同允许集合；
- 固定量、初边界条件、对象、时域、样本范围、单位和输出位置不得静默改变；
- 控制参数（网格、容差、随机种子、迭代次数）与现实决策变量分开；
- 每个必交输出都映射到明确 ReqID、结果键和生成文件。

发现合同不适用时停止编码，按 `DECISION_LOG.md` 的变更流程退回分析阶段；不得在代码注释里悄悄建立第二套题意。

优化类问题必须先保证可行解，再优化目标值。预测类问题必须做无泄漏的训练/验证划分或合理误差评估。评价类问题必须说明指标方向、归一化方法和权重来源。机理/仿真类问题必须检查守恒、量纲或已知极限。以上是按适用条件触发的验证配置，不规定首选算法。

逆问题/参数反演还必须检查：前向模型的解析极限或合成参数回收；目标参数相对干扰参数的可识别性/条件性；至少一个简单、结构不同的基线或跨方法对照；数据窗口、预处理、初值和关键外部参数的敏感性；统计不确定性与系统参数不确定性分开报告。频谱或周期估计要显式检查谐波、倍频/分频别名和参数边界，不能因多个同源导出文件一致就视为反演正确。

### 搜索域、事件与结论强度

- 所有搜索边界、仿真终止条件和时间窗都要来自题面、可行性条件、解析界或可复核的域扩张证据；禁止无解释的“足够大”常数。
- 首次事件不能只凭粗采样点判断；使用区间包围/根求解，或步长收敛与相位偏移检查，防止短暂事件和切触漏检。
- 一维/低维优化显式检查边界、全部候选区间和分支切换；高维优化报告多起点、界或 gap 等实际证据。
- 只有有证明、严格界、穷举证书或有保证的求解器 gap 时，才登记为 `claim_type=optimization`，并将 `epistemic_status` 标为 `proved`、`exact_solver_gap` 或 `certified_numerical`。否则登记为 `claim_type=heuristic`、`epistemic_status=best_found`，正文写“搜索到的最优可行解”等受限表述。
- 保存步长/网格/容差/样本量收敛表和域扩张记录，不能只保存最后一个好看的数值。

### MATLAB 实现约定

- 每个 `problemN.m` 必须可从项目根目录独立、无交互运行；不要依赖当前编辑器工作区中的变量。
- 固定随机种子，例如 `rng(2025, "twister")`，并在结果报告记录 MATLAB/Octave 版本和所需工具箱。
- 使用 `readtable`/`writetable` 交换表格，用 `save(..., "*.mat")` 保存关键工作区变量；结果同时输出为 CSV/JSON 等可检查格式，不能只留在 `.mat` 中。
- 使用 `fullfile` 和 `mfilename("fullpath")` 构造路径，避免写死盘符或用户目录。
- 图表使用 `exportgraphics(..., "ContentType", "vector")` 导出 PDF；若 Octave 不支持该调用，使用经过实测的 `print(..., "-dpdf")` 兼容分支。
- 把关键指标以稳定前缀输出到 stdout，例如 `RESULT objective=...`，并把完整日志写入 `code/outputs/`。

推荐执行方式：

```bash
python "$SKILL_DIR/scripts/matlab_runner.py" code/problem1.m \
  --project-root . --runtime auto --log code/outputs/problem1.log \
  --require-result --expected-artifact results/problem1.json
```

该脚本不通过 shell 拼接命令，为每次运行创建独立 MATLAB 偏好目录，并要求退出码、唯一完成标记、`RESULT` 行和新鲜产物同时通过。非零退出码即使已打印完成标记也必须失败；超时日志要保留。

### Step 3: 结果文件格式


AI 在实现、求解和作图过程中，必须把关键中间过程保存成数据并做好记录，例如清洗后的数据摘要、模型参数、迭代历史、约束检查、灵敏度分析过程、图表所用数据和运行日志。中间数据优先保存到 `figures/` 或 `code/outputs/`，并在 `reports/RESULTS_REPORT.md` 中说明文件用途。

`reports/RESULTS_REPORT.md` 推荐结构：

```markdown
# 计算结果

## 运行环境
## 数据读取与预处理
## 问题一结果
## 问题二结果
## 问题三结果
## 灵敏度分析
## 约束与一致性校验
## 与建模报告的一致性说明
## 可复现运行方式
```

运行环境必须明确列出：编程语言、解释器版本、操作系统、随机种子、依赖包或 MATLAB 工具箱。MATLAB 结果还要记录实际使用的是 MATLAB 还是 Octave。

所有数据和图表结果都必须出现在 `reports/RESULTS_REPORT.md` 中引用

### Step 3.5: 结论账本与独立验证

在 `results/claim_ledger.json` 中逐条登记关键结论：`id`、`contract_refs`、`statement`、`claim_type`、`epistemic_status`、`status`、数值/单位/精度、`decision_variables_used`、`changed_fixed_quantities`、证据路径、验证 ID 和论文定位。`supported` 结论不得有空证据，`changed_fixed_quantities` 必须为空；暂未验证的结论标 `provisional`，不能写成确定事实。

在 `results/validation_manifest.json` 中记录每项验证的 `id`、`claim_refs`、类型、PASS/FAIL/UNVERIFIED、是否独立于产出路径、证据文件和说明。按声明类型选择最小配置：

- 数值/仿真：单位或量纲、约束/不变量、收敛，以及独立公式/实现、极限情形或变形测试之一；
- 优化：可行性、域覆盖、边界/候选检查；声称全局时另需最优性证书；
- 预测/统计：泄漏检查、真正留出或时间外推评估、简单基线和误差/不确定性；
- 逆问题/参数反演：前向模型/合成回收、可识别性、基线或跨方法/窗口对照、系统参数与不确定性传播；
- 排序/评价：指标方向、权重来源和排序稳定性；
- 证明/定性结论：逐步推导复核或独立反例搜索。

这些是证据类别，不指定具体算法。若某项确实不适用，记录 `not_applicable` 和可审计理由，不能删掉字段。至少一项核心验证应与结果生产路径独立；同一 MAT/JSON 转存的 Excel、图表和论文不能互相充当独立 oracle。

需要比较 PSO、差分进化、局部/全局优化、预测算法或大量超参数时调用 `$mathmodel-algorithm-lab`。算法候选必须来自冻结的问题结构和已核验文献，不得因为往年参赛者提到某算法就直接采用。随机算法至少运行多 seed，并报告可行率、中位数/分位数、失败和超时；小规模精确解或结构不同的基线必须保留。当前机器不足以完成计划时只做 smoke test 并标 `UNVERIFIED`，在高性能机器上按同一 plan/hash 继续。

若空间合同被触发，调用 `$mathmodel-spatial` 对坐标、距离、拓扑、轨迹和覆盖结论生成 audit JSON；任何空间语义 FAIL 都阻止对应 claim 成为 supported。

有多 Agent 时，让数值审计者只接收冻结合同、原始输入和代码，不给目标答案；要求其在干净输出目录复跑并寻找反例、魔法常数、域外改进和精度不收敛。审计发现必须进入 validation manifest。

最后用显式产物列表生成运行清单：

```bash
python "<本 skill>/scripts/build_run_manifest.py" \
  --project-root . --output results/run_manifest.json \
  --artifact "source=code" --artifact "result=results" \
  --artifact "figure=figures" --artifact "log=code/outputs" \
  --command "<实际复现命令>" --runtime "<实际版本>"
```

清单不得自动吸收工作区中的未知资料；它记录相对路径、角色、大小、SHA-256 和根哈希。源代码或输入改变后，旧结果即为 `STALE`。

### Step 4: 生成数据驱动图表

根据 `reports/ANALYSIS_MODELING_REPORT.md` 和 `reports/RESULTS_REPORT.md` 规划图表，生成 PDF 到 `figures/`。

典型图表：

- 预测类：真实值-预测值对比、误差分布、指标对比。
- 优化类：收敛曲线、成本对比、资源利用率、方案前后对比。
- 评价类：综合得分排序、雷达图、热力图、敏感性曲线。
- 数据理解：分布图、趋势图、相关性图、箱线图。

图表要求：

- PDF 矢量输出，适合论文。
- 不在图内写大标题，标题交给论文 caption（Typst 的 `caption:` 或 LaTeX 的 `\caption{}`）。
- 中文论文图表使用中文坐标轴和图例；英文论文使用英文。
- 不生成流程图/架构图/路线图。
- MATLAB/Octave 图表必须保存为文件；不要只依赖交互式 figure 窗口。

图表可以由主程序或独立脚本生成，不强制固定脚本名。无论采用哪种方式，都必须保存图表对应的数据来源和生成记录。

每张论文图应能回到生成器、源数据和 ReqID；无关但“看起来合理”的 PDF、过期图片和整页截图不能仅凭文件存在通过。对存在方案取舍的 ReqID 分别快照代码/结果候选，让人类根据约束、独立验证和风险审查包选择，而不是按目标值最好自动选。完成后更新 `CURRENT_VERSIONS.json`、`HANDOFF.json` 和 coding gate，记录合同哈希、claim/validation/run manifest 哈希、复现命令及未通过项。`human-supervised` 下 results 检查点未 `APPROVED` 不得 PASS；存在关键 `FAIL` 时 gate 为 `FAIL`；只做 smoke test、缺运行时、关键验证未执行或模拟豁免时为 `UNVERIFIED`。

## MATLAB Agentic Toolkit（可选原生路径）

若本机安装了 MathWorks MATLAB Agentic Toolkit，优先用其 MCP 工具完成交互式开发：`detect_matlab_toolboxes` 固定版本/工具箱，`check_matlab_code` 做 Code Analyzer 检查，`run_matlab_test_file` 执行 `runtests`，`run_matlab_file` 或 `evaluate_matlab_code` 调试。只安装当前任务需要的 MATLAB skill groups，避免无关工具污染上下文。

先运行只读探测：

```bash
python "<本 skill>/scripts/matlab_agentic_status.py" --output reports/MATLAB_AGENTIC_STATUS.json
```

Windows 可从仓库根目录运行 `powershell -ExecutionPolicy Bypass -File scripts/setup-matlab-agentic.ps1` 做只读检查；用户明确启用时加 `-Install`。脚本将 MathWorks 官方 toolkit 源码和 MCP server 放进被 Git 忽略的 `.runtime/`，生成同样被忽略的机器专属 `.codex/config.toml`，并只注册 `matlab-core`、数据分析、优化和并行计算四组相关 skills。默认关闭 MCP 遥测；重启 Codex 后用 `/mcp` 核验。

MCP 是开发/测试入口，不是复现证书。最终结果仍必须用本 skill 的 `matlab_runner.py` 在独立偏好目录批处理复跑，记录源代码、运行时、工具箱、日志和新鲜产物。官方 toolkit 不可用时自动保留现有 runner；不得因为 MCP 缺失而回退到伪造 MATLAB 输出。
