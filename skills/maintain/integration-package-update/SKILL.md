---
name: integration-package-update
description: "角色 C 本机专用工作流：仅在用户明确授权更新、同步、执行或 apply 整合包时，用于维护 LLMWiki_BGE-M3 新设备部署整合包。普通完整性检查、边界讨论或部署问题分析不要使用本 skill。"
---

# Integration Package Update — 整合包更新

本 skill 只属于开发机本地知识库，用于维护给另一台电脑初始化空知识库的整合包镜像。它不会进入整合包，也不得在整合包内保留路由条目。

## 触发条件

只有用户明确要求角色 C 更新、同步、执行、apply 或实际写入整合包时，才使用本 skill。

不要把下列只读任务路由到本 skill：

- 检查整合包完整性。
- 讨论整合包边界。
- 分析部署问题。
- 判断哪些文件应该进入整合包。

这些任务继续使用 `maintain/kb-framework-admin/SKILL.md`。

## 工作流程

1. 先 dry-run：

```bash
python skills/maintain/integration-package-update/scripts/sync_integration_package.py --dry-run --json
```

2. 检查 JSON 中的同步数量、local-only 过滤结果、跳过目录和边界扫描。
3. 只有用户明确授权写入整合包后，才执行：

```bash
python skills/maintain/integration-package-update/scripts/sync_integration_package.py --apply --user-authorized --json
```

需要同步 registry allowlist 中的便携运行库时，必须在同一授权任务中显式使用：

```bash
python skills/maintain/integration-package-update/scripts/sync_integration_package.py --scope all --include-runtime --apply --user-authorized --json
```

日常轻量同步不带 `--include-runtime`，行为与既有流程保持一致。运行库实体发生变化时额外使用
`--refresh-runtime-inventory` 重建文件级来源清单；普通复验使用可信缓存和哨兵检查。

4. apply 后在整合包 `Skills/` 内验证：

```bash
python <PACKAGE_ROOT>/Skills/_shared/scripts/validate_skill_library.py --allow-missing-runtime
python <PACKAGE_ROOT>/Skills/_shared/scripts/skill_retriever.py --self-test
```

## 整合包边界

允许同步：

- 框架入口文件、agent shim、规则模板。
- canonical skills 的轻量 `SKILL.md`、`card.md`、`manifest.yaml`、references、scripts、templates。
- StudentDataSQL schema、migrations、scripts、templates、合成 fixtures。
- RAG helper 脚本、依赖清单。
- `raw/`、`source-library/`、`StudentDataSQL/runtime/exam-reports/` 的占位 README。

默认禁止同步：

- 生产 `raw/` 内容。
- 生产 `LLMWiki/concepts/` 页面。
- `source-library/` 真实 PDF/DOCX/PPT/图片/音视频。
- BGE-M3 models/data/output/venv。
- StudentDataSQL runtime 数据库、imports、exports、backups、logs、真实 exam report 正文。
- skill runtime env/model、cache、`output/`、`tmp/tasks/`。

显式 `--include-runtime` 的唯一例外：

- 只同步共享 registry 中启用且列入 `portable_package` allowlist 的 skill runtime 组件和环境。
- 只同步 `BGE-M3/runtime/env` 与 `BGE-M3/runtime/models/BAAI/bge-m3`；仍禁止 `data/index/logs`。
- 必须通过许可证 gate、文件级 SHA-256 manifest、临时目录验证、最终路径重定位和边界扫描。
- 运行库、模型和二进制仍不得进入 Git；本 skill 自身仍不得导出到整合包。

本 skill 自身在 `manifest.yaml` 中标记 `package_export: false`，必须从整合包 `Skills/registry.yaml`、catalog、fixtures 和 script catalog 中过滤。
