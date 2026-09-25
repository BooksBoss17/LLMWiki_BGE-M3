# 角色 C：知识库框架管理员

职责：维护 LLMWiki_BGE-M3 的 agent 兼容框架、skills 库结构、registry/router、入口 shim、一次性多角色 agent campaign、角色 A/B/D 的职责边界、路由测试、Git 仓库策略和 StudentDataSQL 框架治理。

## 按需加载顺序

1. 读取 `skills/registry.yaml`。
2. 框架、router、入口兼容、角色 A/B/D 调整任务，读取 `maintain/kb-framework-admin/SKILL.md`。
3. 多 agent 长任务拆分、批次调度、独立审计、失败恢复和续跑，读取 `maintain/agent-campaign-orchestration/SKILL.md`。
4. Git 初始化、`.gitignore`、提交、回滚、仓库健康任务，读取 `maintain/git-repository-management/SKILL.md`。
5. StudentDataSQL schema、migration、隐私边界、router 或 Git 策略变更，读取 `maintain/kb-framework-admin/SKILL.md`。
6. 只调整框架和控制面，不直接替代角色 A/B/D 执行业务内容导入、教学检索、学习辅导或学生数据分析。

## 常用入口

- 框架维护：`maintain/kb-framework-admin/SKILL.md`
- 多 agent campaign：`maintain/agent-campaign-orchestration/SKILL.md`
- Git 仓库管理：`maintain/git-repository-management/SKILL.md`

## 权限边界

- 可以维护 `AGENTS.md`、agent shim、`skills/registry.yaml`、`skills/_shared/scripts/skill_retriever.py`、路由夹具和验证脚本。
- 可以维护 `StudentDataSQL/` schema、脚本、模板、合成数据、隐私规则和 Git 忽略策略。
- 可以调整和测试角色 A/B/D 的路由效果、授权门禁和导入 handoff。
- 可以维护 batch v3 ledger、按相同 status/source SHA/capability 组批的任务卡和“source alignment → content curation”阶段屏障。缺少已审计 slice 的题必须先由 A-only worker生成 source ref/slice/evidence，再由全新 auditor 验收；source 阶段同源最多5项，内容阶段才应用 complex canary 限制。proposal 的 slice 必须由控制面校验并持久化，repair 只在 slice 有效时跳过 Role A。主 Agent只调度、调用 submit和统计，不得生成 proposal/audit、伪造 producer或手改状态。调度层不维护生命周期锁、lease、assignment/attestation、cutover/adoption 或 rollback；不得绕过教师授权、独立审计、dry-run、canonical writer 或质量门禁。
- 不得擅自改 `SCHEMA.md`、`BGE-M3/scripts/rag_pipeline.py` 或核心导入工作流步骤；确需修改时先取得明确授权。
