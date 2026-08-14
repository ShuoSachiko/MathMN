---
name: mathmodel-orchestrator
description: "数学建模多 Agent 编排与异构模型复核。用于长上下文赛题、需要把题意、文献、建模、求解、写作和验收分给隔离 Agent，或需要 Codex 主控联合 DeepSeek 等 OpenAI 兼容模型做独立审查时；生成哈希固定的 CONTEXT_PACKET、AGENT_RESULT、分歧报告和事件日志，但不允许 AI 自批阶段门禁。"
---

# 数学建模多 Agent 编排

保留 Codex 作为总控，把噪声工作分给边界明确的只读专家。Agent 之间只交换结构化工件，不共享完整聊天历史。

## 前置条件

先读取项目的 `reports/PROBLEM_MANIFEST.json`、`reports/STAGE_GATES.json`、`reports/HUMAN_REVIEW.json` 和 `reports/HANDOFF.json`。若这些文件不存在，先使用 `$1start-mathmodel` 初始化项目。

实际比赛必须保持 `human-supervised`。Agent 结果只能是建议或证据，不能替代人工 checkpoint，也不能把失败的确定性检查改成 PASS。

## 初始化编排目录

```bash
python "<本 skill>/scripts/agent_protocol.py" init --project-root .
```

这会在 `reports/agents/` 下建立协议状态、packet、result、review 和只追加事件日志。初始化不会读取白名单以外的赛题文件。

## 分派原则

- 主控只保留需求、决策、门禁和最终合并权。
- 优先并行只读任务：题面核对、文献检索、候选模型、测试、日志分析和论文审校。
- 建模候选必须先独立产生，再允许互看；不要把第一个方案放进第二个 Agent 的 packet。
- 写入同一论文或代码树时采用单写者：专家提交 patch/建议，merger 串行落盘。
- 同一 provider/model 的多个角色属于多次采样，不算异构独立复核。
- 数值、约束和文件格式优先由程序验证；AI 只解释证据或提出反例。

详细角色和升级条件见 [roles.md](references/roles.md)，字段合同见 [protocol.md](references/protocol.md)。

## 签发最小上下文包

明确列出任务真正需要读取的文件；不要把整个工作区或历史聊天塞入 packet。

```bash
python "<本 skill>/scripts/agent_protocol.py" issue --project-root . \
  --task-id REQ-2-model-b --role modeler-independent --stage analysis \
  --objective "独立提出一个可检验的候选模型，不读取候选 A" \
  --req-id REQ-2 --input contract=reports/PROBLEM_CONTRACT.json \
  --input evidence=reports/LITERATURE_RESEARCH_REPORT.md \
  --provider openai --model deepseek-chat
```

把脚本输出的 packet 路径交给对应 Agent。Agent 只读 packet 中 `inputs`，并把输出写成 `AGENT_RESULT.json`；不得自行扩大白名单。

## 提交与验证

```bash
python "<本 skill>/scripts/agent_protocol.py" submit --project-root . \
  --packet reports/agents/packets/REQ-2-model-b.json \
  --result _tmp/REQ-2-model-b-result.json
python "<本 skill>/scripts/agent_protocol.py" verify --project-root .
```

`submit` 会重新核对输入哈希、packet 哈希、身份、ReqID、证据定位和允许状态，然后把结果封存到 `reports/agents/results/`。输入改变时必须废弃旧结果并重新签发 packet。

若两个候选结论冲突，生成确定性的分歧包：

```bash
python "<本 skill>/scripts/agent_protocol.py" disagree --project-root . \
  --left reports/agents/results/REQ-2-model-a.json \
  --right reports/agents/results/REQ-2-model-b.json \
  --output reports/agents/reviews/REQ-2-disagreement.json
```

分歧不能靠多数票静默消失。优先设计可执行判别实验；仍无法判定时标为 `UNVERIFIED` 并请求人工决定。

## 异构模型审查

`external_reviewer.py` 支持 OpenAI-compatible Chat Completions API，包括 DeepSeek 兼容端点。密钥只从环境变量读取，不写入项目：

```bash
python "<本 skill>/scripts/external_reviewer.py" --check-config \
  --base-url "https://api.deepseek.com" --model "deepseek-chat"
```

真正调用前设置 `MATHMODEL_REVIEWER_API_KEY`，再提供一个已签发 packet。脚本只发送 packet 显式白名单内、哈希未变的 UTF-8 文本，并受总字节上限约束；二进制附件须先在本地生成经过核验的最小摘要。`live-competition` 默认拒绝外部发送；只有当届规则、队伍数据政策和人工决定均允许时，才显式使用 `--allow-live-external-review`。外部模型输出仍须走 `submit`，且只能给 `PROPOSED`、`NEEDS_REVIEW`、`REJECTED` 或 `UNVERIFIED`。

## 阶段最小编排

1. intake：一个提取 Agent，一个独立题意核对 Agent。
2. analysis：两个盲建模 Agent，一个 challenger；空间问题再调用 `$mathmodel-spatial`。
3. coding：一个单写者；测试/数值/空间语义由只读验证 Agent 并行。
4. writing：outline、section drafter、equation reviewer、evidence reviewer 分离；merger 独占写权限。
5. verification：使用 `$6verity`；异构 AI 只增加审查意见，不能签发最终 PASS。

结束前运行协议 `verify`，把选中的 result ID 和仍未解决的分歧写入 `HANDOFF.json` 与 `DECISION_LOG.md`，再进入现有人工门禁。
