ALTER TABLE assessment_items ADD COLUMN correct_answer TEXT;
ALTER TABLE assessment_items ADD COLUMN scoring_points_json TEXT;
ALTER TABLE assessment_items ADD COLUMN method_tags_json TEXT;

ALTER TABLE student_item_results ADD COLUMN response_text TEXT;
ALTER TABLE student_item_results ADD COLUMN method_error_type TEXT;
ALTER TABLE student_item_results ADD COLUMN error_detail TEXT;
ALTER TABLE student_item_results ADD COLUMN evidence_ref TEXT;

CREATE TABLE IF NOT EXISTS answer_card_artifacts (
    artifact_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    class_id TEXT NOT NULL REFERENCES classes(class_id) ON DELETE CASCADE,
    student_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    seat_no TEXT NOT NULL,
    artifact_status TEXT NOT NULL CHECK(artifact_status IN ('present', 'missing_artifact')),
    redacted_path TEXT,
    sha256 TEXT,
    evidence_ref TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(assessment_id, student_id)
);

CREATE INDEX IF NOT EXISTS idx_answer_card_artifacts_assessment
ON answer_card_artifacts(assessment_id, class_id, artifact_status);
