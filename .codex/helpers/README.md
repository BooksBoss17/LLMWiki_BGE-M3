# Codex 辅助脚本

本目录只存放 Codex 在 Windows 环境下使用的本机辅助脚本。它们用于降低中文路径、PowerShell 编码、RAG 重建和长任务状态文件带来的执行风险，不改变 Hermes 或其他 agent 共享的 `skills/` 工作流。

## UTF-8 防护入口

当 Codex 需要处理中文路径、读取大段中文文件、重建 RAG，或记录考试/视频导入任务状态时，优先使用 PowerShell 包装器：

```powershell
.\.codex\helpers\codex_utf8_run.ps1 git-status
.\.codex\helpers\codex_utf8_run.ps1 read --path AGENTS.md --max-chars 800
.\.codex\helpers\codex_utf8_run.ps1 find --token video_import_he_20260704
.\.codex\helpers\codex_utf8_run.ps1 run --clear-python-env --cwd . -- python --version
.\.codex\helpers\codex_utf8_run.ps1 rag --kb-root .
```

## 长任务卡片

对带中文源路径的考试、视频、图片 OCR 等长任务，先写 UTF-8 JSON 任务文件，再只把 ASCII 任务 ID 传给 shell：

```powershell
.\.codex\helpers\codex_utf8_run.ps1 task init --task-id exam_import_20260706 --json-file .\task.json
.\.codex\helpers\codex_utf8_run.ps1 task show --task-id exam_import_20260706
.\.codex\helpers\codex_utf8_run.ps1 task path --task-id exam_import_20260706
```

任务文件保存在 `tmp/tasks/codex/`，该目录由 Git 忽略。

## 边界

- 这里的脚本是 Codex 专用保护层，不是跨 agent 的正式知识库能力。
- 需要让所有 agent 共享的规则，必须写入 `AGENTS.md`、`SCHEMA.md` 或对应 canonical skill。
- 视频下载 staging、重下缓存、`.m4s` 中间件等运行产物不得放入 Git。
