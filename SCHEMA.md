# LLMWiki_BGE-M3 知识库规范

> **锁定区**：本文件仅管理员可修改。标准权限下 agent 必须遵守本规范，不得自行修改。

## 知识库概览

高中物理教学知识库，覆盖鲁教版高中物理教材（必修 3 册 + 选择性必修 3 册）、课程标准与高考评价体系、题库（5 大类 17 子分类）、教学视频（3 个 UP 主 66 个视频目录，三层文件体系）、三轮冲刺讲义（16 份讲义 + 16 份设计文档）。

**知识库入口**：从 `PROJECT_LAYOUT.yaml` 定位；可用 `LLMWIKI_KB_ROOT` 覆盖，不在脚本中硬编码本机绝对路径。

**最近数据快照（2026-07-04，后续导入和框架更新可能已增加内容）**：

- `raw/textbooks`: 6 本教材，42 MD + 2387 图
- `raw/standards`: 5 份标准 MD
- `raw/exercises`: 1406 题（5 大类 17 子分类）
- `raw/lectures`: 16 份讲义 + 16 份设计文档 = 32 MD
- `raw/transcripts`: 66 个视频目录，227 MD（逐字稿/知识笔记/教学简案/keyframe_vlm_notes 等）+ 720 关键帧
- `LLMWiki`: 1014 个 MD 页面，其中 `concepts/` 活跃概念页 636 页
- `BGE-M3/runtime/index`: 4582 chunks / 4582 vectors，FAISS 索引约 17.9 MB（本条为 2026-07-04 历史快照）
- `BGE-M3` source_type 分布: textbook=1703, exercise=1406, transcript=1243, lesson_design=111, standard=103, lecture=16
- `skills/`: 25 个 active canonical skills + `registry.yaml` + manifests + runtime/models/envs；本机还包含 local-only 的整合包更新 skill
- 整合包 `Skills/`: canonical skills 普通镜像（不含大模型/venv）
- OCR、VLM、公式识别、STT、OfficeCLI 和 Role D 环境共用 `skills/_shared/model-tools/`：根部是可迁移的注册表、配置、requirements 和脚本，`runtime/` 是本机忽略的大模型、env、工具与基准输出。相关 skill 不得拥有独立权重副本。

## 入口与权威关系

- `AGENTS.md` 是所有 agent 的唯一权威入口契约，负责说明如何进入仓库、如何路由、如何避免一次性加载全部 skills。
- `SCHEMA.md` 是知识库结构与治理规范，负责定义三库架构、角色边界、权限控制、锁定区和长期不变量。
- `skills/registry.yaml` 是 skills 的权威索引，记录角色、skill 路径、manifest 和触发词。
- `skills/_shared/scripts/skill_retriever.py` 是通用路由 CLI，应该优先于旧入口 `agent_skill_router.py` 使用。
- `.roo/`、`.cursor/`、`.github/`、`CLAUDE.md`、`GEMINI.md`、`.clinerules`、`.cursorrules` 只是兼容 shim，不得另立规则。

推荐路由入口：

```bash
python skills/_shared/scripts/skill_retriever.py --json "任务描述"
```

## Agent 角色区分

本知识库有四类 agent 角色，由任务类型和 router 共同判定。

### 角色 A：知识内容导入维护 Agent

**职责**：负责知识内容导入、raw/Wiki/RAG 内容同步、质量检查和导入类 skill 维护。知识点标签、题库解析和受控知识图谱归角色 D。

**边界**：角色 A 不负责 skills 框架、router、入口 shim、Git 仓库策略等框架级维护；这些归角色 C。

**常见 primary skills**：

- DOCX/PDF/OMML/EQ/WMF/OCR: `skills/import/bemarkdown/SKILL.md`
- 教材导入: `skills/import/textbook-import/SKILL.md`
- 课程标准/评价体系导入: `skills/import/standards-import/SKILL.md`
- 题库/试卷/讲义题目导入: `skills/import/exercise-bank-import/SKILL.md`
- Bilibili/视频转写导入: `skills/import/video-transcript-import/SKILL.md`
- Wiki 内容健康与同步: `skills/maintain/llmwiki-maintenance/SKILL.md`
- RAG 重建与查询维护: `skills/maintain/rag-management/SKILL.md`

