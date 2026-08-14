# Agent 工件协议

## 目录

- 上下文包
- Agent 结果
- 信任与状态
- 失效规则

## 上下文包

`CONTEXT_PACKET` 固定以下内容：项目/题面根哈希、task ID、角色、阶段、目标、ReqID、显式输入路径与 SHA-256、provider/model 声明、工具边界和输出合同。输入文件保留在原处，不复制到聊天历史。

每个 task ID 只对应一个不可变 packet。目标或输入变化时签发新 task ID，避免旧结果被误当成当前证据。

## Agent 结果

结果文件必须包含：

```json
{
  "schema_version": 1,
  "task_id": "REQ-2-model-b",
  "packet_sha256": "<packet hash>",
  "role": "modeler-independent",
  "provider": "openai",
  "model": "deepseek-chat",
  "status": "PROPOSED",
  "summary": "...",
  "req_ids": ["REQ-2"],
  "claims": [
    {
      "claim_id": "C1",
      "text": "...",
      "evidence": ["reports/file.md#section"],
      "confidence": 0.7,
      "limitations": ["..."]
    }
  ],
  "artifacts": [],
  "open_questions": [],
  "recommended_checks": []
}
```

`confidence` 只是 Agent 自报，不是概率保证。没有 evidence 的 claim 不允许被写入最终论文。

## 信任与状态

允许状态只有 `PROPOSED`、`NEEDS_REVIEW`、`REJECTED`、`UNVERIFIED`。协议故意不提供 PASS。真正阶段状态仍由 `STAGE_GATES.json`、确定性检查和 `HUMAN_REVIEW.json` 决定。

异构性按 provider、model family、提示/角色和可见证据记录。仅改变 temperature 或 persona 不构成强独立性。

## 失效规则

以下任一变化使 result 失效：packet、任一输入哈希、题面根哈希、冻结合同、选中模型版本或被引用代码/结果发生变化。`verify` 检出后返回失败，不自动修复或覆盖历史。
