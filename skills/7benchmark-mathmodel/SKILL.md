---
name: 7benchmark-mathmodel
description: "数学建模隔离盲测与反信息污染评测工具。仅在用户明确要求 benchmark、盲测、知识污染声明、离线与联网对照、结果封存、验封或解锁后独立审计时手动使用；不属于 1start-mathmodel 的主流程，也不读取或捆绑任何具体题目、答案或官方材料。"
---

# 数学建模隔离评测

本 skill 为现有建模流程增加一个可选的评测外壳，负责阶段隔离、污染状态记录、产物封存和解锁后的独立审计协议。它不替代 `1start-mathmodel` 至 `6verity`，也不得自动加入普通竞赛流程。

## 角色边界

把一次评测拆成三个互不兼任的角色：

- **求解者**：只接收公开题面包、固定 skills 和固定运行时；不得访问裁判私有材料。
- **封存者**：按显式 allowlist 封存求解者提交，并把 root hash 写入求解者不可修改的可信记录。
- **审计者**：求解进程终止且验封通过后，使用全新上下文读取封存副本和解锁后的私有评测包；只输出审计报告，不修改封存提交。

私有评测包必须位于求解环境无法读取、枚举或通过工具侧信道访问的独立存储中。隐藏目录、改名、`.gitignore` 和提示词约束都不是隔离措施。

## 题目来源与污染状态

在任何求解前记录四个独立状态轴，不把自声明等同于无污染证明：

| 轴 | 允许值 | 判定含义 |
| --- | --- | --- |
| `task_provenance` | `historical-public` / `private` | 题目是否已公开；只有私有题可进入严格盲测主分 |
| `prior_exposure` | `denied` / `recognized` / `uncertain` | 求解者对题目、结果或材料的先验接触自声明 |
| `runtime_isolation` | `enforced` / `declared-only` / `violated` | 是否由能力层断网和文件权限实际隔离 |
| `seal_and_audit` | `verified-independent` / `verified-same-context` / `failed` | 封存是否验真且审计是否使用独立上下文 |

`historical-public` 只能作为污染敏感性或回归诊断，不能因求解者声明“没见过”而标为严格 clean。只有 `private`、`runtime_isolation=enforced`、可信 root hash 验证通过和 fresh independent audit 同时成立时，才可称为合格盲测；模型训练阶段的未知污染仍须标为不可证明排除。

## 阶段协议

### 1. 建立离线求解环境

为每个 `run_id` 创建全新进程和空工作区，只读挂载公开输入与固定版本的 skills/runtime。通过容器、受限账户或等效能力边界禁用网络、浏览器、连接器、包安装和其它工作区读取。不要让知道私有材料的调度 agent 兼任求解者。

联网辅助实验必须从离线封存副本另开新 `run_id`，单独报告 `online-assisted` 结果；不得覆盖或回填离线基线。

同时固定评测模式：

- `human-supervised`：指定哪些阶段经过真实人工复核，并把审批记录保存在求解者不可修改的外部系统。
- `autonomous-simulation`：允许全自动运行，用于测量 agent 自主能力；自动 agent 的“自检”“模拟评委确认”或流程豁免都不算人工批准。

评测成功只说明该 benchmark 协议下的表现。除非比赛要求的人工复核真实完成并另行签署，任何模式都不得仅凭自动得分或验封结果宣称“比赛提交就绪”；`autonomous-simulation` 尤其必须明确标为非人工审批。

### 2. 预先登记

在编码、拟合或大规模计算前生成以下控制记录：

- `knowledge_pollution_declaration.json`：四轴中前三轴的初始值、模型标识、skills 版本、是否认题、记得的内容类别、此前接触来源和不确定性。
- `preregistration.json`：逐子问题的题意重述、歧义、候选解释、采用假设及选择规则、变量、目标、约束、验证指标、数据划分、计算预算、停止条件和预期产物。
- `amendments.jsonl`：预注册后的改动只追加，不覆写历史；每条包含时间、原因、触发证据、影响范围和变更前后摘要。
- `tool_events.jsonl`：能力调用、网络拒绝、命令、运行时、随机性和退出状态的追加式记录。

不得在这些文件中写入来自私有评测包的提示、预期数值或答案派生测试。

### 3. 完成离线求解与内部验收

按正常的建模 skills 完成分析、编码、图表、论文和内部复现。`6verity` 仍只负责提交前的一致性与可复现性检查，不接触解锁材料，也不执行官方比对。

### 4. 准备显式 allowlist

把待封存文件复制到一个专用 staging 根。该根只能包含本次提交文件，不得包含私有材料、缓存、其它 run 或封存输出。创建位于 staging 根之外的 JSON allowlist：

```json
{
  "schema": "mathmodel-benchmark-allowlist/v1",
  "files": [
    "code/main.py",
    "reports/final_report.md",
    "results/metrics.json"
  ]
}
```