**核心工作流**：

1. 原件存档到 `source-library/`（永久保存不删除）。
2. 按对应 skill 完成转换、OCR、结构化提取或转写。
3. 生成标准化 MD 文件到 `raw/` 对应分类。
4. 质量检查至少 3 轮，最终 0 unresolved issues。
5. 同步 `LLMWiki/`，创建或更新自包含 Wiki 页面。
6. 生成 `import_handoff.json`，完成角色 D 图谱 dry-run、安全 apply 或破坏性审批，并通过 handoff verify。
7. 同步 `BGE-M3/`，使用既定命令重建向量索引。
8. 入库后检查 raw 完整性、Wiki 页面、RAG chunks、临时文件和 `_tmp_` 残留。

**禁止事项**：

- 标准权限下不得写临时批量脚本绕过 skill。
- 不得跳过 BeMarkdown 或对应导入 skill 的完整流程。
- 不得保留占位符，例如“见解析原件”。
- 不得删除原件。
- 导入完成后必须清理临时文件。

### 角色 B：检索调用 Agent

**职责**：基于知识库内容回答教师问题、辅助备课、解题、组卷、讲义生成、Word/PDF 输出和 PPT 分析。学生拍题、分层提示和学习会话归角色 D。

**常见 primary skills**：

- 知识库检索/回答: `skills/retrieve/llmwiki-rag-retrieval/SKILL.md`
- 讲义/学案/试卷生成: `skills/retrieve/lecture-generation/SKILL.md`
- Markdown 转 Word/PDF: `skills/retrieve/md-to-docx/SKILL.md`
- 教学输出格式: `skills/retrieve/teaching-output-format/SKILL.md`
- PPT 分析: `skills/analyze/teaching-ppt-analysis/SKILL.md`
- 学生成绩/作业安全分析: `skills/analyze/student-data-analysis/SKILL.md`

**检索规则**：

- 不要直接用 read_file 翻 `raw/` 目录全文，先加载检索 skill 确定检索模式。
- 不要直接调用 `rag_pipeline.py`，先加载 skill 确定 dense/sparse/colbert 配置。
- 简单问答/解题默认走 `llmwiki-rag-retrieval`，不要抢先加载讲义生成或 Word 输出 skill。

### 角色 C：知识库框架管理员

**职责**：负责整个知识库框架的更新和维护，包括 `skills/` 架构、registry/router、入口兼容层、一次性多角色 agent campaign、角色 A/B/D 功能调整与测试、验证脚本、Git 仓库策略。

**常见 primary skills**：

- 框架/router/入口维护: `skills/maintain/kb-framework-admin/SKILL.md`
- 多 agent campaign 编排、审计和续跑: `skills/maintain/agent-campaign-orchestration/SKILL.md`
- Git 仓库管理: `skills/maintain/git-repository-management/SKILL.md`
- 本机整合包更新: `skills/maintain/integration-package-update/SKILL.md`，只在用户明确授权更新/同步/执行整合包时使用，不导出到整合包。

**权限边界**：

- 角色 C 可以调整和测试角色 A/B/D 的框架行为，但不得降低导入质量门槛。
- 角色 C 可以维护 `.gitignore`、本地仓库初始化、提交规范、回滚检查和仓库健康。
- Git 管理仍是角色 C 的子技能；角色 D 是独立的物理学习与题库治理角色。
- 修改 `SCHEMA.md`、核心 skill 工作流或 `rag_pipeline.py` 需要用户明确授予管理员权限；当前任务若用户明确指定“你是角色 C 管理员”，可视为本次授权。

**多角色 batch v3 不变量**：

