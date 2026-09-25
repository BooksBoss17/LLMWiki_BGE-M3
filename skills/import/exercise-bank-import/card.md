# exercise-bank-import card

- status: active
- category: import
- role: role-a-import-maintenance
- canonical_skill: `import/exercise-bank-import/SKILL.md`
- manifest: `import/exercise-bank-import/manifest.yaml`
- description: Import exercise sheets, homework, exams, lecture questions into the exercise bank with question extraction, answer/explanation pairing, standard Markdown, image readability audit, RAG parser compatibility gates, exam analysis reports, exam design reports, StudentDataSQL exam-report copies, quality loops, and Wiki/RAG sync.
- triggers: 题库导入, 试卷入库, 讲义题目提取, 题目配对, 月考, 期中考, 期末考, 模拟考, 高考真题
- use_when: import test paper, import exercise bank questions, extract questions, pair answers explanations, create raw/exercises records, create exam analysis report, create exam design analysis report, copy paper report StudentDataSQL exam_reports
- do_not_use_when: only tagging existing questions, simple problem solving, only exporting Word/PDF, only converting source document without exercise-bank import
- input: DOCX, PDF, Markdown, paired_questions.json, source paper and answer materials
- output: per-question Markdown files, media assets, quality reports, optional solution-curation proposal, stable kp_id assignment, import_handoff.json, graph_update_result.json, RAG rebuild handoff
- next_skills: import/bemarkdown/SKILL.md, learn/exercise-solution-curation/SKILL.md, taxonomy/exercise-knowledge-tags/SKILL.md, maintain/llmwiki-maintenance/SKILL.md, learn/physics-knowledge-graph/SKILL.md, maintain/rag-management/SKILL.md

## Load rule

Read card via `registry.yaml` routing. On match, read only `import/exercise-bank-import/SKILL.md` first. Read scripts, references, and runtime details only when the loaded skill asks for them.
