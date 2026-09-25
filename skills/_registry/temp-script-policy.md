# 临时脚本纳入/归档规则

## 判断标准

- **纳入正式 skill**：脚本会被同类任务反复调用；能显著减少 token、减少手工步骤、提高准确率；输入/输出可参数化；有明确 owner skill；可通过 manifest.yaml 暴露。
- **放入 `_shared/scripts`**：脚本被多个 skill 共用，且不是具体任务入口。
- **放入 `_incoming/orphans`**：暂时无法判断归属或用途。
- **放入 `_archive/temp-scripts-*`**：只为一次迁移、一次审计、一次修复而写；运行结果已沉淀到正式 skill/manifest/registry/report；后续不应直接调用。
- **放入 `_runtime`**：运行日志、状态、缓存、报告；不是长期知识内容，也不是正式脚本入口。

## 执行要求

新脚本进入正式 skill 前必须登记到 `_registry/scripts.catalog.yaml`，并在 owner skill 的 `manifest.yaml` 或 `SKILL.md` 中说明何时调用。
