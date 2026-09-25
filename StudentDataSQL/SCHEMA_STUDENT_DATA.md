# StudentDataSQL Schema 与治理规则

## 在知识库中的角色

`StudentDataSQL/` 是独立第四子系统，负责结构化学生学习数据。它不改变现有 `raw/` → `LLMWiki/` → `BGE-M3` 教学知识流，也不得把学生数据送入教学 RAG。

## 隐私等级

- L0：schema、migrations、scripts、templates、合成 fixtures。
- L1：班级级聚合统计和匿名统计。
- L2：伪匿名单生画像。
- L3：脱敏前的原始导入行，以及任何导入阶段出现的禁存身份字段。
- L4：加密备份、审计日志、原始导入文件和答题卡证据。

默认 agent 访问范围仅限 L1 和经过选择的 L2。L3/L4 属于本地教师或管理员数据，不得暴露给普通检索/备课工作流。

## 核心表

- 身份与组织：`students`、`classes`、`class_memberships`、`terms`。`student_identities` 是 legacy/local-only 表，名册导入不得写入。
- 知识联动：`knowledge_points`、`questions`、`question_knowledge_points`。
- 考试：`assessments`、`assessment_items`、`student_assessment_results`、`student_item_results`。
- 考试证据：`answer_card_artifacts` 保存班级/座位号引用、证据状态、路径/证据引用和内容 hash。原始文件名映射只能作为本地 StudentDataSQL 分析证据，不得进入安全视图或公开输出。
- 加密源文件保留：`encrypted_file_artifacts` 在结构化导入后，把真实 CSV/JSON 和答题卡文件体保存进 SQLCipher。持久未加密真实导入文件不得留在 `imports/`。
- 作业：`assignments`、`assignment_items`、`homework_submissions`、`homework_item_results`、`homework_corrections`。
- 过程记录：`daily_observations`、`learning_events`。
- 分析与治理：`analysis_runs`、`student_knowledge_mastery`、`class_knowledge_summary`、`teacher_review_flags`、`import_batches`、`access_audit_log`。

## 安全视图

- `v_class_knowledge_summary_safe`
- `v_homework_recent_safe`
- `v_student_profile_pseudonymized`
- `v_exam_item_analysis_safe`，包含每题 `key_point`、`difficulty_point` 和 `exam_report_ref`

安全视图不得 join `student_identities`。

## 必须遵守的运行规则

- 所有导入、查询、导出必须写入 `access_audit_log`。
- 真实数据导入必须使用加密真实库模式。
- 每一次真实数据导入、查询或导出都必须在当前命令环境中存在 `STUDENT_DATA_DB_KEY`。
- 如果密钥不存在，agent 必须停止并让教师设置；不得回退到 `--dev`，不得发明替代密钥，不得把密钥写入文件。
- 真实数据预处理也必须要求 `STUDENT_DATA_DB_KEY`；没有密钥时，不得创建持久真实 CSV/JSON 或答题卡 staging 图片。
- Seat-only 导入可以在进程内临时用姓名匹配成绩行和答题卡文件，但不得落盘。
- 姓名、学号、考号、学籍号、监护人或联系方式不得写入 normalized CSV、数据库、日志、Git、`raw/`、`LLMWiki/` 或 `BGE-M3`。
- 成绩单预处理必须用 `class_id + seat_no` 对齐 canonical roster，并复制 roster 中已有的 `student_id`；不得根据成绩单生成新学生 ID。
- 学校考试成绩导入应在来源提供时保留 `class_rank`、`grade_rank`、`score_band`，它们只属于 StudentDataSQL 本地分析数据。
- 成绩结果只导入有效数字总分的学生；缺考或缺失行不进入 `student_assessment_results`。
- 排名快照、原始答题卡文件名映射、个人作答记录可在诊断需要时作为本地 StudentDataSQL 分析证据保存；它们必须留在 Git、`raw/`、`LLMWiki/`、`BGE-M3`、`skills/`、安全视图和公开/课堂输出之外。
- 报告默认必须脱敏。
- 公开或课堂输出不得暴露分数、排名、姓名或个人行为细节。
- BGE-M3 不得索引 StudentDataSQL 数据。

## exam_reports 目录

- `exam_reports/` 是本地保留的考试报告库，保存预处理好的试卷分析 Markdown 和 SQL 侧成绩分析附件。
- 标准 `_试卷分析报告.md` 应描述试卷本身。必要时可在同目录保存排名快照、原始答题卡文件名映射或个人作答记录等本地附件。
- 文件夹和文件名必须能识别具体考试。
- 除非教师明确要求本地调查材料，否则不得在 `exam_reports/` 保存完整姓名、学号、考号、学籍号、监护人或联系方式。
- `exam_reports/` 不是 BGE-M3 来源；StudentDataSQL 数据不得进入教学 RAG。

## Seat-Only 考试 ID 规则

- 名册保留字段：生成的 `student_id`、`family_name`、`class_id`/`class_name`、`seat_no`、`term_id`、`gender`。
- 名册禁存字段：完整姓名、学号、考号、学籍号、监护人联系方式。
- 学生 ID：`<term_id>_<class_id>_<gender>_seat<seat_no>`，例如 `2025-2026-s2_g2c01_F_seat01`。
- `student_code`：仅为 legacy 数据库列。外部 CSV 模板使用 `student_id`；内部写入该列时必须等于 `student_id`。
- 成绩单 ID 匹配：只用成绩单中的班级与座位号查找已有 roster 行，然后复制该行 `student_id`。
- 考试 ID：`<class_id>_<exam_base>`，例如 `g2c01_physics_2026_midterm`。
- 排名字段：`class_rank` 为班级排名，`grade_rank` 为年级或分层排名，`score_band` 为来源成绩档次，例如 `A` 或 `B+`。
- 题目 ID：`<exam_base>_q<item_no>`，例如 `physics_2026_midterm_q01` 或 `physics_2026_midterm_q13_3_1`。
- 题目知识点：`Q*_kp_id` 是该题主知识点 ID。
- 题目重点：`Q*_key_point` 是从匹配 `exam_reports/` 报告复制的每题重点。
- 题目难点：`Q*_difficulty_point` 是从匹配 `exam_reports/` 报告复制的每题难点。
- 题目 Wiki 路径：`Q*_wiki_path` 是可选知识页上下文，不是试卷报告路径。试卷报告固定存放在 `StudentDataSQL/runtime/exam-reports/`。
