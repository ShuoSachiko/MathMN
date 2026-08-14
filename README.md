# MathModelAgent 📐

<p align="center"><b>数学建模竞赛的 AI 工作流：一套以完整性为底座的 Codex/DSH 技能链</b></p>

> **诚实定位**：本仓库不是"一键出论文"的自动驾驶工具，而是一套给**有纪律的人类队伍**用的重型质量护栏——题意合同、哈希传播、证据账本、防伪造人工签认、可复现验收。已通过三题实战演习验证（详见下文），当前为 **beta** 阶段。任何全自动运行的结果都只能标 `UNVERIFIED`，不能声称"比赛提交就绪"。

---

## ✨ 功能特性

- **完整性门禁链**：输入白名单 + SHA-256 指纹、题意合同（固定量/决策量分离）、阶段门禁与 STALE 传播、结论—证据账本、七个人工检查点（AI 不得自批）；
- **八阶段工作流**：文献检索（按需）→ 题意分析与建模（`contract_lint` 冻结前校验）→ 编程与图表（含候选算法对比实验）→ 流程图 → 论文撰写（Typst/LaTeX 双引擎，34 套模板 + 防误提交哨兵）→ **论文评审打磨环**（评分卡 + 多版本摘要）→ 最终验收（完整性/文本/提交包/编译/视觉九道检查）；
- **Python + MATLAB 双轨**：`matlab_runner` 四重门禁（退出码/完成标记/RESULT 行/产物新鲜度），固定种子与精确版本记录，跨版本复现差异处置流程；
- **提交包校验**：论文 PDF、支撑材料 zip、提交工作簿逐格 schema、AI 使用日志、MD5 清单；
- **DSH 集成**：`dsh/preset-mathmodel/` 提供数学建模 Agent 预设（本机已通过 mount 校验；安装方式为手动复制到用户预设根，见下方"路线 B"）。**注意**：DSH 没有插件市场，当前也尚未打包成官方 profile bundle——该形态待官方"包内 skill 资源路径"能力闭合后补充；
- **隔离评测**：`7benchmark` 封存/验封协议，历史公开题按污染敏感性处理，不宣称盲测能力。

## 🚀 快速开始

### 路线 A：Codex + 技能链（推荐）

```powershell
git clone https://github.com/ShuoSachiko/MathMN.git
cd MathMN
powershell -ExecutionPolicy Bypass -File scripts/setup-codex.ps1   # 建立 .agents/skills 联结
mkdir workspaces\my-problem
cd workspaces\my-problem
codex   # 输入：$1start-mathmodel 完成这个数学建模任务
```

环境检查用 `$doctor`；需要 MATLAB 时先跑 `python skills/3coding-visual/scripts/matlab_runner.py --check`。

### 路线 B：DeepSeek Harness 安装

本仓库提供 DSH 的"数学建模"Agent 预设（已在本机通过 mount 校验）。安装分三步：拿仓库 → 建技能联结 → 装预设。

**第 1 步：获取仓库**

```powershell
git clone https://github.com/ShuoSachiko/MathMN.git
cd MathMN
```

