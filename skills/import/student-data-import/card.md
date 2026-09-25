# student-data-import card

- status: active
- category: import
- role: role-a-import-maintenance
- canonical_skill: `import/student-data-import/SKILL.md`
- manifest: `import/student-data-import/manifest.yaml`
- description: Import StudentDataSQL rosters, school score sheets, item-score CSVs, and de-identified answer-card manifests through approved root scripts with privacy gates.
- triggers: StudentDataSQL导入, 学生数据导入, 名册导入, 成绩单导入, 小题分导入, 答题卡manifest导入, 答题卡导入, 学校成绩单, 考试成绩导入, StudentDataSQL真实数据导入, 真实数据加密归拢, 加密真实库导入, imports清理, seat-only, 座位号导入
- use_when: import class rosters, import school exam score sheets, normalize item-score CSVs, import exam item scores, import de-identified answer-card manifests, run seat-only roster or score ingestion
- do_not_use_when: analyze existing student data, produce weak-point reports, modify StudentDataSQL schema/migrations/scripts/router, import teaching content into raw/Wiki/RAG, import exam paper questions
- input: roster .xlsx, school score .xls, item-score CSV, answer-card folder or manifest CSV, term_id, class_id, assessment_id
- output: encrypted real DB import JSON, encrypted file-artifact archival, transient normalized CSV cleanup, de-identified answer-card artifact manifest, validation summary
- next_skills: analyze/student-data-analysis/SKILL.md

## Load rule

Read this card or `registry.yaml` for routing. When this is the primary match, read only `import/student-data-import/SKILL.md` first. Read `references/exam-score-import-workflow.md` only for exam score or answer-card workflows.
