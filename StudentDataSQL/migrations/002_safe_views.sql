CREATE VIEW IF NOT EXISTS v_class_knowledge_summary_safe AS
SELECT
    c.class_id,
    c.class_name,
    kp.kp_id,
    kp.title AS knowledge_point,
    kp.wiki_path,
    s.avg_mastery,
    s.weak_student_cnt,
    s.evidence_count,
    s.period_start,
    s.period_end
FROM class_knowledge_summary s
JOIN classes c ON c.class_id = s.class_id
JOIN knowledge_points kp ON kp.kp_id = s.kp_id;

CREATE VIEW IF NOT EXISTS v_homework_recent_safe AS
SELECT
    c.class_id,
    c.class_name,
    a.assignment_id,
    a.title AS assignment_title,
    a.assigned_at,
    COUNT(h.submission_id) AS student_count,
    SUM(CASE WHEN h.status = 'submitted' THEN 1 ELSE 0 END) AS submitted_count,
    SUM(CASE WHEN h.status = 'late' THEN 1 ELSE 0 END) AS late_count,
    SUM(CASE WHEN h.status = 'missing' THEN 1 ELSE 0 END) AS missing_count,
    ROUND(AVG(COALESCE(h.completion_rate, 0)), 3) AS avg_completion_rate
FROM assignments a
JOIN classes c ON c.class_id = a.class_id
LEFT JOIN homework_submissions h ON h.assignment_id = a.assignment_id
GROUP BY c.class_id, c.class_name, a.assignment_id, a.title, a.assigned_at;

CREATE VIEW IF NOT EXISTS v_student_profile_pseudonymized AS
SELECT
    s.student_id,
    s.pseudonym,
    s.status,
    c.class_id,
    c.class_name,
    COUNT(DISTINCT ar.assessment_id) AS assessment_count,
    ROUND(AVG(ar.score_rate), 3) AS avg_score_rate,
    COUNT(DISTINCT hs.assignment_id) AS assignment_count,
    ROUND(AVG(COALESCE(hs.completion_rate, 0)), 3) AS avg_completion_rate
FROM students s
LEFT JOIN class_memberships cm ON cm.student_id = s.student_id
LEFT JOIN classes c ON c.class_id = cm.class_id
LEFT JOIN student_assessment_results ar ON ar.student_id = s.student_id
LEFT JOIN homework_submissions hs ON hs.student_id = s.student_id
GROUP BY s.student_id, s.pseudonym, s.status, c.class_id, c.class_name;

CREATE VIEW IF NOT EXISTS v_exam_item_analysis_safe AS
SELECT
    c.class_id,
    c.class_name,
    a.assessment_id,
    a.title AS assessment_title,
    ai.item_no,
    ai.max_score,
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
GROUP BY c.class_id, c.class_name, a.assessment_id, a.title, ai.item_no, ai.max_score, q.question_id, kp.kp_id, kp.title, kp.wiki_path;