- 通用控制面只公开 `init → dispatch → submit → recover → status`，结构只有 `campaign_state v3`、`batch_card v3`、`batch_result v3` 和 `apply_receipt v3`。完整题干、OCR、图片和推导留在 batch artifact，控制面只保存引用与 SHA-256。
- 主 agent 只代理用户意图、调度和统计。A+D executor 每批最多 5 项，只生成暂存 proposal/evidence/dry-run；全新 C+D auditor 逐项只读复核；通过项才由 profile 指定的 canonical writer 写回。失败项只允许一次全新 repair 和一次全新复审，第二次失败即 `blocked`。
- 主 agent 必须先确认空闲 isolated-worker 槽位。平台有查询接口时先查询再 dispatch/spawn；无查询接口时先以 `fork_context=false` spawn 本批实际 worker 待命，再 dispatch 并发送任务卡，不得创建 disposable capacity probe。spawn 返回的真实 agent ID 必须写入 `producer.worker_instance_id`；executor、auditor、repair 与重试不得复用 worker。worker 只写预登记 result，主 agent 调用 `submit` 作为唯一确定性验收门禁并读取 `submit.items`，不得用临时脚本重做内部 state/artifact 解析。主 agent 不得代做 proposal 或 audit；无 isolated worker 时 fail closed。
- 同一 campaign 只有一个 active run，最多并行四个不同 campaign。每次 dispatch 生成唯一 `run_id` 和 hash-bound batch card；worker 结束后销毁，旧 run 的迟到结果拒绝。运行故障只允许换新 worker 重试一次。
- `dispatch` 必须按相同 status、source SHA-256 与 capability 稳定组批。queued/revalidation 题缺少已审计 slice 时必须优先进入 A-only source-alignment wave；source card 按 `source_slice_v3.schema.json` 为每题预建唯一 manifest 路径和隔离 artifact root，locator 必须声明 `type` 与明确的坐标空间。source result 同时绑定 source ref、题目级 slice 和 evidence，经全新 auditor 通过后才进入内容队列；source 重试的新 candidate 被接受时必须清空上一轮 audit/defects。source 阶段同源最多5项且不受 complex canary 单题限制。batch card 必须预建 result 骨架并给出合法 `producer_role`；内容阶段只加载 Role D，repair 复用 slice。`complex/critical` 内容 canary 每批只派1项。
- worker 无法完成时也必须逐项提交 `runtime_failed/source_blocked/unresolved`，不能只留下转换缓存。`runtime_attempts_exhausted` 只有在补入绑定 question ID、source SHA-256、定位信息与 slice artifact 哈希的 source slice 后，才能通过原 `recover` 接口受控重新排队；内容审计失败不得走该入口。
- 不使用 source/target 生命周期锁、lease、worker assignment/attestation、registration token、cutover/adoption 或调度层 rollback。状态文件只在读改写瞬间使用 OS 自动释放的短时互斥；writer 只在落盘瞬间比较当前哈希并执行原子文件事务。
- 教师授权必须绑定授权记录与 ledger SHA-256、允许字段、canonical writer、期限和 `destructive=false`。主 agent 只能传递用户授权。grant 过期后只读检查仍可进行，任何新写入都必须取得新授权并重新初始化或按业务 profile 明确续接。
- canonical writer 必须回查 active auditor batch、batch-card SHA-256、逐项 `passed` verdict、proposal/dry-run 和 target/source/media hash。写后确定性验证失败时 writer 在本次调用内恢复备份；成功写入必须生成 before/after hash、备份和验证结果齐全的 `apply_receipt v3`。
- `run_id`、phase 和 batch-card SHA-256 只证明批次身份，不证明审计独立。writer 与完成报告必须核对 executor/auditor 均为 `isolated_worker` 且 `producer.worker_instance_id` 不同；缺失或碰撞时拒绝写回与完成声明。
- `recover` 只处理三种可证明状态：有效 result 可继续 submit；apply receipt 且当前目标等于 post-hash可补记完成；已确认 worker 结束且无结果可登记运行失败并重派。任何无法由状态与 artifact 证明的情况 fail closed。
- 旧流程留下的 `passed/no_change_passed` 若 producer identity 不可信，可在无 active run 时通过 `recover --revalidate-passed` 受控转为 `revalidation_queued`。必须保存旧 candidate/audit/receipt 到 `revalidation_history`；不得手改状态，也不得把重验证队列直接视为内容错误。
- `source_blocked` 只能进入 A-only source-resolution batch，新映射经全新 auditor 通过后才回到 executor 队列。Wiki、图谱和 RAG 后置流程属于业务 skill，不进入调度器。
- Codex 静态 capability 映射为 `bounded=Luna/xhigh`、`ambiguous=Sol/medium`、`complex=Sol/high`、`critical=Sol/xhigh`；不运行子 agent 能力上限测试或实际 runtime 能力证明。平台无法提供隔离的一次性 worker 时，不得让主线程模拟长任务多 agent。

