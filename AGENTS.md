# LLMWiki_BGE-M3 Agent 兼容契约

本仓库是高中物理知识库，需兼容 Hermes、Codex、Claude Code、OpenCode、Gemini CLI、Cursor、Cline/Roo、Copilot Chat、Aider、Continue 等自主 agent。

## 语言与用户背景

- 除非用户明确要求，否则用中文沟通。
- 用户是高中物理教师兼班主任，使用鲁教版教材。
- 质量优先于速度。OCR/VLM 结果不得留下占位符或猜测内容。

## 权威路径

- `PROJECT_LAYOUT.yaml` 是机器可读路径权威源；脚本必须通过 `skills/_shared/scripts/project_paths.py` 解析路径，不得新增本机绝对路径硬编码。
- 四库固定为 `raw/`、`LLMWiki/`、`BGE-M3/`、`StudentDataSQL/`。
- 原件库为 `source-library/`，能力层为 `skills/`，正式交付区为 `output/`，唯一临时工作区为 `tmp/`。
- `tmp/` 固定按 `tasks/`、`logs/`、`state/`、`renders/`、`downloads/`、`staging/` 分类；Codex 专用辅助脚本位于 `.codex/helpers/`。
- skills registry 路径 ID 为 `skills.registry`；整合包镜像路径 ID 为 `integration.skills-mirror`。
- 可用 `LLMWIKI_KB_ROOT`、`LLMWIKI_MODEL_RUNTIME_ROOT`、`LLMWIKI_INTEGRATION_SKILLS_ROOT` 覆盖对应本机路径。

## 权威顺序

- `AGENTS.md` 是唯一权威 agent 入口契约。
- `CLAUDE.md`、`GEMINI.md`、`.roo/`、`.cursor/`、`.github/`、`.clinerules`、`.cursorrules` 都是 shim，必须指回本文件和 router。
- `SCHEMA.md` 是锁定的知识库 schema 与治理文档，定义仓库结构、角色、权限和长期不变量。
- `skills/registry.yaml` 是 canonical skill 索引。
- 只有 router 选中 primary 后，对应 `SKILL.md` 才在当前阶段具备工作流权威性。

## 通用规则：不要加载全部 Skills

所有 agent 必须使用 registry-router 模式：

1. 先读 `skills/registry.yaml`。
2. 可用时运行专用检索器：

```bash
python skills/_shared/scripts/skill_retriever.py --json "任务描述"
```

3. 将任务归入 Role A、Role B、Role C 或 Role D。
4. 只加载 `load_now[0]` 指向的一个 primary canonical `SKILL.md`。
5. `queued` 只代表后续阶段；当前阶段不要提前读取。
6. 只有需要脚本、runtime 路径或 MCP/plugin 依赖时，才读 primary skill 的 `manifest.yaml`。
7. 只有被当前 skill 明确要求时，才读 `references/`、scripts 或 runtime 细节。
8. 禁止批量读取全部 `SKILL.md`，这会破坏节省上下文的设计。

## 角色

### Role A — 知识导入维护

用于导入和维护知识内容：OCR、文档转换、视频转写、raw/Wiki/RAG 同步、质量检查和导入类 skill 维护。教材、课标、题库或视频导入必须完成 Role D 图谱 handoff 后才能最终重建 RAG 并声明完成。

常用 primary skills：

- DOCX/PDF/OMML/EQ/WMF/OCR：`skills/import/bemarkdown/SKILL.md`
- 教材导入：`skills/import/textbook-import/SKILL.md`
- 课程标准/评价体系导入：`skills/import/standards-import/SKILL.md`
- 题库/试卷/讲义题目导入：`skills/import/exercise-bank-import/SKILL.md`
- 学生数据导入预处理：`skills/import/student-data-import/SKILL.md`
- B 站/视频转写导入：`skills/import/video-transcript-import/SKILL.md`
- Wiki 健康与内容同步：`skills/maintain/llmwiki-maintenance/SKILL.md`
- RAG 重建与查询维护：`skills/maintain/rag-management/SKILL.md`
- 单个已完成导入任务的临时工作区清理：`skills/maintain/cleanup-import-workspace/SKILL.md`

### Role B — 检索调用

用于教师侧答疑、备课、解题、讲义生成、组卷、Word/PDF 输出、PPT 分析和安全学生数据分析。学生拍题、分层提示和学习会话归 Role D。

