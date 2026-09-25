ALTER TABLE assessment_items ADD COLUMN key_point TEXT;

ALTER TABLE assessment_items ADD COLUMN difficulty_point TEXT;

ALTER TABLE assessment_items ADD COLUMN exam_report_ref TEXT;

DROP VIEW IF EXISTS v_exam_item_analysis_safe;

CREATE VIEW v_exam_item_analysis_safe AS
SELECT
    c.class_id,
    c.class_name,
    a.assessment_id,
    a.title AS assessment_title,
    ai.item_no,
    ai.max_score,
    ai.key_point,
    ai.difficulty_point,
    ai.exam_report_ref,
    q.question_id,
    kp.kp_id,
    kp.title AS knowledge_point,
    kp.wiki_path,
    COUNT(r.item_result_id) AS evidence_count,
    ROUND(AVG(CASE WHEN ai.max_score > 0 THEN r.score / ai.max_score ELSE NULL END), 3) AS avg_score_rate,
    ROUND(AVG(COALESCE(r.is_correct, 0)), 3) AS correct_rate
FROM assessment_items ai
JOIN assessments a ON a.assessment_id = ai.assessment_id
JOIN classes c ON c.class_id = a.class_id
LEFT JOIN questions q ON q.question_id = ai.question_id
LEFT JOIN question_knowledge_points qkp ON qkp.question_id = q.question_id
LEFT JOIN knowledge_points kp ON kp.kp_id = qkp.kp_id
LEFT JOIN student_item_results r ON r.assessment_item_id = ai.assessment_item_id
GROUP BY
    c.class_id, c.class_name, a.assessment_id, a.title,
    ai.item_no, ai.max_score, ai.key_point, ai.difficulty_point, ai.exam_report_ref,
    q.question_id, kp.kp_id, kp.title, kp.wiki_path;
