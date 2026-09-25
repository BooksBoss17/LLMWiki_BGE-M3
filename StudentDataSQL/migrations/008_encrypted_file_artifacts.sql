CREATE TABLE IF NOT EXISTS encrypted_file_artifacts (
    artifact_id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL,
    source_path TEXT NOT NULL,
    source_relpath TEXT NOT NULL,
    source_name TEXT NOT NULL,
    class_id TEXT,
    assessment_id TEXT,
    student_id TEXT,
    seat_no TEXT,
    linked_record_id TEXT,
    content_sha256 TEXT NOT NULL,
    content_size INTEGER NOT NULL,
    mime_type TEXT,
    content BLOB NOT NULL,
    imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    real_data INTEGER NOT NULL DEFAULT 1,
    UNIQUE(source_relpath, content_sha256)
);

CREATE INDEX IF NOT EXISTS idx_encrypted_file_artifacts_assessment
ON encrypted_file_artifacts(assessment_id, class_id, artifact_type);

CREATE INDEX IF NOT EXISTS idx_encrypted_file_artifacts_hash
ON encrypted_file_artifacts(content_sha256);