常用 primary skills：

- 知识库检索/答疑：`skills/retrieve/llmwiki-rag-retrieval/SKILL.md`
- 讲义/练习/试卷生成：`skills/retrieve/lecture-generation/SKILL.md`
- Markdown 转 Word/PDF：`skills/retrieve/md-to-docx/SKILL.md`
- 教学输出格式：`skills/retrieve/teaching-output-format/SKILL.md`
- PPT 分析：`skills/analyze/teaching-ppt-analysis/SKILL.md`
- 学生成绩/作业分析：`skills/analyze/student-data-analysis/SKILL.md`

### Role C — 知识库框架管理员

用于维护知识库框架：skills 架构、registry/router、入口兼容层、Role A/B/D 路由测试、验证脚本、Git 仓库策略和整合包边界。

Role C 可以调整和测试 Role A/B/D 的框架行为，但必须保留内容质量门禁、skills 检索隔离和四库架构。

常用 primary skills：

- 框架/router/入口维护：`skills/maintain/kb-framework-admin/SKILL.md`
- 一次性多角色 agent campaign、批次调度、审计、失败恢复和续跑：`skills/maintain/agent-campaign-orchestration/SKILL.md`
- Git 仓库管理：`skills/maintain/git-repository-management/SKILL.md`
- 全仓库 tmp、共享 runtime、RAG 可重建资产和哈希重复别名清理：`skills/maintain/cleanup-runtime-assets/SKILL.md`
- 本机整合包更新：`skills/maintain/integration-package-update/SKILL.md`，只在用户明确授权更新/同步/执行整合包时使用，且不得导出到整合包。

Role C 的 cleanup 权限必须先 dry-run。普通 tmp/运维 runtime 清理需要用户明确授权；共享 runtime 缓存、RAG 可重建资产和 `source-library/` 哈希重复别名还需要类别级额外授权与前后验证。Role C 不得删除活动/未完成任务、唯一原件、有效环境中的单个依赖文件、正式 `raw/LLMWiki/output` 内容或真实 StudentDataSQL 数据。单个已完成导入任务的工作区清理由 Role A 的 `skills/maintain/cleanup-import-workspace/SKILL.md` 负责，不得借此扩大到全局 runtime。

多 agent 长任务由 `agent-campaign-orchestration` 使用 batch v3 控制面：`init → dispatch → submit → recover → status`。主 agent 只代理用户意图、调度和统计；`dispatch` 按相同 status、source SHA-256 与 capability 稳定组批。任何 queued/revalidation 题缺少已审计 `source_slice_ref` 时，先进入同源最多5题的 A-only source-alignment wave；card 按 `source_slice_v3.schema.json` 为每题预建唯一 manifest 路径和隔离 artifact root，locator 必须声明明确坐标空间，worker 只能覆盖这些预登记产物，再提交 source ref、题目切片和 evidence 并交全新 auditor 验收。source 重试生成新 candidate 时必须清空旧 audit/defects，主 agent 以本次 `submit.ok` 和当前 status 判断结果。source 阶段不受 complex canary 单题限制。可对齐题完成后才进入纯 Role D 内容修正，`complex/critical` 内容 canary 每批只派1项。batch card 预建 result 骨架、给出唯一合法的 `worker_contract.producer_role` 并明确 `stage_plan`；proposal 必须带已验证 slice。worker 无法完成时也必须逐项提交 `runtime_failed/source_blocked/unresolved`。通过项才由 canonical writer 写回；失败项只允许一个全新 repair，repair 复用 slice 并跳过 Role A，再由全新 auditor 复审，第二次失败即 `blocked`。真正缺失原件的题 fail closed，但不阻塞其他卷。每个 campaign 同时只有一个 active run，最多并行四个不同 campaign；不使用 source/target 生命周期锁、lease、assignment、attestation、registration token、cutover/adoption 或调度层 rollback。控制面只在状态落盘瞬间使用短时互斥，writer 只在写入瞬间执行哈希比较和原子事务。唯一 `run_id`、batch-card SHA-256、落盘状态和幂等 receipt 用于拒绝迟到结果及中断续跑。Codex capability 静态映射保持不变；教师授权仍绑定授权记录、ledger SHA-256、允许字段、writer、期限及 `destructive=false`。

