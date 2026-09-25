# StudentDataSQL 学生数据分析子系统

`StudentDataSQL/` 是独立的学生数据分析子系统，用于保存和分析学生成绩、作业记录、日常观察、知识点掌握度和教师复核标记。它只通过 `kp_id`、`question_id`、`assessment_item_id`、可选 `wiki_path` 与教学知识库联动，不改变 `raw/` → `LLMWiki/` → `BGE-M3/` 的教学知识流。

## 边界

- 真实学生数据不得进入 `raw/`、`LLMWiki/`、`BGE-M3/`、`skills/`、Git 或任何向量索引。
- 试卷报告正文属于 `StudentDataSQL/runtime/exam-reports/`，用于 SQL 侧成绩分析上下文；它不是教学 RAG 来源。
- 默认 agent 只能通过参数化脚本和安全视图访问数据，不得自由执行 SQL 查询原始表。
- 真实数据模式默认 fail closed：没有 `STUDENT_DATA_DB_KEY` 和加密支持时，必须拒绝真实数据导入、查询、导出和预处理。

## 真实库密钥

`STUDENT_DATA_DB_KEY` 是教师持有的真实学生数据库加密密钥。Agent 可以使用教师在当前会话提供的密钥，但不得把密钥写入仓库文件、Markdown 报告、日志、Git、向量索引或长期记忆。

执行任何 `--real-data` 导入、查询、导出或真实数据预处理前，必须确认当前命令环境存在该变量。缺失时必须停下让教师设置；不得改用 `--dev`，不得自己生成长期密钥，也不得把真实 normalized CSV/JSON 或答题卡图片留在未加密的持久目录中。

PowerShell 示例：

```powershell
$env:STUDENT_DATA_DB_KEY="<teacher-provided-key>"
python StudentDataSQL/scripts/query_student_data.py --real-data --view student_profile --student-id <student_id> --json
```

## 开发库快速验证

开发库只允许使用合成数据：

```powershell
python StudentDataSQL/scripts/init_db.py --dev --reset
python StudentDataSQL/scripts/import_synthetic_demo.py
python StudentDataSQL/scripts/query_student_data.py --view class_knowledge_summary --class-id demo_class_001 --json
python StudentDataSQL/scripts/export_safe_report.py --class-id demo_class_001 --type weekly --out StudentDataSQL/runtime/exports/demo_weekly.md
```

## Seat-Only 考试导入

两班两场考试或学校 `.xls` 成绩单导入时，先运行预处理脚本。脚本通过 Excel COM 读取旧版 `.xls`，只向 ignored runtime 目录写入 seat-only CSV。

关键规则：

- 名册标准化必须先于成绩单标准化。
- 成绩单只能用 `class_id + seat_no` 查找 canonical roster；不得用成绩单反向生成新 `student_id`。
- normalized roster CSV 不得包含 `姓名`、`学号`、`考号`、`学籍号` 或真实姓名。
- `student_id` 只由 `term_id + class_id + gender + seat_no` 生成，例如 `2025-2026-s2_g2c01_F_seat01`。
- `students.student_code` 是兼容旧 schema 的内部列；外部 CSV 模板使用 `student_id`，内部写入时 `student_code` 必须等于 `student_id`。

示例流程：

```powershell
python StudentDataSQL/scripts/preprocess_school_exam_xls.py --class-code g2c01 --exam midterm --copy-answer-cards --json
python StudentDataSQL/scripts/init_db.py --dev --reset
python StudentDataSQL/scripts/import_roster.py --dev --csv StudentDataSQL/runtime/imports/normalized/g2c01_seat_only_roster.csv --json
python StudentDataSQL/scripts/import_exam_csv.py --dev --csv StudentDataSQL/runtime/imports/normalized/g2c01_physics_2026_midterm_scores.csv --json
python StudentDataSQL/scripts/import_answer_card_manifest.py --dev --csv StudentDataSQL/runtime/imports/normalized/g2c01_physics_2026_midterm_answer_card_manifest.csv --json
```

如果缺少 `StudentDataSQL/runtime/imports/normalized/<class_id>_<term_id>_roster.csv`，先运行 `preprocess_roster_xlsx.py`，并用 `--roster-csv` 显式传给成绩单预处理脚本。成绩单预处理不得回退到成绩单里的不完整名册字段。

