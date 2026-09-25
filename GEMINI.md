# LLMWiki-BGE-M3 Agent 入口说明

本文件是兼容 Gemini CLI 的 shim。真正权威入口是同目录的 `AGENTS.md`；如果工具没有自动读取，请先手动打开 `AGENTS.md`。

关键规则：

- 用中文回复用户。
- 先读 `skills/registry.yaml`。
- 不要一次性加载全部 skills。
- 需要路由建议时运行：`python skills/_shared/scripts/skill_retriever.py --json "任务描述"`。
- 只加载 `load_now[0]` 指向的 primary canonical `SKILL.md`；`queued` 只代表后续阶段。
- 需要脚本、runtime 路径或 MCP/plugin 依赖时再读 `manifest.yaml`。
- 没有明确授权时，不要修改 `SCHEMA.md`、`BGE-M3/scripts/rag_pipeline.py` 或核心 skill 工作流。
- Role A 负责知识内容导入维护；Role B 负责教师侧检索与教学输出；Role C 负责框架、router、兼容层和 Git；Role D 负责学生学习、题库解析、标签和受控知识图谱。
