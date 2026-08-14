---
name: mathmodel-spatial
description: "数学建模空间、几何与时空问题工具链。用于题目含二维/三维坐标、经纬度、距离矩阵、邻接网络、轨迹、覆盖、可见性、路径或空间优化时；先冻结坐标系和单位，再用确定性脚本检查点集、距离、拓扑、轨迹及覆盖语义，支持 Python 与 MATLAB 工件且不包含任何历史赛题答案。"
---

# 空间建模工具链

空间问题先解决“坐标和几何语义是否正确”，再优化数值。不要从图像外观或变量名猜坐标系、单位、角度制和距离度量。

## 进入条件

读取 `PROBLEM_CONTRACT.json`、附件字段与题面中的坐标定义。若存在位置、方向、距离、区域、轨迹、覆盖、邻接或 2D/3D 几何，创建 `reports/SPATIAL_CONTRACT.json` 并在分析阶段触发本 skill。

完整检查清单见 [spatial-modeling.md](references/spatial-modeling.md)。

## 冻结空间合同

笛卡尔二维示例：

```bash
python "<本 skill>/scripts/spatial_audit.py" init-contract \
  --output reports/SPATIAL_CONTRACT.json --coordinate-system cartesian \
  --dimension 2 --axis x --axis y --unit m --distance-metric euclidean \
  --tolerance 1e-9
```

经纬度必须声明 `geographic`、轴顺序、角度单位和 CRS；不得直接把经纬度差代入欧氏距离。题面没有给出 CRS、垂直基准或局部原点时写 `unknown` 并保留为风险，不能凭经验补造。geographic 合同当前仅支持二维（经纬度）；三维经纬度+高程暂不支持，需先降维/投影或人工处理。

## 检查点集和轨迹

```bash
python "<本 skill>/scripts/spatial_audit.py" points \
  --contract reports/SPATIAL_CONTRACT.json --csv input/points.csv \
  --id id --coord x --coord y --output results/spatial_points_audit.json

python "<本 skill>/scripts/spatial_audit.py" trajectory \
  --contract reports/SPATIAL_CONTRACT.json --csv input/track.csv \
  --id object_id --time t --coord x --coord y --coord z \
  --max-speed 100 --output results/spatial_trajectory_audit.json
```

点检查覆盖缺失/非有限值、重复 ID、重复坐标、范围和经纬度合法性。轨迹另外检查时间严格递增、零时间跳跃和速度上限；速度上限只有来自题面或已核验来源时才可设置。

## 检查距离与拓扑

```bash
python "<本 skill>/scripts/spatial_audit.py" distance \
  --csv results/distance_matrix.csv --labels --tolerance 1e-8 \
  --output results/spatial_distance_audit.json

python "<本 skill>/scripts/spatial_audit.py" adjacency \
  --csv input/adjacency.csv --labels \
  --output results/spatial_adjacency_audit.json
```

距离矩阵检查方阵、非负、零对角、对称和三角不等式。邻接矩阵检查 0/1、对角和无向对称性，并报告连通分量。题意允许有向图或自环时显式传入对应选项。

## 核验覆盖结论

```bash
python "<本 skill>/scripts/spatial_audit.py" coverage \
  --contract reports/SPATIAL_CONTRACT.json \
  --demand input/demand.csv --facility results/facilities.csv \
  --demand-id id --facility-id id --coord x --coord y --radius 500 \
  --weight population --output results/spatial_coverage_audit.json
```

覆盖脚本适用于欧氏二维/三维的点覆盖证据，输出未覆盖点、加权覆盖率和冗余度；它不是连续区域覆盖证明，也不处理遮挡。连续区域、视线、曲面或多边形边界必须另给几何证书和采样误差界。

## MATLAB

可从 `assets/matlab/` 复制 `mm_pairwise_distance.m` 和 `mm_validate_distance_matrix.m` 到项目 `code/`。优先使用 MATLAB Agentic Toolkit 的 Code Analyzer 和 tests；不可用时使用 `$3coding-visual` 的批处理 runner。MATLAB 与 Python 的坐标轴顺序、容差和输出单位必须回到同一个 `SPATIAL_CONTRACT.json`。

## 建模与验收规则

- 每张空间图显示坐标轴、单位、方向、比例和必要的 CRS/投影说明。
- 2D 图不能证明 3D 不相交、可见或可达；必须核验原维度约束。
- 采样可行不等于连续可行；报告网格尺度和漏检风险。
- 空间优化必须分离固定几何量、决策位置/方向、状态和 solver 控制。
- 至少构造一个刚体变换/平移旋转不变量测试，以及一个边界反例。
- 把 audit JSON 登记到 `validation_manifest.json` 和 `claim_ledger.json`；任何 FAIL 阻止对应 claim 进入论文。