真实数据成功导入结构化表后，应把源文件、normalized CSV/JSON、脱敏答题卡 staging 等归档进加密数据库，并删除未加密副本：

```powershell
python StudentDataSQL/scripts/import_file_artifacts.py --real-data --root StudentDataSQL/runtime/imports --delete-after --json
```

默认真实数据库路径是 `StudentDataSQL/runtime/db/student_data.sqlite3`。班级或考试专用加密数据库只作为历史快照，不作为默认工作库。

## 考试小题与试卷报告

学校成绩单提供排名字段时，导入脚本应保留这些 StudentDataSQL 本地分析字段：

- `class_rank`：班级排名。
- `grade_rank`：年级或分层排名。
- `score_band`：来源成绩档次，例如 `A`、`B+`。

成绩 CSV 只导入总分有效的学生；缺考或源表缺失的学生不进入 `student_assessment_results`。

题目映射字段：

- `Q*_kp_id`、`Q*_kp_title`：题目主知识点。
- `Q*_key_point`、`Q*_difficulty_point`：从匹配的 `exam_reports/` 试卷分析报告复制的每题重点和难点。
- `Q*_wiki_path`：可选知识点 Wiki 页面路径，不是试卷报告路径。
- `item_map_review.csv`：给人工或 agent 复核知识点、题目重点、难点、答案、评分点和方法标签。

## 名册导入

名册只保留足以生成学生匿名标识的字段：

- 生成的 `student_id`
- `family_name`
- `class_id` / `class_name`
- `seat_no`
- `term_id`
- `gender`，取值 `M`、`F`、`U`

示例：

```powershell
python StudentDataSQL/scripts/preprocess_roster_xlsx.py --desktop-token 10 --term-id 2025-2026-s2 --class-id g2c02 --class-name 高二02班 --json
python StudentDataSQL/scripts/import_roster.py --real-data --csv StudentDataSQL/runtime/imports/normalized/g2c02_2025-2026-s2_roster.csv --json
```

## exam_reports 目录

`exam_reports/` 是本地保留的考试报告库，存放预处理好的试卷分析 Markdown 和 SQL 侧成绩分析需要的本地附件。标准文件名应以 `_试卷分析报告.md` 结尾，并能明确识别考试：

```text
StudentDataSQL/runtime/exam-reports/厦门第六中学_2025-2026学年第二学期高二物理半期考试/
  厦门第六中学_2025-2026学年第二学期高二物理半期考试_试卷分析报告.md
```

如成绩诊断需要，可在同一考试目录保留本地分析附件，例如排名快照、原始答题卡文件名映射、个人作答记录。除非教师明确要求制作本地调查材料，否则不要保存完整姓名、学号、考号、学籍号、监护人或联系方式。

`exam_reports/` 不得复制到教学 RAG、`raw/`、`LLMWiki/`、`skills/`、Git 或公开输出。

## Git 跟踪与忽略

跟踪：

- schema、migrations、scripts、templates、文档、合成 fixtures。

本地保留但 Git 忽略：

- `db/`
- `imports/`
- `exports/`
- `backups/`
- `logs/`
- `exam_reports/`
- 真实 SQLite/SQLCipher 数据库、WAL/SHM 文件、normalized CSV、答题卡副本、生成报告。

## 安全 agent 接口

Agent 应调用：

- `scripts/query_student_data.py`
- `scripts/export_safe_report.py`
- 经过参数化约束的导入脚本

Agent 不得对原始表执行自由 SQL。安全视图不得暴露 `student_identities`、真实姓名、学号、考号、监护人联系方式、班级/年级排名、成绩档次或原始教师备注。

## 目录说明

- `backups/`：本地备份产物，Git 忽略。
- `db/`：SQLite/SQLCipher 数据库及 WAL/SHM，Git 忽略。
- `exam_reports/`：本地考试报告和 SQL 侧分析附件，Git 忽略，不是教学 RAG 来源。
- `exports/`：教师查看的脱敏报告，Git 忽略。
- `imports/`：临时 normalized CSV 和脱敏答题卡副本，真实数据导入后应归档进加密库并清理。
- `logs/`：运行和审计日志，Git 忽略。
- `migrations/`：跟踪的 SQL schema migration。
- `scripts/`：跟踪的安全导入、查询、导出、预处理脚本。
- `templates/`：跟踪的 CSV 模板。
- `tests/`：跟踪的合成数据 fixtures。
