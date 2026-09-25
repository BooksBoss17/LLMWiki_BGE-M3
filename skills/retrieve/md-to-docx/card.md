# md-to-docx 检索卡

- status: active
- category: retrieve
- role: role-b-retrieval-calling
- canonical_skill: `retrieve/md-to-docx/SKILL.md`
- manifest: `retrieve/md-to-docx/manifest.yaml`
- description: 将已有 Markdown 教学内容导出为 Word/PDF，处理 LaTeX-to-OMML 公式、Word COM 后处理、页码检查，以及可控的 OfficeCLI OpenXML/视觉 QA。
- triggers: Word输出, DOCX, PDF, OMML, 排版, md-to-docx, LaTeX-to-OMML, OMML转换器, 公式导出, OfficeCLI, 视觉校验, OpenXML校验, 输出质量检查
- use_when: Markdown 导出 Word, 生成 DOCX/PDF, 公式转 OMML, 维护 md-to-docx 转换器, 最终文档排版, 对生成文件运行 OfficeCLI QA
- do_not_use_when: 从零生成教学内容, 将 DOCX/PDF 导入 raw, 简单知识检索
- input: 已有 Markdown 教学文档, 输出格式要求, 样式约束
- output: DOCX/PDF 文件, 排版校验报告, 可选 `output/qa_<name>/` OfficeCLI QA 产物
- next_skills: retrieve/teaching-output-format/SKILL.md

## 加载规则

路由时只读本 card 或 `registry.yaml`。当本 skill 是 primary 时，先只读 `retrieve/md-to-docx/SKILL.md`；只有该 skill 要求时才读取 manifest/scripts/references/runtime。