### 角色 D：物理学习 Agent

**职责**：面向学生识别并分层讲解物理题、生成或批注物理图；在教师授权后维护题库题干/答案/详解/题图、稳定知识点标签和受控高中物理知识关系图谱。

**模式**：

- `student` 为默认只读模式，只能写 `output/learning_sessions/<session_id>/`。
- `curator` 为教师授权模式，所有写入先 proposal + dry-run；普通 apply 需要 `--teacher-authorized`，破坏性操作还需要 `--allow-destructive`。显式 strong curator 可更新单题的题干、答案、详解、`assets`，并将哈希绑定的新增或修复题图/解析图写入该题 `media/`。覆盖图片必须同时校验目标图当前 hash，并在 ignored runtime 备份原图。
- batch v3 的 A+D executor 可按阶段使用 Role A 证据定位和 Role D strong curator，但只能提交暂存稿；C+D auditor 完全只读，不能调用 writer。

**模型档**：

- `weak` 为跨 agent 默认档；`strong` 必须显式选择，Codex Role D 默认提示使用 strong。
- strong 增加脱敏图视觉复核、按需深度检索、多方法推导、完整题目记录校对、AI 自主标签、二次自检和渲染后语义复核；strong curator 的绘图流程可把复核通过的图受控写回题库。它不改变 student/curator 模式，也不绕过 hash、dry-run、教师授权或破坏性门禁。
- OCR、视觉复核、用户确认均绑定源文件和当前结构化结果 hash；旧确认不得复用于已变化的题目。

**常见 primary skills**：

- 拍题与分层辅导: `skills/learn/physics-question-tutoring/SKILL.md`
- 受力图/坐标图/原图批注/题库图写回: `skills/learn/physics-diagram-toolkit/SKILL.md`
- 题库题干/答案/详解/题图维护: `skills/learn/exercise-solution-curation/SKILL.md`
- 高中物理知识关系图谱: `skills/learn/physics-knowledge-graph/SKILL.md`
- 稳定知识点标签: `skills/taxonomy/exercise-knowledge-tags/SKILL.md`

**边界**：OCR、公式识别和绘图由本地工具链提供结构化结果；strong 仅在本地 OCR 后查看去 EXIF 会话副本。关键项不确定时必须停下确认。角色 D 不读取或修改真实 StudentDataSQL，不自由重写 Wiki 整页，只修改 managed pages/blocks。

题目正式标准标签由 `exercise-knowledge-tags` 独占管理：`knowledge_points`、`knowledge_point_ids`、稳定 `kp_id` registry 的新增、分配、改名、移动、合并和停用都必须走该 skill。`ai_extra_tags` 是不进入 registry 的 AI 自主自由标签，strong curator 可在题目校对、绘图写回或 taxonomy 分配时维护。

## Skills 路由与隔离

所有 agent 必须遵守 registry-router 模式：

