---
name: agent-campaign-orchestration
description: 用一次性 executor 与独立 auditor 串行处理可续跑批次。适用于多 Agent 长任务、题库维护、导入批次、失败修复、断点续跑和状态统计；每批先暂存、后审计、再由确定性 writer 写回。
---

# Agent Campaign Orchestration

主 Agent 只调度和统计，不生成 proposal、evidence、dry-run 或 audit。每个 campaign 同时只运行一个批次；最多并行四个不同 campaign。平台不能提供隔离的一次性 worker 时停止，不得退回主线程模拟执行。

## 五步接口

```powershell
python skills/_shared/scripts/agent_mission_orchestrator.py init --campaign <id> --ledger <ledger.json> --authorization <authorization.json> --profile <profile.json> --batch-size 5 --json
python skills/_shared/scripts/agent_mission_orchestrator.py dispatch --campaign <id> --json
python skills/_shared/scripts/agent_mission_orchestrator.py submit --campaign <id> --result <batch-result.json> --json
python skills/_shared/scripts/agent_mission_orchestrator.py recover --campaign <id> --run-id <run-id> --reason <reason> --json
python skills/_shared/scripts/agent_mission_orchestrator.py status --campaign <id> --json
```

## 调度规则

1. 先确认至少有一个空闲 isolated-worker 槽位。平台有槽位查询时先查询，再 `dispatch` 并立即 spawn；平台没有查询接口时，先以 `fork_context=false` spawn 本批实际 worker 待命，用成功 spawn 作为槽位确认，再 `dispatch` 并把 batch card 发送给同一 worker。不得创建一次性 capacity probe，也不得先留下无人负责的 active run；dispatch 失败时立即关闭待命 worker。
2. 把 spawn 工具返回的真实 agent ID、batch card 和指定输入交给 worker；worker 将该 ID 原样写入 `producer.worker_instance_id`，并把 card 中的 `worker_contract.producer_role` 原样写入 `producer.role`，以 `execution_mode=isolated_worker` 提交。主 Agent 不得代填业务结果。
3. `dispatch` 按相同 status、source SHA-256 与 capability 稳定组批。任何 `queued/revalidation_queued` 题若没有已审计的 `source_slice_ref`，必须先进入 A-only source-alignment wave；source card 按 `source_slice_v3.schema.json` 为每题预建唯一 `source_slice_path` 和隔离的 `source_artifact_root`，worker 必须覆盖该骨架，不能自行发明 XML/JSON 格式或另选路径。locator 必须声明 `type` 与 `coordinate_space`，明确 PDF 文件页/印刷页或 DOCX 原始段落/规范化 block 的坐标语义。source worker 同时提交 source ref、题目级 slice 和 evidence，全新 auditor 验收后才回到 queued。source 阶段不受 complex canary 单题限制，同源最多5题；内容 executor 才按 capability 门禁进入 Role D。
4. executor 结束后 `submit`，再按同样顺序派全新 auditor。auditor 的真实 agent ID 必须与 executor 及该题历史 worker 均不同；auditor 逐题独立核对，只输出 `passed/failed` 和 audit 引用，不拥有 writer 权限。
5. 审计通过后由控制面调用 profile 的 canonical writer；writer 写前再次验证 executor/auditor producer chain、目标/source/media 哈希，写后运行确定性验证并生成 apply receipt。
6. executor 的 proposal 必须提交可验证的 `source_slice_ref`，控制面校验后持久化；同批通过题先完成，失败题由全新 executor 修复一次。repair card 复用仍有效的 slice 并跳过 Role A，只加载 Role D；之后由另一个全新 auditor 复审，第二次失败则 `blocked`。
7. worker 运行故障允许换新 worker 重试一次；重试、repair 和复审均不得复用历史 worker。旧 `run_id` 结果一律拒绝。主线程中断后只依据状态、card、result 和 receipt 续跑。
8. `source_blocked` 和缺少 slice 的 queued 题都使用同一 A-only source 批次；新映射和 slice 经独立 auditor 通过后持久化。一个 campaign 的所有可对齐题完成 source wave 后，才开始该卷内容修正；真正缺失原件的题 fail closed，不阻塞其他卷。
9. canary 中的 `complex/critical` executor 每批只派 1 项；同一 source SHA-256 的确定性转换缓存固定放在 card 指定的 `source_cache_root`，新 worker 必须优先复用。
10. worker 无法完成时也必须覆盖 result 骨架，逐项提交 `runtime_failed/source_blocked/unresolved`，不得只留下转换缓存。
11. worker 只覆盖 result 骨架，不调用控制面命令；主 Agent 在 worker 结束后直接调用 `submit`，把它作为 schema、身份、哈希与 writer 的唯一确定性门禁，不另写临时脚本重复解析 artifact 或 campaign 内部结构。`submit.items` 会紧凑返回逐项状态及已验证的 source slice/apply receipt 引用。
12. source auditor 失败后允许全新 source worker 进行剩余的一次重试；新 source candidate 被接受时控制面必须原子清空上一轮 audit/defects。主 Agent 以 `submit.ok` 与当前 `status` 判断本次提交，不能把历史 defects 当作新失败；`status=source_audit_queued` 表示应立即派全新 auditor。

运行次数耗尽的题目只能在补入哈希绑定的题目级 source slice 后受控恢复，仍使用原 `recover` 命令：

```powershell
python skills/_shared/scripts/agent_mission_orchestrator.py recover --campaign <id> --requeue-blocked <question_id> --source-slice <source-slice.json> --reason <reason> --json
```

该路径只接受 `runtime_attempts_exhausted`，不解锁内容审计失败或原件仍不明确的题目。

历史 `passed/no_change_passed` 若因旧流程缺少可信 producer identity 而必须重验，不手改 campaign JSON；只在无 active run 时使用：

```powershell
python skills/_shared/scripts/agent_mission_orchestrator.py recover --campaign <id> --revalidate-passed <question_id> --reason <reason> --json
```

该入口保留旧 candidate/audit/receipt 与 dispatch attempt 摘要到 `revalidation_history`，重置新一轮 dispatch attempt，清空活动候选并转为 `revalidation_queued`；它不表示旧内容错误，也不允许跳过新的 executor、auditor 或 writer 门禁。若旧 attempt 计数曾使这一转换误入 `dispatch_attempts_exhausted`，同一入口可凭已有 revalidation history 恢复该未完成转换。

## 不变量

- 审计前不写正式库；不使用 source/target 生命周期锁、lease、assignment、attestation、registration token 或 cutover/adoption。
- `run_id`、phase 或 card hash 只能证明批次身份，不能证明独立审计；报告必须引用 executor/auditor 的不同 `producer.worker_instance_id`。没有有效 producer chain 时 fail closed。
- 状态写入仅使用内部短时原子互斥；writer 仅在落盘瞬间使用哈希比较和原子文件事务。
- 授权按 campaign 绑定 ledger、writer、字段和期限；主 Agent 不自行生成授权。
- 题干、OCR、图片或推导不确定时标记 `source_blocked/unresolved`，不得猜修。
- 题目级 source slice 必须绑定 question ID、source SHA-256、定位信息及实际 slice artifact 哈希；不得把整份原件转换目录冒充题目证据。
- 新 source slice 只能写入 card 预登记的 manifest 路径，artifact 只能位于该题的 `source_artifact_root`；格式、路径或 locator 坐标空间不匹配均按 worker contract 错误 fail closed。
- Wiki、图谱和 RAG 后置流程由业务 skill 在 campaign 内容完成后执行，不进入本控制面。
