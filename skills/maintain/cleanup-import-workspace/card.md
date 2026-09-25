# cleanup-import-workspace 检索卡

- status: active
- category: maintain
- role: role-a-import-maintenance
- canonical_skill: `maintain/cleanup-import-workspace/SKILL.md`
- manifest: `maintain/cleanup-import-workspace/manifest.yaml`
- description: 只清理一个已完成导入任务在 tmp 下的工作区；保护 checkpoint、失败证据、原件、raw/Wiki/RAG 与共享 runtime。
- triggers: 清理导入任务临时文件, 清理已完成导入任务, 清理题库导入临时文件, 清理视频导入临时文件, cleanup manifest
- use_when: 一个教材、课标、题库或视频导入任务已经完成全部 handoff 和验证，需要删除该 task id 的临时资产
- do_not_use_when: 全仓库空间治理, 清理共享模型/runtime, 清理多个任务, source-library 去重
- input: task id、完成证据、cleanup manifest、用户授权
- output: 任务级 dry-run、apply 和释放空间报告
- next_skills: maintain/cleanup-runtime-assets/SKILL.md

## 加载规则

路由时只读 card 或 `registry.yaml`。本 skill 为 primary 时先读 `SKILL.md`，并按原导入 skill 的完成标准核对 cleanup manifest。