1. 先读 `skills/registry.yaml`。
2. 优先运行 `python skills/_shared/scripts/skill_retriever.py --json "任务描述"`。
3. 只加载 `load_now[0]` 指向的单个 primary `SKILL.md`。
4. `queued` skills 只作为后续阶段候选，不得提前加载。
5. `manifest.yaml` 仅在需要脚本或 runtime 路径时读取。
6. `references/`、scripts、runtime 细节只在具体分支需要时读取。

隔离规则：

- skill 检索只读 `skills/registry.yaml`、各 skill 的 `card.md` 和必要 frontmatter。
- 全文 `SKILL.md` 只在 primary 确定后加载。
- skill 检索缓存只能写入 `skills/_ops/runtime/state/skill-retrieval/`。
- 不得把 `skills/`、`SKILL.md`、skill card 或 skill cache 并入教学 RAG。
- 教学 RAG 仍只面向 `raw/` 清洗后的教学内容。

## 权限控制规则

| 级别 | 触发 | 可操作范围 |
| --- | --- | --- |
| **标准**（默认） | 用户未授权 | 严格按 skill 工作流执行，不跳步、不另写脚本 |
| **单次允许** | 用户对某次操作明确允许 | 允许该次跳出工作流，仅限当次 |
| **管理员** | 用户明确授予管理员权限或指定角色 C 管理员执行框架维护 | 允许修改 skill/SCHEMA/router/入口/Git 策略；修改 `rag_pipeline.py` 仍需具体说明 |

角色 D 的 `student`/`curator` 是业务写入模式，不替代上述仓库权限。即使 agent 具备管理员能力，也不能替用户补出 `--teacher-authorized` 或 `--allow-destructive`。

### 区域分类

| 区域 | 权限 | 包含内容 |
| --- | --- | --- |
| **锁定区** | 仅管理员可修改 | `SCHEMA.md`、核心 skill 工作流步骤、`BGE-M3/scripts/rag_pipeline.py` 核心逻辑、Question MD Standard、检索模式定义、上下文预算、权限控制定义 |
| **框架区** | 角色 C 可维护 | `AGENTS.md`、agent shim、`skills/registry.yaml`、router、validation、routing fixtures、Git policy |
| **自由区** | 标准权限可操作 | `raw/` 文件导入（按 skill 走）、Wiki 页面创建/更新、RAG 重建（运行现有脚本）、`source-library/` 原件存档 |
| **自适应区** | 标准权限可更新 | 智能模式检索经验、问题分类到检索模式映射、用户检索偏好 |

### 关键规则

1. 标准模式下不得另写临时脚本绕过 skill。
2. 标准模式下不得修改工作流内容。
3. 单次允许用完即止，不延伸到后续操作。
4. 权限不可自授，必须来自用户授权或入口契约。
5. 角色 C 维护框架时，应运行验证脚本并报告结果。

## 四库架构

### 四库相对独立原则

- `raw/` 保存清洗后的 MD 教学源稿，是导入和 BGE-M3 切片来源；二进制原件只进入 `source-library/`，回答阶段不直接回 `raw/` 读取全文。
- `LLMWiki/` 保存自包含 Wiki 页面，把 raw 中整理好的内容导入 Wiki 或二次加工后存储，使用 `[[wikilinks]]` 导航。Wiki 正文不得只有 raw transclusion、raw 原文区或 raw 查看链接。
- `BGE-M3/` 从 raw 中整理好的 MD 预切片建索引，回答时语义召回 chunks；BGE 不重复返回 Wiki 页面本身。
- `StudentDataSQL/` 保存结构化数据 schema、迁移和安全查询框架；真实学生数据只进入 Git 忽略的 `runtime/`，不进入前三库或教学向量索引。
- frontmatter `sources` 可保留 raw 路径作为溯源元数据，但不作为回答阶段读取入口。

