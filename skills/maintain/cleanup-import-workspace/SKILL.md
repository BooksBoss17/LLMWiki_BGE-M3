---
name: cleanup-import-workspace
description: "清理单个已完成的教材、课标、题库或视频导入任务工作区。用于任务级 cleanup manifest、导入临时文件归档与删除；属于 Role A，只能处理本任务在 tmp 下的资产，不能清理共享 runtime、RAG runtime 或唯一原件。"
---

# 导入任务工作区清理

本 skill 属于 Role A，只处理一个已完成导入任务的临时工作区。跨任务、跨 runtime 或全仓库清理由 `cleanup-runtime-assets` 接管。

## 权限范围

- 允许：当前任务在 `tmp/tasks/<task_id>/`、`tmp/logs/<task_id>/`、`tmp/state/<task_id>/`、`tmp/renders/<task_id>/`、`tmp/downloads/<task_id>/`、`tmp/staging/<task_id>/` 下的已完成中间产物。
- 必须保留：`source-library/` 原件、`raw/`、`LLMWiki/`、RAG 正式索引、`output/` 交付物、`skills/_ops/audits/` 结论、StudentDataSQL 真实数据。
- 禁止：共享模型/工具 runtime、BGE runtime、其他 task id、活动或未完成任务、没有 canonical 副本的下载件。

## 强制门禁

1. 读取本导入 skill 的完成标准和 cleanup manifest schema。
2. 教材、课标、题库、视频任务必须已经完成 Wiki 同步、Role D handoff verify 和最终 RAG；不适用阶段需在 manifest 中说明。
3. 检查 `checkpoint.json`、`document_queue.json`、`issue_log.jsonl` 和状态文件。存在 `pending`、`blocked`、`running`、`in_progress` 时停止。
4. cleanup manifest 列出每个目标、大小、文件数、保留副本、SHA-256（适用时）和完成证据；先 dry-run。
5. apply 需要用户明确授权。删除后重新验证原件、raw/Wiki/RAG 和任务完成证据。

## 升级到 Role C

以下情况不得在本 skill 内扩大权限，必须重新路由到 `cleanup-runtime-assets`：

- 同时清理多个 task id；
- 清理共享 runtime、模型、Python 环境、RAG runtime 或 skill 运维 runtime；
- 对 `source-library/` 做哈希去重；
- 修改全局保留策略或 cleanup 权限。

## 完成标准

- 只删除 manifest 声明的当前任务临时资产。
- checkpoint 和失败证据在任务完成前可恢复。
- 唯一原件、正式知识内容和运行资产未受影响。
- 报告 dry-run、apply、释放空间与复核结果。
