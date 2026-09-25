# exercise-knowledge-tags 检索卡

- status: active
- category: taxonomy
- role: role-d-physics-learning
- canonical_skill: `taxonomy/exercise-knowledge-tags/SKILL.md`
- manifest: `taxonomy/exercise-knowledge-tags/manifest.yaml`
- description: 独占维护稳定 `kp_id` registry、知识点层级/别名/停用关系和题目正式知识点字段；同时支持独立维护 `ai_extra_tags`。
- triggers: 知识点标签, 题库分类, knowledge_points, knowledge_point_ids, ai_extra_tags, 合并标签, 改名标签, 移动标签
- use_when: 给已有题目打标签, 新增或优化标签, 改名或移动知识点, 合并或停用标签, 维护稳定 kp_id
- do_not_use_when: 导入新的试卷或讲义题目, 从试卷中抽题, 生成讲义内容, 简单解题
- input: taxonomy-change.json, knowledge-points.yaml, weak|strong model profile, 可选题目 hash, 教师授权
- output: dry-run 差异, 稳定 ID registry, alias/deprecated 映射, 题目正式双字段元数据和可选 ai_extra_tags
- next_skills: learn/physics-knowledge-graph/SKILL.md, maintain/rag-management/SKILL.md

## 加载规则

路由时只读本 card 或 `registry.yaml`。当本 skill 是 primary 时，先只读 `taxonomy/exercise-knowledge-tags/SKILL.md`；只有该 skill 要求时才读取 manifest/scripts/references/runtime。
