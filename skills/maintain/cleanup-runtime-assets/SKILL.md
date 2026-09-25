---
name: cleanup-runtime-assets
description: "审计并受控清理 LLMWiki_BGE-M3 的 tmp、共享模型/工具 runtime、RAG runtime、skill 运维 runtime 和经哈希确认的重复原件别名。用于全局存储盘点、冗余文件清理、旧 runtime/缓存清理；必须由 Role C 执行 dry-run，apply 需要用户明确授权。"
---

# Runtime 资产清理

本 skill 属于 Role C。目标是释放可重建或已确认重复的空间，不以牺牲任务可恢复性、原件完整性或运行能力换取容量。

## 权限矩阵

| 范围 | 默认权限 | 硬门禁 |
|---|---|---|
| 全仓库大小、文件数、重复哈希审计 | 只读允许 | 通过 `PROJECT_LAYOUT.yaml` 解析路径；报告逻辑大小与硬链接口径 |
| `tmp/`、`skills/_ops/runtime/` | Role C 可清理 | 先排除活动进程和 `pending`、`blocked`、`running`、`in_progress` 任务；保护 checkpoint、审批包和失败证据 |
| 共享 runtime 的非 canonical 缓存、旧工具副本 | 条件允许 | dry-run + `--role-c-authorized` + `--allow-runtime-cache`；不得逐文件删除环境依赖 |
| BGE/RAG 可重建模型格式、缓存、索引或数据 | 高风险条件允许 | 证明当前调用路径不依赖目标；前后运行相同冒烟；apply 额外要求 `--allow-rebuildable` |
| `source-library/` 重复别名 | 高风险条件允许 | 仅限普通文件；目标和 canonical 文件 SHA-256 完全一致；额外要求 `--allow-source-dedup` |
| 已禁用但策略要求保留的模型 | 默认禁止 | 用户必须明确改变保留策略；同步 registry/profile 后才能清理 |
| 活动 Python/Node 环境、有效模型、MCP 工具 | 禁止在线删除 | 先停止依赖进程并使用环境级重建/迁移方案，不得手删 DLL/包文件 |
| `raw/`、`LLMWiki/`、`StudentDataSQL/`、`output/`、`.git/`、唯一原件 | 禁止 | 本 skill 无删除权限 |

## 强制流程

1. 读取 `skills/registry.yaml`，确认本 skill 是 primary；解析 `PROJECT_LAYOUT.yaml`。
2. 统计目标大小、文件数、最后修改时间、重解析点和硬链接；列出活动进程。
3. 搜索 checkpoint、状态和审批文件。发现未完成状态时从清理计划排除，不得用 `--allow-*` 绕过任务状态。
4. 对模型、RAG、原件去重分别建立验证基线：model doctor、相同 RAG 冒烟、SHA-256 canonical 绑定。
5. 写 UTF-8 JSON 计划，先运行：

```powershell
python skills/maintain/cleanup-runtime-assets/scripts/cleanup_runtime_assets.py --plan <plan.json>
```

6. 用户明确授权后 apply；按计划类别增加相应高风险开关：

```powershell
python skills/maintain/cleanup-runtime-assets/scripts/cleanup_runtime_assets.py `
  --plan <plan.json> --apply --role-c-authorized
```

7. 重跑基线、路径边界审计、skill 验证和空间统计。任何新增失败都必须停止并报告。

## 计划格式

每个 target 必须包含 `path_id`、非空 `relative_path`、`category` 和 `reason`。支持的类别：

- `temporary`：已完成的 `tmp` 或运维 runtime 产物；有 checkpoint 时还需 `completed: true`。
- `runtime_cache`：共享 runtime 中经引用审计确认的非 canonical 缓存；需 `registry_unreferenced: true`。
- `rebuildable`：已验证可重建且不被当前调用路径使用的 RAG/runtime 资产；需 `rebuildable: true`。
- `source_duplicate`：目标文件与 `canonical_path_id`、`canonical_relative_path` 指向文件哈希相同。

尽量绑定 `expected_bytes`；`source_duplicate` 必须绑定 `expected_sha256` 和 `canonical_sha256`。

## 完成标准

- dry-run 目标、字节数和 apply 结果一致。
- 不删除活动/未完成任务、唯一原件、正式交付物、真实学生数据或有效 runtime。
- 模型 doctor 与 RAG 冒烟不新增失败；canonical 原件哈希不变。
- 报告释放空间、保留项、不可恢复项、验证命令和锁定区影响。