```text
LLMWiki_BGE-M3/
├── README.md
├── CHANGELOG.md
├── PROJECT_LAYOUT.yaml     # 机器可读路径权威源
├── source-library/         # 二进制原件库，仅跟踪目录契约
├── raw/                    # 清洗后的 MD：教材、标准、题库、讲义、视频转写
│   ├── textbooks/
│   ├── standards/
│   ├── exercises/
│   ├── lectures/
│   └── transcripts/
├── LLMWiki/                # 自包含 Wiki 页面，Obsidian 浏览和 wikilinks 导航
│   ├── concepts/
│   ├── index.md
│   └── log.md
├── BGE-M3/                 # 教学 RAG 子系统
│   ├── config/
│   ├── scripts/
│   ├── tests/
│   └── runtime/{env,models,data,index,logs}/
├── StudentDataSQL/         # 学生成绩、作业、掌握度分析子系统；真实数据不进 Git/RAG
│   ├── migrations/
│   ├── scripts/
│   ├── templates/
│   ├── tests/fixtures_synthetic/
│   └── runtime/{db,imports,exports,backups,logs,exam-reports}/
├── skills/                 # 权威 skills 库，canonical skills
│   ├── registry.yaml
│   ├── import/
│   ├── retrieve/
│   ├── maintain/
│   ├── analyze/
│   ├── learn/
│   ├── taxonomy/
│   ├── roles/
│   ├── _shared/model-tools/
│   └── _ops/
├── output/                 # 正式交付物与 QA 输出，Git 忽略
├── tmp/                    # 唯一临时工作区，按 tasks/logs/state/renders/downloads/staging 分类
├── .codex/helpers/         # Codex on Windows 专用辅助脚本
├── AGENTS.md               # agent 唯一权威入口契约
└── SCHEMA.md               # 本文件，知识库结构与治理规范
```

`tmp/` 不设长期 `_archive`。临时材料默认保留 30 天；清理器必须先只读 dry-run，只有用户显式授权后才能删除。

**三库同步规则**：每次教学 `raw/` 完成导入后，必须同步自包含 `LLMWiki/`，再完成 Role D 图谱 handoff，最后同步 `BGE-M3/`。`bemarkdown` 只是转换前置，`student-data-import` 属于独立学生数据子系统，二者不触发图谱更新。

## StudentDataSQL 学生数据分析子系统

`StudentDataSQL/` 是独立第四子系统，负责学生成绩、日常作业、课堂观察、知识点掌握度和教学干预分析。它不改变教学知识三库的入库与检索规则。

- 真实学生数据不得进入 `raw/`、`LLMWiki/`、`BGE-M3/`、`skills/`、Git 或向量索引。
- 与教学知识库只通过 `kp_id`、`question_id`、`wiki_path` 发生联动。
- `kp_id` 来自 Role D 稳定知识点 registry；改名、移动或布局变化不改 ID，停用通过 replacement IDs 迁移。
- 默认 agent 只能使用 `StudentDataSQL/scripts/query_student_data.py` 和 `export_safe_report.py` 这类参数化脚本。
- 默认安全视图不得暴露 `student_identities`、真实姓名、学号、家长联系方式、公开排名或原始教师备注。
- 真实数据导入必须使用加密真实模式；未配置 `STUDENT_DATA_DB_KEY` 和 SQLCipher 时必须失败关闭。
- schema、migration、脚本、模板、合成测试数据和说明文档可进 Git；`runtime/{db,imports,exports,backups,logs,exam-reports}/` 不进 Git。

## 题库分类体系

机器权威标签表位于 `skills/taxonomy/exercise-knowledge-tags/references/knowledge-points.yaml`。每个节点固定包含 `kp_id/title/parent_id/level/sort_order/path/aliases/status/replacement_ids/wiki_path`。`kp_id` 使用不可复用顺序号；题目渐进增加 `knowledge_point_ids`，同时保留中文 `knowledge_points` 供当前 BGE-M3 parser 使用。所谓删除一律实现为 `deprecated` 并指定 replacement。

