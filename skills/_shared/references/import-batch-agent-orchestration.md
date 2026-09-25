# 导入批次 one-shot agent 编排契约

本契约为七个 Role A 导入 skill 提供共享的 reviewer / executor / verifier 批处理边界。它只优化可委派的审阅与新文件写入，不替代各 skill 的原始文件保留、三轮质量检查、Role D handoff、Wiki/RAG 同步、隐私或教师授权门禁。

本文件定义的是仍受支持的 import v1 业务合约。通用多角色 campaign 使用 mission v2；迁移期通过 `import_v1_mission_adapter.py` 无损封装 v1 task card/compact result，不修改原 v1 JSON、七个 profile、CLI 或 writer。adapter 只提供兼容 envelope，不能把旧授权提升为 mission v2 authorization grant。

## 不变量

1. 每张 task card 绑定 `task_id + batch_id + attempt_id + attempt_no + worker_instance_id + queue_ref.sha256 + queue_ordinal + owner_skill + agent_role`，且固定 `fork_context=false`。`attempt_no` 只能为 1 或 2；第二次必须用 `retry_of` 指向 ledger 中唯一的第一次，并使用全新的 worker。agent 完成一次 compact result 后即结束。
2. agent 上下文只含短指令、路径引用、SHA-256、大小和必要的脱敏审阅块。正文、图片、完整转写、真实学生行、密钥和长日志只保存在本机受控路径中。
3. compact result 使用 `import_agent_compact_result.schema.json`，UTF-8 文件总大小不得超过 16384 bytes。结果只返回摘要、问题和 artifact references，禁止嵌入文档正文、图片/base64 或长日志。
4. profile 是并发、批量和隐私上限，不是建议值。任何未知 skill、role、payload class、越界路径、超限批次、缺失哈希、复用 attempt/worker 或队列 hash/ordinal 漂移均 fail closed。
5. reviewer 可并行，但 executor/verifier 和最终 Wiki、图谱、RAG 阶段按 profile 串行。主流程负责合并、冲突处理、已有文件更新、最终批准和完成门禁。

## 状态流

`queue + attempt ledger -> task card -> one-shot result -> identity/ledger validation -> main-process review -> canonical/shared writer -> dry-run plan -> hash-bound approval -> atomic apply -> receipt verify`

- `import_batch_contract.py` 校验 task card、compact result、attempt ledger、artifact bundle 和 approval，并从 `import_batch_profiles.json` 读取七个 skill 的硬上限。结果必须逐字段回显 task/batch/attempt/worker/queue 身份，并绑定 task card 文件的实际 SHA-256。
- attempt ledger 只记录已结束的前序 attempt。第一次派发时 ledger 为空；第二次派发时 ledger 必须恰有 attempt 1，且 `retry_of`、queue hash 与 ordinal 全部一致。已记录的 attempt、attempt_no 或 `worker_instance_id` 不得再次派发。
- artifact bundle 只描述已在磁盘 staging 中的文件及其目标，不携带文件正文。
- 通用 `write_import_artifact_batch.py dry-run` 只服务允许共享 writer 的 profile。`bemarkdown` 必须调用 `skills/import/bemarkdown/scripts/apply_formula_reviews.py`，`exercise-bank-import` 必须调用 `skills/import/exercise-bank-import/scripts/write_exercise_batch.py`；二者不得降级到通用 writer。
- 审批必须同时绑定 bundle 文件 SHA-256 与 dry-run plan 文件 SHA-256。任何内容、路径或计划变化都会使审批失效。
- `apply` 需要显式 `--user-authorized`，再次验证 bundle、plan、source 和目标不存在，然后在目标目录内 staging，并以 create-only 原子提交。任一提交、验证或 receipt 写入失败时，只回滚本次创建且哈希仍匹配的文件；不删除外部改变的文件。
- receipt 可重复验证；相同 plan 的成功 receipt 使 apply 幂等返回。不同 plan 或目标冲突一律阻断。

## 七个 profile 的边界

| skill | 批次上限 | generic agent 隐私边界 | 串行边界 |
|---|---|---|---|
| `bemarkdown` | 最多 5 个 unresolved review blocks | 不传整篇正文、图片 bytes 或长日志 | canonical `apply_formula_reviews.py` |
| `exercise-bank-import` | schema v2 writer 每批最多 5 项 | 只传 bounded review 与引用 | canonical `write_exercise_batch.py` 与主审 |
| `chinese-handwriting-formula-transcriber` | 最多 5 个显式脱敏 abstain summaries | 真实学生图片与 OCR 文本禁止进入 generic agent；禁止 executor | 本地主流程 |
| `textbook-import` | 一章或最多 20 页 | 可委派 source review、图片分类、Wiki draft 摘要 | 批准与已有文件更新 |
| `standards-import` | 一个一级节或最多 20 页 | 可委派层级/OCR 审阅 | create-only 新 artifact apply |
| `video-transcript-import` | 一个视频 | 完整 transcript 留在磁盘 | Wiki -> graph -> RAG 使用 fresh executor 串行 |
| `student-data-import` | 最多 20 个显式脱敏题号到既有 `kp_id` 映射 | 禁止真实行、标识符、密钥、OCR 和 SQLCipher payload；禁止 executor | 参数化脚本与主进程 apply |

## 校验接口

```bash
python skills/_shared/scripts/import_batch_contract.py validate-task-card --input <task-card.json> --attempt-ledger <attempt-ledger.json>
python skills/_shared/scripts/import_batch_contract.py validate-result --input <result.json> --task-card <task-card.json> --attempt-ledger <attempt-ledger.json>
python skills/_shared/scripts/import_v1_mission_adapter.py task-card --input <v1-task.json> --campaign <id> --scope-key <scope> --out <adapter-v2.json> --json
python skills/_shared/scripts/import_v1_mission_adapter.py compact-result --input <v1-result.json> --adapted-task-card <adapter-v2.json> --out <result-v2.json> --json
```

库调用使用 `validate_task_card_against_attempt_ledger(...)` 和 `validate_result_lifecycle(...)`。CLI 对 compact result 使用实际文件字节数，并对 task card 使用实际文件 SHA-256；不得用重新序列化后的 JSON 哈希代替文件哈希。

## 失败处理与恢复

- agent 超时、abstain、schema 失败或 compact result 超限：保留磁盘证据；若尚未重试，登记 attempt 1 后用全新 worker 派发 attempt 2。attempt 2 仍失败则阻断并交回主流程，不得创建 attempt 3，也不得把未验证结果合并为正式产物。
- worker 被复用、attempt 重复、`retry_of` 不存在或 queue hash/ordinal 改变：拒绝派发；先由主流程修复 queue/ledger 身份，不得静默重排队列。
- source 在 dry-run 后变化、target 出现、approval 哈希不匹配：废弃该 approval，从 dry-run 重新开始。
- apply 中途失败：writer 自动回滚本次安全创建项并返回 compact error；若任何目标被外部修改而无法安全回滚，必须人工处理后再继续。
- 长任务的 checkpoint、cleanup manifest、原始文件和已有 skill 专属 writer 状态继续由原 skill 管理；共享契约不创建旁路完成标记。