**第 2 步：建立技能发现联结**（技能链靠工作区内的 `.agents/skills` 被发现，会话工作目录要在这个仓库内或其 `workspaces/` 子目录下）

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup-codex.ps1
```

**第 3 步：安装预设**

Windows PowerShell：

```powershell
mkdir "$env:USERPROFILE\.dsh\.agent-presets\mathmodel" -Force
Copy-Item dsh\preset-mathmodel\* "$env:USERPROFILE\.dsh\.agent-presets\mathmodel" -Force
```

macOS / Linux：

```bash
mkdir -p "${DSH_HOME:-$HOME/.dsh}/.agent-presets/mathmodel"
cp dsh/preset-mathmodel/* "${DSH_HOME:-$HOME/.dsh}/.agent-presets/mathmodel/"
```

**第 4 步：新建会话并验证**

1. 在 DSH 新建会话时，预设列表中选择 **数学建模**；
2. 会话工作目录设为仓库根目录（或 `workspaces/` 下某题工作区）；
3. 验证成功：会话技能目录里能看到 `1start-mathmodel`、`2analysis-modeling`、`6verity` 等技能；输入 `$1start-mathmodel 完成这个数学建模任务` 即进入工作流。

**从 GitHub 发现本插件**：本仓库打有 `dsh-plugin` 话题标签，可在 <https://github.com/topics/dsh-plugin> 中找到。

> **一键安装（profile bundle）待办**：DSH 官方的一键安装形态为 `dsh plugin --profile <name> add <git-spec>`（profile bundle）。当前版本对"包内随附 skill 目录"的声明式路径仍有官方列名的覆盖缺口，因此本仓库暂以"复制预设文件"方式安装；待能力闭合后补充 `dsh/bundle/` 并支持一条命令安装。详见 [dsh/README.md](dsh/README.md)。

## 🔁 工作流

```
1start-mathmodel（总控）→ mathmodel-literature-research（按需）
→ 2analysis-modeling（题意合同 + contract_lint + 候选池）
→ 3coding-visual（可复现代码 + 对比实验 + 图表）
→ 4drawio（非数据图）→ 5writing（论文）
→ mathmodel-review-polish（评审 + 摘要多版本 + 迭代）
→ 6verity（九道验收，产出 VERIFY_REPORT）
```

每个阶段产物可机器校验，输入或上游哈希变化时下游自动标 `STALE` 并重跑。

## 🧪 三题实战演习（2026-08，已封存）

用三个真实题目快照（2024-A 板凳龙、2025-A、2025-B 碳化硅）对流水线做了端到端验证：

- **三篇中文论文全部 xelatex 编译成功**，提交包校验 PASS，21 个人工检查点零伪造；
- **数值复现**：两题干净复现 PASS；2025-A 暴露了 MATLAB 版本更新（Update 3→4）导致固定种子启发式漂移——已按规程处置并固化"精确版本记录"义务；
- **演习修复**：root_hash 双口径统一、LaTeX 图片路径约定修正、runner 编码修复、沙箱环境文档化；
- 完整结论与逐题报告见 `workspaces/EXERCISE_SUMMARY.md`（演习工作区默认不入库）。

## 📁 目录结构

```
skills/                     # 全部 16 个技能（含共享规范 _references）
scripts/                    # 安装/启动脚本（setup-codex、setup-local 等）
dsh/                        # DeepSeek Harness 预设与发布说明
backend/  frontend/         # 历史遗留 WebUI（demo 阶段，见 升级说明.md）
docs/                       # 文档
```

> WebUI 后端为历史遗留产品：Web Search/RAG/HIL/Evaluator/Fallback 均未实现或仅配置壳，架构级缺陷（解释器无沙箱隔离、无断点续跑、5 小时硬超时）未修复。**比赛请走技能链路线**，详见 [升级说明.md](升级说明.md)。

## ✅ 验证

```powershell
# 技能单元测试（stdlib，共 15 个文件）
python skills/1start-mathmodel/scripts/test_project_guard.py
python skills/2analysis-modeling/tests/test_contract_lint.py
python skills/6verity/tests/test_integrity_check.py
# ... 完整清单见 AGENTS.md 或 .github/workflows/ci.yml

# 模板校验 / MATLAB 探测
python skills/5writing/scripts/validate_templates.py
python skills/3coding-visual/scripts/matlab_runner.py --check
```

CI（GitHub Actions）在 push 后自动运行：13 个技能测试文件、模板静态校验、`bash -n writing_check.sh`、后端 ruff 与 compileall。

## ⚠️ 环境注意

- 受限沙箱（如 DSH 默认模式）下 MATLAB 与 xelatex/MiKTeX 装包需要更宽执行权限，子代理会话通常无法扩权——见 `skills/doctor/SKILL.md` 的"沙箱化 Harness 环境"节；
- LaTeX 论文统一从 `paper/` 目录编译，图片路径用 `../figures/`（Typst 例外，保持文件相对路径）。

## 📄 许可证

[PolyForm Noncommercial License 1.0.0](./LICENSE)：源码开放，个人/学术/研究等非商业用途免费；**商业用途请联系作者单独授权**。

## 🙏 致谢

本项目基于 [jihe520/MathModelAgent](https://github.com/jihe520/MathModelAgent) 的技能工作流持续开发；模板、完整性框架与多智能体设计承袭上游。感谢以下项目：OpenCodeInterpreter、TaskWeaver、Local-Code-Interpreter、MathModelingLatexTemplate、Agent Laboratory。