主 agent 必须先确认有空闲 isolated-worker 槽位。平台有槽位查询时先查询，再 `dispatch` 并立即以 `fork_context=false` spawn；平台无查询接口时先 spawn 本批实际 worker 待命，再 `dispatch` 并把卡发送给同一 worker，不得另建 disposable capacity probe。把 spawn 返回的真实 agent ID 交给 worker，并要求写入 `producer.worker_instance_id`。executor、auditor、repair、重试必须使用不同 worker。worker 只覆盖预登记 result，不调用控制面命令；主 agent 直接调用 `submit` 作为 schema、身份、哈希和 writer 的唯一确定性门禁，并读取紧凑的 `submit.items`，不得另写临时脚本重复解析内部 state/artifact。主 agent 不得生成 proposal、evidence、dry-run 或 audit；平台不能提供 isolated worker 时 fail closed。`run_id`、phase 和 card hash 不能单独证明独立审计，writer 与报告都必须核对不同的 executor/auditor producer identity。

历史 `passed/no_change_passed` 题若因旧流程缺少可信 producer identity 而必须重验，禁止手改 campaign state；在无 active run 时使用 `recover --revalidate-passed <question_id> --reason <reason>`。控制面必须把旧 candidate/audit/receipt 保存到 `revalidation_history`，再转入 `revalidation_queued`，并完整重走全新 executor、全新 auditor 与 writer 门禁。

### Role D — 物理学习 Agent

用于学生物理题识别与分层辅导、确定性物理绘图，以及教师授权后的题库题干/答案/详解/题图校对、稳定知识点标签和受控知识关系图谱维护。

Role D 有两种模式：

- `student`（默认）：只读 Wiki + RAG，只能写 `output/learning_sessions/<session_id>/`。
- `curator`：写入前必须 proposal + dry-run；普通 apply 需要 `--teacher-authorized`，破坏性操作还需要 `--allow-destructive`。显式 `strong` 档可在同一 hash 绑定 proposal 中修订题干、答案、详解、`assets`，并新增或修复题目图、解析图。覆盖现有图片时必须绑定目标图当前 SHA-256 并自动备份。

Role D 有两个显式模型档：

- `weak` 为跨 agent 默认档，使用短上下文、固定 schema 和一次验证。
- `strong` 只在明确选择时启用；Codex Role D 默认提示选择该档。它允许本地 OCR 后复核去 EXIF 图像、按需深度检索、多方法推导、完整题目记录校对、AI 自主标签和渲染后视觉复核。strong curator 的绘图 skill 可将通过复核的图受控写回题库。它不绕过 student/curator、hash、dry-run、教师授权或破坏性门禁。

常用 primary skills：

- 拍题识别、题意确认、分层提示和规范解析：`skills/learn/physics-question-tutoring/SKILL.md`
- 受力图、坐标图、矢量图、轨迹、原图批注及 strong curator 题库图写回：`skills/learn/physics-diagram-toolkit/SKILL.md`
- 题库题干/答案/详解/题图维护：`skills/learn/exercise-solution-curation/SKILL.md`
- 高中物理知识关系图谱：`skills/learn/physics-knowledge-graph/SKILL.md`
- 稳定知识点标签和 `kp_id`：`skills/taxonomy/exercise-knowledge-tags/SKILL.md`

正式 `knowledge_points`、`knowledge_point_ids` 和任何 `kp_id` 的新增/分配只能由 `exercise-knowledge-tags` 管理。`ai_extra_tags` 是非标准自由标签，strong curator 可在题目校对或绘图写回时顺手维护，但不得伪装成 `kp_id` 或标准知识点路径。

Role D 不读取或修改真实 StudentDataSQL。OCR、公式识别和绘图由本地工具链输出结构化结果；`strong` 只能查看会话中的脱敏图并提交 hash 绑定的复核 JSON。关键识别项不确定时必须请求确认，禁止补猜。

在 batch v3 中，Role D executor 只提交暂存稿，Role D auditor 不拥有 writer 权限。canonical writer 必须回查 active auditor batch、batch-card SHA-256、逐题 `passed` verdict、教师授权、proposal/dry-run 及 target/source/media hash；写回后生成 `apply_receipt v3` 并运行题目 RAG 兼容、图片和公式检查。验证失败由 writer 在本次调用内恢复备份，不进入 campaign rollback。进程在写回后中断时，`recover` 只依据 apply receipt 与当前 post-hash 补记完成；无匹配 receipt 时不得猜测成功。旧 mission v2 card、receipt 或迟到结果仅作只读历史证据，v3 writer 和控制面必须拒绝。

