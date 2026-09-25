# student-data-analysis 检索卡

- status: active
- category: analyze
- role: role-b-retrieval-calling
- canonical_skill: `analyze/student-data-analysis/SKILL.md`
- manifest: `analyze/student-data-analysis/manifest.yaml`
- description: 安全只读查询 StudentDataSQL，生成匿名化成绩分析、作业分析、考试小题分析、班级薄弱点、学生知识点掌握和单生报告。
- triggers: SQL学生数据分析, 学生成绩分析, 作业分析, 学生数据库分析, 班级薄弱点, 学生掌握度, 单生期中知识点掌握, 期中考试报告, 座位号报告, STUDENT_DATA_DB_KEY, 考试小题分析, 方法错误分析, 答题卡分析, 失分分析
- use_when: 分析 StudentDataSQL 中的成绩或作业, 生成脱敏班级报告, 生成按座位号的单生掌握报告, 查询考试小题得分和知识点薄弱项
- do_not_use_when: 导入名册或成绩单, 预处理答题卡 manifest, 修改 schema/migration/scripts, 查询真实身份, 导入试卷到题库
- input: class_id, assessment_id, student_id 或 seat_no, 安全查询/报告需求
- output: JSON 分析结果, 脱敏 Markdown/Word/PDF 报告, 掌握率图表, `output/qa_<name>/` QA 产物
- next_skills: retrieve/llmwiki-rag-retrieval/SKILL.md, retrieve/lecture-generation/SKILL.md, retrieve/teaching-output-format/SKILL.md

## 加载规则

路由时只读本 card 或 `registry.yaml`。当本 skill 是 primary 时，先只读 `analyze/student-data-analysis/SKILL.md`；只有需要 schema/治理细节时才读 `StudentDataSQL/SCHEMA_STUDENT_DATA.md`。
