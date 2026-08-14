---
name: mathmodel-algorithm-lab
description: "数学建模算法检索、候选筛选和可扩展实验执行。用于优化、预测、仿真、聚类或空间问题需要比较 PSO、差分进化、数学规划、局部优化、启发式算法和基线时；根据问题结构生成候选而非追逐算法名称，并在弱机/高性能笔记本配置下执行多种子、并行、限时和可追溯实验。"
---

# 数学建模算法实验室

算法不能修复错误的题意或目标函数。先冻结 `PROBLEM_CONTRACT.json` 和模型公式，再按变量类型、约束、光滑性、维度、噪声和单次评估成本筛选求解器。

## 先检索，再进入候选池

调用 `$mathmodel-literature-research` 检索与当前问题结构直接相关的算法、基线和验证方法。记录原始论文/官方文档、适用条件和失败模式；“往年有人用某算法”只作为待核线索，不作为当前模型证据，也不得读取答案派生参数。

算法选型卡见 [algorithm-selection.md](references/algorithm-selection.md)，机器可读目录位于 `assets/algorithm_registry.json`。

```bash
python "<本 skill>/scripts/algorithm_selector.py" \
  --registry "<本 skill>/assets/algorithm_registry.json" \
  --variable continuous --objective single --constraints nonlinear \
  --differentiable no --convex unknown --evaluation-cost medium \
  --domain "三维覆盖与路径规划" \
  --output reports/ALGORITHM_CANDIDATES.json
```

输出中的 `research_queries` 是下一步交给 `$mathmodel-literature-research` 的检索计划，不是文献证据本身。只把经原始论文或官方文档核验、且适用条件匹配当前合同的方法加入实验矩阵。

候选必须至少包含：可解释/确定性基线、主算法、结构不同的替代算法。能用线性规划、凸优化、动态规划或枚举证书解决时，不要因为 PSO 流行就优先使用元启发式。

## PSO 基线

仓库提供无额外 PSO 包依赖的 NumPy 全局最优 PSO，用于连续有界问题和合成回归：

```bash
python "<本 skill>/scripts/pso_runner.py" --benchmark rastrigin \
  --dimension 10 --lower -5.12 --upper 5.12 --particles 80 \
  --iterations 500 --seed 1 --output results/pso-seed-1.json
```

真实赛题通过 `--objective-module` 加载项目内适配器。函数接收形状为 `(particles, dimensions)` 的 NumPy 数组，返回 objective 数组，或 `(objective, nonnegative_violation)`。比较采用 feasibility-first；输出只称“本次运行找到的最好可行解”，不得声称全局最优。

## 计算配置

先探测当前机器：

```bash
python "<本 skill>/scripts/compute_profile.py" --output reports/COMPUTE_CAPABILITIES.json
```

`assets/compute_profiles.json` 提供：

- `weak-dev`：当前弱机，单 worker、小种群和少量种子，用于接口/回归。
- `balanced`：普通竞赛运行，保留操作系统和论文编译资源。
- `i9-4060`：未来笔记本，多进程 CPU 和可选 CUDA；仍以实际探测值为上限。
- `i9-4060-long`：人工显式选择的离线长跑档，最多 20 worker、2048 个 run、单 run 6 小时；先检查内存、散热、MATLAB 许可和磁盘空间，比赛前台工作期间不要启用。

不要在弱机上伪装完成大规模搜索。把未运行组合写进计划，状态标为 `UNVERIFIED`，迁移后用相同输入和代码哈希继续。

## 预算化多种子实验

复制 `assets/experiment_plan.example.json` 并显式列出每个无 shell 命令。Python 任务必须指向工作区内明确的 `.py` 文件，禁止 `-c`、`-m` 和标准输入脚本。运行器限制 run 数、worker 数和单次超时，分别保存 stdout/stderr、退出码、时长、实际资源预算和产物哈希。

```bash
python "<本 skill>/scripts/experiment_runner.py" \
  --project-root . --plan experiments/plan.json \
  --profiles "<本 skill>/assets/compute_profiles.json" \
  --output results/experiment_manifest.json
```

运行结束必须聚合跨种子统计，而不是只挑最好的一次：

```bash
python "<本 skill>/scripts/aggregate_experiments.py" \
  --project-root . --manifest results/experiment_manifest.json \
  --output results/experiment_aggregate.json
```

聚合器报告每种算法的成功数、可行率、最佳值、中位数、四分位数和耗时中位数，并把结论强度限定为“已记录运行的描述统计”。

## 必做比较

- 相同目标、约束、评估预算、种子集合和停止条件。
- 报告最佳值之外的中位数、分位数、失败率、可行率、时间和评估次数。
- 对惩罚系数、种群规模和初始化范围做消融；避免只报最好的一次 seed。
- 小规模问题用枚举/精确求解器核对；连续问题检查约束残差和局部精修。
- 预测任务不得把优化器调参结果当作独立测试；保持外层测试集隔离。
- 若 GPU 只加速单次评估而不是算法本身，明确区分模型训练与搜索并行度。

把 `ALGORITHM_CANDIDATES.json`、compute capabilities、experiment manifest 和聚合统计加入 `run_manifest.json` 与 `validation_manifest.json`，再交给 `$6verity`。