## 四库架构

- `raw/` 保存源文件和清洗后的 Markdown，是教学 RAG 的主要来源。
- `LLMWiki/` 保存自包含 Wiki 页面和 wikilinks。Wiki 正文不得只是 raw 转引壳。
- `BGE-M3/` 保存教学 RAG 框架；从 raw 清洗 Markdown 生成的 chunks/vectors 位于 `BGE-M3/runtime/`，不从 Wiki 或 `skills/` 建教学索引。
- `StudentDataSQL/` 保存结构化数据框架；真实数据与运行资产只进入 `StudentDataSQL/runtime/`。

raw 内容改变后，声明任务完成前必须同步 Wiki 和 RAG。

## StudentDataSQL 子系统

- `StudentDataSQL/` 是独立第四子系统，用于学生成绩、作业、日常观察、掌握度摘要和安全教学报告。
- 真实学生数据不得进入 `raw/`、`LLMWiki/`、`BGE-M3/`、`skills/`、Git 或向量索引。
- Agent 访问必须使用 `skills/analyze/student-data-analysis/SKILL.md` 指定的参数化脚本和白名单安全视图。
- schema、migrations、scripts、templates、docs、合成 fixtures 可跟踪；`runtime/{db,imports,exports,backups,logs,exam-reports}/` 为本地运行/报告库，Git 忽略。
- `runtime/exam-reports/` 保存预处理好的试卷分析 Markdown，用于 SQL 侧成绩报告；它不是教学 RAG 来源。
- StudentDataSQL 的 schema/router/Git 策略变更属于 Role C；普通分析和报告属于 Role B；真实数据导入属于 Role A 的 `student-data-import`。
- Role D 只通过稳定 `kp_id` 与 StudentDataSQL 发生 schema 级联动，不访问真实学生记录。

## 锁定区

除非用户在当前任务中明确授权管理员修改，否则不要修改：

- `SCHEMA.md`
- `BGE-M3/scripts/rag_pipeline.py`
- `skills/` 下核心工作流步骤和 schema
- 权限控制、检索模式、上下文预算定义

如果任务看起来必须修改锁定区，先停下请求授权；若用户已授予 Role C 管理员权限，则按最小范围修改并运行验证。

## Skills 检索隔离

- skill routing 只读取 `skills/registry.yaml`、skill `card.md` 和必要 frontmatter。
- 完整 `SKILL.md` 只在 primary route 确定后加载。
- skill 检索缓存只属于 `skills/_ops/runtime/state/skill-retrieval/`。
- 禁止把 `skills/` 内容加入教学 BGE-M3 RAG 索引。
- 禁止把 `output/learning_sessions/`、Role D runtime/report 或题目图片加入教学 BGE-M3 RAG 索引。

## 质量门禁

- 导入任务至少 3 轮质量检查，且不能有未解决问题。
- OCR/VLM 清理必须修复乱码公式和文本，不得留占位符。
- 原始源文件必须永久保留。
- 讲义/试卷：学生版不得包含答案；教师版按请求包含提示/答案。
- 视频导入保持三页结构：父页面/总览 + 知识笔记 + 教学简案。
- 教材、课标、题库和视频导入必须在 Wiki 同步后生成 `import_handoff.json`，运行 Role D 图谱更新并通过 handoff verify，最后才重建 RAG。`bemarkdown` 和 `student-data-import` 不触发该阶段。
- RAG 重建优先使用安全命令，除非 loaded skill 另有说明：

