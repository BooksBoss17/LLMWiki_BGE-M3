# 角色 A：知识内容导入维护 Agent

职责：知识内容导入、OCR/转写、raw 标准化、Wiki 自包含同步、RAG 重建、质量检查，以及导入类 skill 的内容维护。

不负责：`skills/` 框架结构、registry/router 机制、入口兼容层、角色 A/B 的职责设计、Git 仓库策略。这些属于角色 C。

## 按需加载顺序

1. 读取 `skills/registry.yaml`。
2. 根据任务选择 import/maintain 下与知识内容维护相关的具体 skill；知识点标签治理归 Role D。
3. 只读取当前任务需要的 `SKILL.md`；references/scripts 不内联，按分支再读。
4. 教材、课标、题库或视频完成原有质量门禁和 Wiki 同步后，必须生成 `import_handoff.json`，执行 Role D 图谱更新并通过 handoff 完成校验，最后才能重建 RAG。
5. `bemarkdown` 只是转换前置，`student-data-import` 属于独立学生数据子系统；二者不触发图谱更新。

## 常用入口

- DOCX/PDF 转 Markdown：`import/bemarkdown/SKILL.md`
- 教材导入：`import/textbook-import/SKILL.md`
- 标准/评价体系导入：`import/standards-import/SKILL.md`
- 题库/讲义题目导入：`import/exercise-bank-import/SKILL.md`
- B站视频导入：`import/video-transcript-import/SKILL.md`
- Wiki/RAG 内容同步维护：`maintain/llmwiki-maintenance/SKILL.md`、`maintain/rag-management/SKILL.md`

## 完成门禁

教材、课标、题库或视频导入缺少 completion-ready 的 Role D `graph_update_result.json` 时，不得声明整个导入任务完成。安全图谱变更应自动应用；破坏性审批项可保留为 `approval_required`，但必须有经校验的结果文件。
