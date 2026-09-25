# StudentDataSQL 框架变更记录

## 2026-07-13

- 将真实数据库、导入、导出、备份、日志和试卷报告统一迁入 `runtime/`。
- 共享脚本通过 `PROJECT_LAYOUT.yaml` resolver 获取 SQL 根和 runtime 路径。
- 本次只迁移并校验真实数据库哈希，不对真实数据库执行 migration 或结构变更。
