CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL,
    parameters_json TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS student_knowledge_mastery (
    mastery_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    student_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    kp_id TEXT NOT NULL REFERENCES knowledge_points(kp_id) ON DELETE CASCADE,
    as_of_date TEXT NOT NULL,
    mastery_score REAL NOT NULL,
    evidence_count INTEGER NOT NULL,
    last_evidence_at TEXT,
    method TEXT NOT NULL,
    UNIQUE(run_id, student_id, kp_id)
);

CREATE TABLE IF NOT EXISTS class_knowledge_summary (
    summary_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES analysis_runs(run_id) ON DELETE CASCADE,
    class_id TEXT NOT NULL REFERENCES classes(class_id) ON DELETE CASCADE,
    kp_id TEXT NOT NULL REFERENCES knowledge_points(kp_id) ON DELETE CASCADE,
    period_start TEXT,
    period_end TEXT,
    avg_mastery REAL,
    weak_student_cnt INTEGER,
    evidence_count INTEGER
);

CREATE TABLE IF NOT EXISTS teacher_review_flags (
    flag_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    class_id TEXT NOT NULL REFERENCES classes(class_id) ON DELETE CASCADE,
    detected_at TEXT NOT NULL,
    flag_type TEXT NOT NULL,
    severity INTEGER NOT NULL,
    fact_basis TEXT NOT NULL,
    recommended_action TEXT,
    reviewed_by_teacher INTEGER NOT NULL DEFAULT 0,
    resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS import_batches (
    batch_id TEXT PRIMARY KEY,
    import_type TEXT NOT NULL,
    source_file TEXT,
    row_count INTEGER,
    success_count INTEGER,
    error_count INTEGER,
    imported_by TEXT,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    checksum TEXT,
    real_data INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS access_audit_log (
    audit_id TEXT PRIMARY KEY,
    accessed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    target TEXT NOT NULL,
    purpose TEXT,
    row_count INTEGER,
    anonymized INTEGER NOT NULL DEFAULT 0,
    real_data INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_mastery_student_kp ON student_knowledge_mastery(student_id, kp_id);
CREATE INDEX IF NOT EXISTS idx_class_summary_class ON class_knowledge_summary(class_id);
CREATE INDEX IF NOT EXISTS idx_audit_accessed_at ON access_audit_log(accessed_at);
