# llmwiki-maintenance 检索卡

- status: active
- category: maintain
- role: role-a-import-maintenance
- canonical_skill: `maintain/llmwiki-maintenance/SKILL.md`
- manifest: `maintain/llmwiki-maintenance/manifest.yaml`
- description: 维护 LLMWiki 层：审计死链、结构性孤立页、index/log 新鲜度、视频三页结构、raw 指针泄漏和资源文件，并提供只读审计脚本。
- triggers: Wiki维护, 死链, 孤立页, 图谱健康, 视频三页
- use_when: Wiki 健康检查, 死链审计, 孤立页审计, 图谱健康, 视频三页验证
- do_not_use_when: 只重建向量索引, 简单检索, 只做源文件转换, Word 导出
- input: LLMWiki vault 当前状态, 维护请求, 导入完成后的交接信息
- output: 维护报告, 审计 JSON, 已修复 Wiki 问题, RAG 重建建议
- next_skills: maintain/rag-management/SKILL.md

## 加载规则

路由时只读 card 或 `registry.yaml`。当本 skill 是 primary 时，先读取 `maintain/llmwiki-maintenance/SKILL.md`；只有 skill 要求时再读取 manifest、scripts、references 或 runtime。
