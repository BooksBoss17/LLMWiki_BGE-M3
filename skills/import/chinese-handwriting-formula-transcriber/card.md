# 中文手写与公式转写路由卡（chinese-handwriting-formula-transcriber）

- status: active
- category: import
- role: role-a-import-maintenance
- canonical_skill: `import/chinese-handwriting-formula-transcriber/SKILL.md`
- manifest: `import/chinese-handwriting-formula-transcriber/manifest.yaml`
- description: 面向中文手写和手写物理/数学公式学生答题卡图像的本地优先 OCR 工作流；调用 PaddleOCR/PaddleX/PaddleOCR-VL/PP-FormulaNet/TexTeller，并输出保守的机器转写。
- triggers: 中文手写转写, 手写公式OCR, 学生答题卡识别, 答题卡OCR, 物理公式LaTeX转写, PaddleOCR-VL, PP-OCRv6, PP-FormulaNet, TexTeller
- use_when: 转写中文手写答题卡，对学生答题纸运行本地 OCR 冒烟测试，裁剪 q09-q16 答题区域，将手写公式块转换为 LaTeX 候选结果，生成纯机器 OCR 转写 JSON/Markdown
- do_not_use_when: 将已转写的成绩表导入 StudentDataSQL，根据现有 SQL 数据分析学生表现，将 DOCX/PDF 教学文件转换为 Markdown，根据教学知识库回答物理问题
- input: 带 ASCII 任务 ID 和真实源图像路径的 UTF-8 任务卡；学生答题卡图像保存在 Skill 目录之外
- output: 私有运行 OCR 裁剪图、引擎日志、`auto_transcript.json`、`auto_transcript.md` 和保守的 `machine_final|machine_candidate|machine_abstain` 状态
- next_skills: import/student-data-import/SKILL.md, analyze/student-data-analysis/SKILL.md

## 加载规则

使用本卡片或 `registry.yaml` 进行路由。当本 Skill 为 primary 匹配时，首先只读取 `import/chinese-handwriting-formula-transcriber/SKILL.md`。只有在需要环境安装、模型预热、工作流修改或运行时诊断时，才读取 `manifest.yaml`、`references/architecture.md` 或脚本。
