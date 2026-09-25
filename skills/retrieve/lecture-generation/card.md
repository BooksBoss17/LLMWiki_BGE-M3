# lecture-generation 检索卡

- status: active
- category: retrieve
- role: role-b-retrieval-calling
- canonical_skill: `retrieve/lecture-generation/SKILL.md`
- manifest: `retrieve/lecture-generation/manifest.yaml`
- description: 基于 Wiki+RAG 证据用题库原题原图生成讲义、作业、练习卷或试卷；净版练习与逐题答案成对交付，原创图强制区分黑白 question image 和彩色 solution image 并做 hash/geometry 用途检查；源题缺失或错图回退 Role A，流程问题回退 Role C。
- triggers: 讲义生成, 作业生成, 复习讲义, 组卷, 题库原题组题, 题库原题, 原题原图, 原创组题, 原创题, 原创练习, 备课, 输出
- use_when: 生成讲义, 生成作业, 生成练习, 生成试卷, 练习和答案分开输出, 不分学生版教师版, 用题库原题原图组题, 用户确认题库覆盖不足后补原创题, 制作复习材料, 输出备课材料
- do_not_use_when: 只回答简单问题, 修复题库题干缺失或错图, 导入 raw 材料, 只把现有 Markdown 转 Word, 只检查输出格式
- input: 教学目标, 章节或主题, 约束条件, 输出类型
- output: `output/` 下只显示本地题号的 `<名称>` 与 `<名称>答案` Markdown/DOCX/PDF 成对文件, `output/assets_<name>/` 下隔离的 question/solution 图像, `tmp/tasks/<task_id>/question_source_map.json` 与 `diagram_assets.json`, 两套独立 OfficeCLI QA 交接
- next_skills: learn/physics-diagram-toolkit/SKILL.md（仅原创/改编图）, retrieve/md-to-docx/SKILL.md, retrieve/teaching-output-format/SKILL.md

## 加载规则

路由时只读本 card 或 `registry.yaml`。当本 skill 是 primary 时，先只读 `retrieve/lecture-generation/SKILL.md`；只有该 skill 要求时才读取 manifest/scripts/references/runtime。
