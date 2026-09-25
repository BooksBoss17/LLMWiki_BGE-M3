# 变更记录

这里只记录项目框架、目录、schema、脚本、权限、依赖和接口变化；普通教学内容导入使用 Git 历史和审计报告追踪。

## 2026-07-13

- 启动四库统一目录治理，将 `raw`、`LLMWiki`、`BGE-M3`、`StudentDataSQL` 固定为四个业务库。
- 新增 `PROJECT_LAYOUT.yaml` 和统一路径 resolver，逐步移除本机绝对路径与旧目录硬编码。
- 完成 `source-library/`、`skills/_shared/model-tools/`、`skills/_ops/`、各库 `runtime/` 与统一 `tmp/` 的硬切换。
- 将 Codex 专用辅助脚本迁入 `.codex/helpers/`，下载中间件迁入 `tmp/downloads/`，任务卡、日志、渲染和待归类材料分别进入对应 tmp 子目录。
- BGE-M3 索引迁移前后均为 4780 chunks / 4780 vectors；StudentDataSQL 真实数据库仅迁移并完成 SHA-256 等价校验，未执行业务 schema 变更。
