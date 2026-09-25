# git-repository-management 检索卡

- status: active
- category: maintain
- role: role-c-kb-framework-admin
- canonical_skill: `maintain/git-repository-management/SKILL.md`
- manifest: `maintain/git-repository-management/manifest.yaml`
- description: 管理 LLMWiki_BGE-M3 本地 Git 仓库，包括初始化、.gitignore 策略、提交、仓库健康和回滚检查。
- triggers: git, git仓库, .gitignore, 初始化仓库, 提交, commit, 版本控制
- use_when: 创建 Git 仓库, 维护 .gitignore, 提交框架变更, 检查仓库健康, 防止大型生成资产被跟踪
- do_not_use_when: 导入知识内容, 回答物理问题, 编辑教学输出, 仅重建教学 RAG
- input: Git 管理请求, 仓库状态, 跟踪策略
- output: 初始化或健康的 Git 仓库, 忽略策略, commit 或 status 报告
- next_skills: maintain/kb-framework-admin/SKILL.md

## 加载规则

路由时只读 card 或 `registry.yaml`。当本 skill 是 primary 时，先读取 `maintain/git-repository-management/SKILL.md`；只有 skill 要求时再读取 manifest、scripts、references 或 runtime。
