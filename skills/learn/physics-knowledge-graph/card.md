# physics-knowledge-graph 检索卡

- status: active
- category: learn
- role: role-d-physics-learning
- canonical_skill: `learn/physics-knowledge-graph/SKILL.md`
- manifest: `learn/physics-knowledge-graph/manifest.yaml`
- description: 根据 Role A import handoff 局部维护高中物理知识关系图谱、受控专题枢纽和反向链接。
- triggers: 更新高中物理知识关系图谱, 知识关系图谱维护, 专题枢纽更新, 图谱反向链接, import_handoff
- use_when: 教材课标题库视频导入完成后更新图谱, 新增受控专题关系, 补充受控页反向链接和统计
- do_not_use_when: 检查一般 Wiki 死链, 自由重写任意 Wiki 页面, 修改 StudentDataSQL, 创建角色 D 或改 router
- input: import_handoff.json, 受控 graph changes, 文件 hash, weak|strong model profile, 可选教师授权
- output: graph_update_result.json, 局部差异, 备份, 破坏性审批项
- next_skills: maintain/rag-management/SKILL.md

## 加载规则

路由只读本 card；成为 primary 后读取 `SKILL.md`，只在校验 handoff 或页面权限时读取对应 reference。
