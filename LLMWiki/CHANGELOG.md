# LLMWiki 框架变更记录

## 2026-07-13

- 将 `_meta/` 下历史备份迁入 `runtime/backups/`，隔离材料迁入 `runtime/quarantine/`。
- 将可长期复核的脱敏审计摘要统一归入 `_meta/audits/`。
- 路径由根 `PROJECT_LAYOUT.yaml` 的 `library.wiki` 与 `wiki.runtime` 统一解析。
