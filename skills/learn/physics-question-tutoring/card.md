# physics-question-tutoring 检索卡

- status: active
- category: learn
- role: role-d-physics-learning
- canonical_skill: `learn/physics-question-tutoring/SKILL.md`
- manifest: `learn/physics-question-tutoring/manifest.yaml`
- description: 对学生提供题目文字、公式和示意图的保守识别、题意确认、分层提示与规范解析。
- triggers: 学生拍题, 拍照识别并讲解, 讲解这道题, 题意确认, 分层提示, 物理题图片
- use_when: 学生上传物理题图片或 PDF, 需要逐层提示而非立即给答案, 需要先确认 OCR 题意再解题
- do_not_use_when: 导入试卷到题库, 修改已有题库解析, 教师备课检索, 分析真实学生成绩
- input: 题目文字、图片或 PDF, session_id, weak|strong model profile, confirm/hint/plan/solution 阶段
- output: question_parse.json, strong 视觉复核状态, 经 schema/数值校验的分层学习回复, 会话内图片资产
- next_skills: retrieve/llmwiki-rag-retrieval/SKILL.md, learn/physics-diagram-toolkit/SKILL.md

## 加载规则

路由只读本 card。成为 primary 后读取 `SKILL.md`；只有需要解析字段时再读取 schema reference。
