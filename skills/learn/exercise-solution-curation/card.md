# exercise-solution-curation 检索卡

- status: active
- category: learn
- role: role-d-physics-learning
- canonical_skill: `learn/exercise-solution-curation/SKILL.md`
- manifest: `learn/exercise-solution-curation/manifest.yaml`
- description: 通过 proposal、question id 和文件 hash 安全校对题库题干、答案、详解、必要题图和 AI 自主标签；完整题目写入仅限 strong curator。
- triggers: 校对题库题干答案详解, 继续校对, 继续校对题库, Role D strong 校对题干答案详解, 题库校对断点续跑, 修改题干, 修改题目解析, 修改 MC0001426 的题干与解析, 补写详解, 修复答案解析不一致, 修复题目原图, 题库校对, 题库解析维护, review exercise answer, modify question stem, detailed solution, modify MC analysis, proofread question bank answer
- use_when: 指定已有题目需要修改题干/答案/详解, 跨 Codex 任务继续长校对 campaign, 导入后发现解析或 OCR 风险, 需要新增或标注规范示意图
- do_not_use_when: 学生只想听题, 导入新试卷, 批量改知识点名称
- input: curation_proposal.json 或 campaign task_card.json, question_id, 目标文件 hash, strong verification 原件证据与独立答案核验, weak|strong model profile, 教师授权
- output: dry-run 差异, 更新后的题干/答案/详解/题图/ai_extra_tags, 兼容性和图片校验报告, campaign handoff/status
- next_skills: taxonomy/exercise-knowledge-tags/SKILL.md, learn/physics-knowledge-graph/SKILL.md, maintain/rag-management/SKILL.md

## 加载规则

路由只读本 card；成为 primary 后读取 `SKILL.md`，生成 proposal 时再读取 schema reference。