```text
raw/exercises/
├── 力学/
│   ├── 运动学/
│   ├── 力与平衡/
│   ├── 牛顿运动定律/
│   ├── 曲线运动/
│   ├── 万有引力与航天/
│   ├── 功和能/
│   └── 动量/
├── 电磁学/
│   ├── 电场/
│   ├── 磁场/
│   ├── 电磁感应/
│   ├── 交变电流/
│   └── 电路/
├── 光学/
│   ├── 光学/
│   └── 机械振动与波/
├── 热学/
│   └── 热学/
└── 综合/
    ├── 综合/
    └── 2025高考真题/
```

## 导入工作流总览

- 教材导入：`raw/textbooks/` → Wiki（书 → 章 → 节三级拆分）→ Role D 图谱 handoff → RAG。
- 标准导入：`raw/standards/` → Wiki（文档总结 + H2 章总结）→ Role D 图谱 handoff → RAG。
- 题库导入：原件存档 → BeMarkdown 转换 → LLM 逐题配对 → 生成题目 MD → 质量检查 loop → Role D 解析风险/稳定标签 → Wiki 枢纽页 → Role D 图谱 handoff → RAG 重建。
- 讲义处理：讲义完整 MD 保留到 `raw/lectures/`；题目提取到题库后，原位置保留题号索引或指针。
- 视频导入：候选发现/下载 → faster-whisper 转写 → LLM 抽帧 → VLM 验证 → 三层文件 → Wiki → Role D 图谱 handoff → RAG。

Role A handoff 固定记录来源 skill、changed raw/Wiki files、question ids、kp ids、质量门禁和文件 hash。没有 `no_change`、`applied_safe`、`applied_destructive` 或 `approval_required` 的 completion-ready 图谱结果时，不得声明教学导入完成。

质量检查三层：

1. 格式：frontmatter 完整、公式闭合、图片断链、WMF 引用处理。
2. 内容：题干、答案、详解完整，无占位符和 OCR/VLM 残留。
3. 对应关系：题号一致、答案字母一致、解析与答案一致。

视频三层文件：

- 逐字稿：原始转写 + ASR 修正，不进 RAG。
- 知识笔记：去口语化知识点 + LaTeX，进 RAG 切片。
- 教学简案：教学流程 + 板书/关键帧引用，供 Wiki 和教学调用。

## RAG 重建规则

在 `BGE-M3/` 目录运行安全命令：

```bash
env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

不得在未加载对应 skill 的情况下改写 dense/sparse/colbert 配置，不得把 `skills/` 内容加入教学 RAG。

## Git 策略

Git 仓库由角色 C 通过 `skills/maintain/git-repository-management/SKILL.md` 管理。

- v1 采用“框架 + Markdown”跟踪策略。
- 跟踪入口规则、skills 轻量文件、`LLMWiki/**/*.md`、`raw/**/*.md`、`StudentDataSQL/` 的 schema/scripts/templates/synthetic fixtures 和轻量配置。
- 忽略模型、venv、各库 runtime、缓存、`source-library/` 二进制、media 图片、`output/`、`tmp/`、StudentDataSQL 真实数据库、导入原件、导出报告、备份和日志。
- 不使用 Git LFS 纳入大文件。
- 本地仓库策略由 `.gitignore` 执行，变更后必须用 `git status`、`git ls-files`、`git check-ignore` 核验。

## 日常维护

`kb_maintenance_check.py` 用于日常维护检查：

- 公式闭合、答案完整性、占位符、详解过短。
- 答案与“故选”不一致、图片断链、WMF 引用。
- ID 连续性、Wiki 死链、RAG 完整性。

## 约定

- `raw/` 中的原始资料永久保留，不随意删除。
- 原件（PDF/DOCX/MP4）永久保存在 `source-library/` 目录。
- 文件名优先使用小写、连字符、无空格。
- 每个 Wiki 页面都应以 YAML frontmatter 开头。
- 页面之间使用 `[[wikilinks]]`。
- 中文为主，必要英文技术名词保留。
- 讲义完整 MD 保留到 `raw/lectures/`；Wiki 中讲义设计/讲义内容需自包含，题目索引写题号并由 BGE 召回题目 chunk。