```bash
env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

Codex on Windows 可使用：

```powershell
python .codex/helpers/windows_utf8_guard.py rag --kb-root .
```

## Runtime 资产

大型 OCR/VLM/STT、portable tools、OfficeCLI 和 Role D 共享 runtime 资产统一放在 `skills/_shared/model-tools/runtime/`：

- 权重和工具统一由 `skills/_shared/model-tools/registry.yaml` 登记，任务默认值由 `profiles.yaml` 管理。
- 所有相关 skill 必须按共享模型 ID 或 `LLMWIKI_MODEL_RUNTIME_ROOT` 解析；不得新增 skill-local `models/`、`.venvs/` 或用户缓存硬编码。
- Node、FFmpeg/ffprobe、LibreOffice、uv、OfficeCLI、MCP server 与所需 CPython 必须按组件 ID 解析，禁止依赖全局 npm cache、`npx` 或 Agent 私有 runtime。
- Git 始终忽略 `runtime/`。只有用户显式授权整合包 runtime 同步时，`sync_integration_package.py --include-runtime` 才按 registry allowlist 携带当前有效环境、模型和工具；生产内容、缓存、索引与 disabled/legacy 组件仍禁止导出。
- Role D 轻量绘图/解析环境位于 `skills/_shared/model-tools/runtime/envs/role-d-learning/`；源需求位于 `skills/learn/_shared/requirements/`，便携环境使用 `skills/_shared/model-tools/requirements/role-d-learning-py311.txt` 锁版本。
- Skill 运维日志、状态、报告、备份与 staging 只进入 `skills/_ops/runtime/`；长期可复核的脱敏结论进入 `skills/_ops/audits/`。

优先使用 wrapper：

- `python skills/_shared/model-tools/scripts/model_runtime.py resolve --id <model-id> --json`
- `python skills/_shared/model-tools/scripts/model_runtime.py doctor --all --json`
- `python skills/_shared/model-tools/scripts/model_runtime.py exec --id <tool-id> -- <args>`
- 各 skill 的长期 wrapper 必须位于其 `scripts/` 下，并调用共享 resolver。

## MCP / Plugin 依赖

skill 自有 MCP/plugin 依赖必须在对应 `manifest.yaml` 的 `mcp_servers` 中声明，并集中登记到 `skills/_registry/mcp_servers.catalog.yaml`。调用外部 MCP 前先验证依赖，不要假设工具存在。

Hermes profile 检查：

```bash
python skills/_shared/scripts/check_mcp_requirements.py --json
```

已知必需 server：

- `bilibili-search`（bundled Node + `bilibili-mcp-js`），用于 `skills/import/video-transcript-import/SKILL.md`；native MCP 不可用时调用 `mcp_call.py`。

新设备部署或依赖修复：

```bash
python skills/_shared/scripts/install_skill_dependencies.py --json
python skills/_shared/scripts/install_skill_dependencies.py --install --all --json
```

bootstrap 脚本集中检查 portable MCP、RAG runtime、BGE-M3 模型和 OCR/STT 环境，并在项目 venv 调用时清理 `PYTHONPATH`/`PYTHONHOME`。

## Git 策略

本地 Git 仓库由 Role C 通过 `skills/maintain/git-repository-management/SKILL.md` 维护。

- 跟踪框架文件、skills 轻量源文件、`LLMWiki/**/*.md`、`raw/**/*.md` 和轻量配置。
- 不跟踪模型、venv、RAG runtime、cache、`source-library/` 二进制、media、`output/`、`tmp/` 或 StudentDataSQL runtime 数据。
- `.gitignore` 必须与该策略保持一致。

## Codex Windows 编码保护

以下规则只约束 Codex 执行，不改变共享 `skills/` 工作流。

- 在 Windows 上，Codex 不应把中文路径字面量直接塞进 inline PowerShell/Python；尽量从仓库根枚举、JSON 任务文件或 ASCII token 定位路径。
- 优先使用 `.\.codex\helpers\codex_utf8_run.ps1 <guard-subcommand>`。它会设置 UTF-8 控制台、`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，再调用 `.codex/helpers/windows_utf8_guard.py`。
- 长导入或中文源路径任务，先在 `tmp/tasks/codex/` 建 ASCII task id 的 UTF-8 JSON 任务卡，再把 ASCII task id 传给 shell。
- 调用读写中文路径的 Python 子进程前设置 `PYTHONUTF8=1` 和 `PYTHONIOENCODING=utf-8`；调用项目 venv 时同时清理 `PYTHONPATH`、`PYTHONHOME`。
- 检查文件或 Git 状态时，可优先使用 `.codex/helpers/windows_utf8_guard.py read` 和 `git-status`。
- 不要把 PowerShell 对中文的乱码显示当作真实内容证据；用 UTF-8 读取或验证脚本确认。
- 如果需要所有 agent 共享的长期规则，必须更新共享契约，不要藏在 Codex-only helper 中。

## 完成标准

最终回复必须说明：

- 具体改动文件。
- 运行过的命令/测试。
- 验证结果。
- 是否触及锁定区。
- 如工具或模型失败，必须如实报告，不得编造输出。

## 扩展参考

兼容细节见 `skills/_shared/references/agent-compatibility.md`。
