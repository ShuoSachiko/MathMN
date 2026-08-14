# 建模 Agent 角色与权限

## 核心角色

| 角色 | 最小输入 | 主要输出 | 写权限 |
| --- | --- | --- | --- |
| intake-auditor | 原题与附件清单 | 遗漏、单位、时点和歧义 | 无 |
| literature-researcher | 冻结问题指纹 | 来源和方法证据 | references/ |
| modeler-independent | 合同与已核验证据 | 独立候选模型 | 自己的候选目录 |
| challenger | 两个已封存候选 | 反例、失败模式、判别实验 | 无 |
| solver-writer | 选中模型与合同 | 可运行代码和结果 | code/、results/ |
| numerical-verifier | 代码、结果和合同 | 复现、约束与数值报告 | 无 |
| spatial-verifier | 空间合同、代码和结果 | 坐标/距离/拓扑/覆盖审查 | 无 |
| outline-editor | supported claims 与模板 | 章节计划 | paper/drafts/ |
| section-drafter | 单节所需 claims | 单节候选 | paper/drafts/ |
| equation-reviewer | 论文候选、模型报告 | 符号和公式问题 | 无 |
| evidence-reviewer | 论文候选、claim ledger | 越界表述和引用问题 | 无 |
| paper-merger | 已审查候选与决定 | 最终论文源文件 | paper/ |
| final-verifier | 全部封存工件 | PASS/FAIL/UNVERIFIED 证据建议 | 无 |

## 升级规则

- intake、合同、模型、结果、论文和提交仍需真实队员确认。
- 两个独立模型共享同一个关键错误时，不能用一致票数升级可信度。
- 任一确定性检查失败，AI 共识不得覆盖失败。
- 争议涉及题意、固定量、目标函数或提交规则时立即升级人工；不要让 merger 猜测。
- 外部模型只获得最小必要材料，且受竞赛规则和数据政策约束。
