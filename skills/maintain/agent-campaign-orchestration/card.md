# agent-campaign-orchestration 检索卡

- status: active
- category: maintain
- role: role-c-kb-framework-admin
- canonical_skill: `maintain/agent-campaign-orchestration/SKILL.md`
- manifest: `maintain/agent-campaign-orchestration/manifest.yaml`
- description: 先把缺少已审计 slice 的题按同源最多5项完成 A-only 原件对齐，再进入纯 Role D 修正；每阶段使用一次性隔离 worker、独立 auditor 和确定性 writer。
- use_when: 多 Agent 批次调度、独立审计、长任务恢复、题库维护或导入 campaign
- do_not_use_when: 单题解答、单文件小修改、普通只读检索
- input: ledger、campaign authorization、profile
- output: 带精确 producer role、stage plan/result skeleton/持久化 source slice 的 batch cards，以及由 `submit.items` 返回的逐项状态、已验证 slice/receipt 引用和 compact status
- next_skills: 由 profile 指定的 executor skill、auditor skill 和业务后置 skill

路由命中后只读取 `SKILL.md`；完整题目、OCR、图片和推导保留在批次 artifact。
