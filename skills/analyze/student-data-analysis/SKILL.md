---
name: student-data-analysis
description: 安全只读查询 StudentDataSQL，用于学生成绩分析、作业分析、班级薄弱知识点、匿名化学生画像、单生考试知识点掌握报告、真实数据密钥保护查询和脱敏教学报告。用于 SQL学生数据分析、学生成绩分析、作业分析、班级薄弱点、学生掌握度、期中考掌握情况、考试小题分析、失分分析、答题卡分析；不要用于名册、成绩单、小题分或答题卡 manifest 导入。
version: 1.0.0
author: LLMWiki Agent
license: MIT
platforms: [windows]
metadata:
  role: role-b-retrieval-calling
  tags: [student-data, sql, privacy, homework, score-analysis]
---

# Student Data Analysis — 学生数据安全分析

本 Role B skill 只用于从 `StudentDataSQL/` 做安全只读查询和脱敏报告。

## 安全规则

1. 只使用 `StudentDataSQL/scripts/` 下已批准的查询和导出脚本。
2. 不预处理、不导入、不更新、不修复名册、成绩单、小题分 CSV、答题卡 manifest、schema、migration 或 router 规则。
3. 不对原始表执行自由 SQL。
4. 不读取或暴露 `student_identities`、真实姓名、学号、考号、学籍号、家长联系方式、公开排名、原始教师评语、备份或原始导入文件。
5. 不把学生数据写入 `raw/`、`LLMWiki/`、`BGE-M3/`、`skills/`、Git 或向量索引。
6. 任何 `--real-data` 查询或导出前，必须确认当前命令环境存在 `STUDENT_DATA_DB_KEY`。如果缺失，停止并请教师设置；不要退回 `--dev`，不要生成密钥，也不要把密钥写入文件、日志或记忆。
7. 优先输出班级聚合和匿名画像。单生考试报告只能使用匿名化的 `student_id`、`pseudonym` 和 `seat_no`；真实身份查询不属于 v1 默认 skill。
8. seat-only 考试分析只使用安全字段，例如 `student_id`、`pseudonym`、`class_id`、`seat_no`、`assessment_id`、`question_id`、`kp_id`、`kp_title`、`key_point`、`difficulty_point` 和可选 `wiki_path`。不得反推姓名。
9. `StudentDataSQL/runtime/exam-reports/` 中的试卷层报告可作为成绩分析上下文读取。它们必须只描述试卷，不得包含学生身份、排名、原始答题卡文件名或个人作答记录。

## 安全命令

班级薄弱知识点：

```bash
python StudentDataSQL/scripts/query_student_data.py --view class_knowledge_summary --class-id <class_id> --json
```

近期作业概览：

```bash
python StudentDataSQL/scripts/query_student_data.py --view homework_recent --class-id <class_id> --json
```

匿名化学生画像：

```bash
python StudentDataSQL/scripts/query_student_data.py --view student_profile --student-id <student_id> --json
```

安全周报：

```bash
python StudentDataSQL/scripts/export_safe_report.py --class-id <class_id> --type weekly --out StudentDataSQL/runtime/exports/<name>.md
```

考试小题分析：

```bash
python StudentDataSQL/scripts/query_student_data.py --view exam_item_analysis --class-id <class_id> --json
```

单生考试知识点掌握报告：

```bash
python StudentDataSQL/scripts/export_exam_student_mastery.py --class-id <class_id> --assessment-id <assessment_id> --pick first --out output/<name>.md --officecli-qa auto --json
```

如果请求按座位号分析，使用 `--seat-no <seat_no>`，不要查真实身份。

单生掌握报告会把教师可读产物写到仓库 `output/`：Markdown、Word `.docx`、PDF、掌握率图表 PNG，以及可选 `output/qa_<name>/` OfficeCLI QA 产物。草稿用 `--officecli-qa auto`，正式交付用 `--officecli-qa required`，后者要求 OfficeCLI 可用且视觉/OpenXML QA 通过。

真实学生数据必须使用 `--real-data` 和 `StudentDataSQL/runtime/db/` 下的真实库。`STUDENT_DATA_DB_KEY` 只从当前命令环境读取；不要把真实 seat-only 考试数据导入 dev 数据库。

## 与教学知识库联动

SQL 输出含 `kp_id` 或 `wiki_path` 时，只把这些知识点标识交给现有检索 skill 去查教学资料。不要把学生身份、原始学生行或答题细节发送给 BGE-M3。

## 路由切出

- 如果任务要求导入或预处理名册、成绩单、小题分 CSV、答题卡 manifest，切换到 `skills/import/student-data-import/SKILL.md`。
- 如果任务要求修改 schema、migration、脚本、router、Git 策略、隐私治理或 `exam_reports/` 框架规则，切换到 `skills/maintain/kb-framework-admin/SKILL.md`。