`files` 必须逐文件列举相对 POSIX 路径；不接受目录、glob、绝对路径、反斜杠、`.`、`..`、符号链接或 reparse point。脚本默认 fail closed：staging 根中出现 allowlist 外文件也会拒绝封存。

### 5. 封存

将 archive 和 manifest 写入 staging 根之外、由裁判拥有且求解者无写权的同一目录。脚本拒绝跨目录发布和覆盖既有输出；发布第二个文件失败时会回滚第一个文件：

```powershell
python scripts/benchmark_seal.py seal `
  --source C:\path\to\staging `
  --allowlist C:\path\to\benchmark_allowlist.json `
  --archive C:\judge-owned\submission.zip `
  --manifest C:\judge-owned\submission.manifest.json `
  --human-review-status not-reviewed
```

脚本仅使用 Python 标准库，生成固定顺序、固定 ZIP 元数据的 archive，以及包含每个文件规范路径、字节数和 SHA-256 的 canonical manifest。`--human-review-status` 必须显式选择 `human-reviewed`、`not-reviewed` 或 `simulation-waived`；只有 `human-reviewed` 可同时传 `--human-review-record-id` 指向外部真实审批记录，其余状态禁止携带或暗示批准。人工状态也绑定进 root hash。将 stdout 返回的 `root_hash` 立即写入 archive/manifest 之外的可信日志、签名记录或 append-only 存储。只读文件属性本身不构成不可变封存。

### 6. 先验封，再解锁

审计者必须从可信通道取得 expected root hash，并在接触私有材料前运行：

```powershell
python scripts/benchmark_seal.py verify `
  --archive C:\judge-owned\submission.zip `
  --manifest C:\judge-owned\submission.manifest.json `
  --expected-root-hash ROOT_HASH_FROM_TRUSTED_LOG
```

如需确认原 staging 根未变，再传 `--source C:\path\to\staging`。任何缺失、新增、路径变化、字节变化、manifest 不规范、archive 条目异常或 root hash 不符都必须停止解锁并把 `seal_and_audit` 标为 `failed`。

### 7. 解锁后独立审计

验封成功并终止求解进程后，启动无父对话历史的新审计 agent。只向它传递：公开输入、封存 archive、可信 root hash、预先冻结的评分维度以及此时才解锁的私有评测包。不要传求解者的自我评价、预期修复、已知 bug 或其它审计者结论。

审计至少包括：

1. 重新验封并从 archive 解包到只读临时目录。
2. 在固定运行时独立复现必需输出。
3. 依据私有测试和冻结 rubric 核查题意、约束、数值、稳健性与可复现性；允许合理的不同建模路线。
4. 检查 tool log、canary 命中和产物异常相似性，更新四轴污染状态。
5. 在 `scorecard.json` 和 `audit_report.md` 中分别记录 `evaluation_mode`、seal manifest 的 `human_review`、审计阶段 `human_review` 及其外部记录 ID；`simulation-waived` 不得转换为 `human-reviewed`。
6. 把 scorecard 和 audit report 写到封存根之外；禁止修补 archive。修订只能创建新 `run_id`，并作为不覆盖原基线的后续结果。

审计结论使用两个独立字段：`benchmark_outcome`（例如 pass/fail/invalid）和 `competition_submission_readiness`。后者只有在所需人工节点真实完成且证据可核验时才能为 `human-approved`；否则必须是 `not-human-approved`，即便自动评测全部通过。

两个审计产物至少共同包含以下状态块；可增加评分细节，但不得删除或改名这些字段：

```json
{
  "evaluation_mode": "autonomous-simulation",
  "seal_human_review": {
    "status": "simulation-waived",
    "record_id": null
  },
  "audit_human_review": {
    "status": "not-reviewed",
    "record_id": null
  },
  "benchmark_outcome": "pass",
  "competition_submission_readiness": "not-human-approved"
}
```

若审计确有人工复核，把相应 `status` 设为 `human-reviewed` 并填写可从外部可信系统核验的 `record_id`。不得根据 agent 自述、自动测试通过或 `simulation-waived` 自动升级该状态。

使用两名审计者时，让二者彼此不可见并分别输出结构化 scorecard，再由规则化 adjudicator 合并。

## 防泄露门禁

- 私有题、参考结果、隐藏测试、评分阈值、canary 表和答案派生资产不得放入本 skill、求解工作区、同一 Git 历史或共享临时目录。
- 不把低熵预期值的 hash、golden file、答案暗示性文件名或私有评分器 stdout 暴露给求解者。
- subagent 使用 fresh context 和最小原始材料；不要把父 agent 的结论、预期方法或可疑错误写入任务提示。
- 封存脚本只保证内容寻址与篡改检测；真正的不可变性依赖外部可信 root hash 和求解者无写权存储。

## 自检

修改本 skill 或脚本后运行：

```powershell
python -m unittest discover -s tests -v
python scripts/benchmark_seal.py --help
```
