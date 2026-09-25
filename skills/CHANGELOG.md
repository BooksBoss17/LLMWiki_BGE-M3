# Skills 变更记录

这里只记录 Skills 架构、router、共享依赖、权限和接口变化；单个 Skill 的普通内容修订由 Git 路径历史追踪。

## 2026-07-13

- 将 `_shared/model-runtime` 与 `_shared/runtime` 合并为 `_shared/model-tools`。
- 将 OCR、VLM、公式识别、STT、OfficeCLI 和共享环境统一到 `model-tools/runtime/`。
- 将 `_runtime` 分类迁移为 `_ops/runtime`，新增 `_ops/audits` 保存脱敏长期摘要。
- Skill 通过共享组件 ID 和 resolver 调用模型与工具，不再建立 Skill-local 权重副本。
