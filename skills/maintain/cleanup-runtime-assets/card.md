# cleanup-runtime-assets 检索卡

- status: active
- category: maintain
- role: role-c-kb-framework-admin
- canonical_skill: `maintain/cleanup-runtime-assets/SKILL.md`
- manifest: `maintain/cleanup-runtime-assets/manifest.yaml`
- description: 由 Role C 统计全仓库存储，并以 dry-run、哈希绑定和前后冒烟门禁清理 tmp、共享 runtime、RAG 可重建资产、旧缓存和重复原件别名。
- triggers: 全局清理tmp, 清理共享runtime, 清理RAG缓存, runtime cleanup, 存储空间清理, 冗余文件清理, 重复模型缓存
- use_when: 需要跨任务清理 tmp/runtime、做全仓库空间治理、去除确认无引用的模型缓存或哈希完全相同的原件别名
- do_not_use_when: 仅清理一个尚在执行的导入任务, 修改 raw/Wiki 内容, 删除真实学生数据, 普通 Git 管理
- input: 用户授权、清理计划、目标路径、大小/哈希/引用与验证基线
- output: dry-run 清单、受控 apply 结果、前后验证和释放空间报告
- next_skills: maintain/kb-framework-admin/SKILL.md

## 加载规则

路由时只读 card 或 `registry.yaml`。本 skill 为 primary 时先读 `SKILL.md`；执行计划时再调用脚本。
