# integration-package-update 检索卡

- status: active
- category: maintain
- role: role-c-kb-framework-admin
- canonical_skill: `maintain/integration-package-update/SKILL.md`
- manifest: `maintain/integration-package-update/manifest.yaml`
- description: 角色 C 本机专用整合包更新工作流；仅在用户明确授权更新/同步/执行整合包时使用，导出整合包时必须过滤。
- triggers: 用户授意更新整合包, 授权更新整合包, 执行整合包同步, 同步整合包, 更新部署包, 同步部署包, apply integration package
- use_when: 用户明确授权更新或同步整合包, 需要实际 apply 整合包镜像, 需要生成过滤 local-only skill 后的包内 registry/catalog/fixtures
- do_not_use_when: 只检查整合包完整性, 讨论整合包边界, 分析部署问题, 导入教学内容, 回答物理问题
- input: 用户授权的整合包更新请求, package root 路径, 当前开发库框架文件
- output: dry-run 或 applied 同步报告, local-only 过滤报告, 边界扫描结果
- next_skills: maintain/kb-framework-admin/SKILL.md

## 加载规则

路由时只读 card 或 `registry.yaml`。当本 skill 是 primary 时，先读取 `maintain/integration-package-update/SKILL.md`；任何写入整合包的操作前都必须先执行 dry-run。
