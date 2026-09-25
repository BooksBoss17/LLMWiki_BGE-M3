# teaching-ppt-analysis 检索卡

- status: active
- category: analyze
- role: role-b-retrieval-calling
- canonical_skill: `analyze/teaching-ppt-analysis/SKILL.md`
- manifest: `analyze/teaching-ppt-analysis/manifest.yaml`
- description: 分析课堂 PowerPoint/PPTX 课件的内容质量、教学逻辑、视觉设计和投影可读性，支持 PPTX 结构提取和 PDF 渲染辅助脚本。
- triggers: PPT分析, 课件分析, 课堂投影, 教学逻辑
- use_when: 分析 PPT, 审查 PowerPoint 课件, 检查课堂投影可读性, 审核教学逻辑, 做逐页证据化评估
- do_not_use_when: Word/PDF 导出, raw 材料导入, 简单知识检索, 题库导入
- input: PPT/PPTX 文件, 幻灯片截图, 分析请求
- output: 课件分析报告, 提取的幻灯片结构, 渲染/contact-sheet 产物, 教学改进建议
- next_skills: retrieve/lecture-generation/SKILL.md

## 加载规则

路由时只读本 card 或 `registry.yaml`。当本 skill 是 primary 时，先只读 `analyze/teaching-ppt-analysis/SKILL.md`；只有该 skill 要求时才读取 manifest/scripts/references/runtime。
