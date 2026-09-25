# kb-framework-admin 检索卡

- status: active
- category: maintain
- role: role-c-kb-framework-admin
- canonical_skill: `maintain/kb-framework-admin/SKILL.md`
- manifest: `maintain/kb-framework-admin/manifest.yaml`
- description: 维护 LLMWiki_BGE-M3 agent 框架、角色 A/B/C/D、registry、skill router、入口契约、兼容 shim 和验证脚本；新增或修改 skill 时强制按 0 失误率、高效率、长任务保障设计和验收。
- triggers: 框架维护, 角色C, registry, router, validator, 验证器, skill门禁, schema升级, 入口文件, AGENTS.md, SCHEMA.md, agent shim, 角色ABD测试, 创建角色D, 调整角色D路由, 组题流程问题, 生成skill问题, 生成作业流程问题, 作业生成流程问题, 题源标记泄露, 答题区错误, 角色回退错误, 整合包, 部署包, StudentDataSQL框架
- use_when: 新增或修改 canonical skill, 分析并修复 skill/schema/validator/门禁问题, 更新 agent 入口 shim, 更新 AGENTS/SCHEMA 契约, 创建或调整 Role D, 调整 Role A/B/C/D 边界, 修复 Role B 组题或 Role A 回退流程, 维护 registry/router, 测试 skill 路由, 验证 skills 框架
- do_not_use_when: 导入教学内容, 回答物理问题, 生成讲义, 仅重建教学 RAG
- input: 框架维护请求, 角色或路由兼容问题, 验证失败信息
- output: 已更新的框架文件, 路由夹具, 验证报告
- next_skills: maintain/git-repository-management/SKILL.md

## 加载规则

路由时只读 card 或 `registry.yaml`。当本 skill 是 primary 时，先读取 `maintain/kb-framework-admin/SKILL.md`；只有 skill 要求时再读取 manifest、scripts、references 或 runtime。
