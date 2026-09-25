# electromagnetic-rod-problems 检索卡

- status: active
- category: retrieve
- role: role-b-retrieval-calling
- canonical_skill: `retrieve/electromagnetic-rod-problems/SKILL.md`
- manifest: `retrieve/electromagnetic-rod-problems/manifest.yaml`
- description: 整理电磁感应单杆、双杆、导体棒和杆轨模型的知识点、分类、解题方法、易错点和复习材料。
- triggers: 单杆, 双杆, 单双杆, 单杆问题, 双杆问题, 双棒, 导体棒, 杆轨模型, 电磁感应综合, 终端速度, 安培力, 动量能量, 解题方法, 解题思路
- use_when: 讲解电磁感应杆轨模型, 总结单杆/双杆方法, 准备相关复习材料, 制作杆轨模型示意图和讲义
- do_not_use_when: 普通电磁感应检索即可回答, 导入题库, 导出 Word/PDF, 分析学生数据
- input: 杆轨模型主题, 题目或讲义目标, 输出形式要求
- output: 模型分类, 解题流程, 公式和易错点清单, 可选示意图资源
- next_skills: retrieve/llmwiki-rag-retrieval/SKILL.md, retrieve/md-to-docx/SKILL.md

## 加载规则

路由时只读本 card 或 `registry.yaml`。当本 skill 是 primary 时，先只读 `retrieve/electromagnetic-rod-problems/SKILL.md`；通常先由 `llmwiki-rag-retrieval` 提供证据。
