# teaching-output-format 检索卡

- status: active
- category: retrieve
- role: role-b-retrieval-calling
- canonical_skill: `retrieve/teaching-output-format/SKILL.md`
- manifest: `retrieve/teaching-output-format/manifest.yaml`
- description: 应用用户的物理讲义/试卷格式规则，包括字体、版式、答案分离、Word/PDF 排版约定和最终交付 QA 要求。
- triggers: 试卷格式, 讲义格式, 宋体, Times New Roman, 格式规范, 交付规范, 输出质量
- use_when: 输出样式规则, 试卷格式, 讲义格式, 字体要求, 答案/提示分离, 最终 Word/PDF 交付 QA 策略
- do_not_use_when: 源材料导入, raw/Wiki/RAG 维护, 简单检索, 与格式无关的内容生成
- input: 教学 Markdown 或输出要求, 样式约束
- output: 格式指导, 样式检查清单, 导出/OfficeCLI QA 策略交接
- next_skills: retrieve/md-to-docx/SKILL.md

## 加载规则

路由时只读本 card 或 `registry.yaml`。当本 skill 是 primary 时，先只读 `retrieve/teaching-output-format/SKILL.md`；只有该 skill 要求时才读取 manifest/scripts/references/runtime。
