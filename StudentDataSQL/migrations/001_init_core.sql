PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS terms (
    term_id TEXT PRIMARY KEY,
    school_year TEXT NOT NULL,
    term_name TEXT NOT NULL,
    starts_on TEXT,
    ends_on TEXT,
    UNIQUE(school_year, term_name)
);

CREATE TABLE IF NOT EXISTS classes (
    class_id TEXT PRIMARY KEY,
    class_name TEXT NOT NULL,
    grade_level TEXT,
    term_id TEXT REFERENCES terms(term_id),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(class_name, term_id)
);

CREATE TABLE IF NOT EXISTS students (
    student_id TEXT PRIMARY KEY,
    student_code TEXT NOT NULL UNIQUE,
    pseudonym TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS student_identities (
    student_id TEXT PRIMARY KEY REFERENCES students(student_id) ON DELETE CASCADE,
    real_name TEXT NOT NULL,
    school_number TEXT,
    guardian_contact TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS class_memberships (
    membership_id TEXT PRIMARY KEY,
    student_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    class_id TEXT NOT NULL REFERENCES classes(class_id) ON DELETE CASCADE,
    term_id TEXT REFERENCES terms(term_id),
    seat_no TEXT,
    joined_on TEXT,
    left_on TEXT,
    UNIQUE(student_id, class_id, term_id)
);

CREATE TABLE IF NOT EXISTS knowledge_points (
    kp_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    wiki_path TEXT,
    raw_ref TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    UNIQUE(title, wiki_path)
);

CREATE TABLE IF NOT EXISTS questions (
    question_id TEXT PRIMARY KEY,
    source_ref TEXT,
    title TEXT,
    question_type TEXT,
    difficulty REAL,
    max_score REAL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS question_knowledge_points (
    question_id TEXT NOT NULL REFERENCES questions(question_id) ON DELETE CASCADE,
    kp_id TEXT NOT NULL REFERENCES knowledge_points(kp_id) ON DELETE CASCADE,
    weight REAL NOT NULL DEFAULT 1.0,
    role TEXT NOT NULL DEFAULT 'primary',
    confidence REAL NOT NULL DEFAULT 1.0,
    PRIMARY KEY(question_id, kp_id)
);

CREATE TABLE IF NOT EXISTS assessments (
    assessment_id TEXT PRIMARY KEY,
    class_id TEXT NOT NULL REFERENCES classes(class_id),
    term_id TEXT REFERENCES terms(term_id),
    title TEXT NOT NULL,
    assessment_type TEXT NOT NULL,
    assessed_on TEXT NOT NULL,
    max_score REAL,
    source_ref TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assessment_items (
    assessment_item_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    question_id TEXT REFERENCES questions(question_id),
    item_no TEXT NOT NULL,
    max_score REAL NOT NULL,
    display_order INTEGER NOT NULL,
    UNIQUE(assessment_id, item_no)
);

CREATE TABLE IF NOT EXISTS student_assessment_results (
    result_id TEXT PRIMARY KEY,
    assessment_id TEXT NOT NULL REFERENCES assessments(assessment_id) ON DELETE CASCADE,
    student_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    total_score REAL,
    score_rate REAL,
    class_rank INTEGER,
    percentile REAL,
    teacher_note TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(assessment_id, student_id)
);

CREATE TABLE IF NOT EXISTS student_item_results (
    item_result_id TEXT PRIMARY KEY,
    assessment_item_id TEXT NOT NULL REFERENCES assessment_items(assessment_item_id) ON DELETE CASCADE,
    student_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    score REAL,
    is_correct INTEGER,
    error_type TEXT,
    teacher_note TEXT,
    UNIQUE(assessment_item_id, student_id)
);

CREATE TABLE IF NOT EXISTS assignments (
    assignment_id TEXT PRIMARY KEY,
    class_id TEXT NOT NULL REFERENCES classes(class_id),
    term_id TEXT REFERENCES terms(term_id),
    title TEXT NOT NULL,
    assignment_type TEXT NOT NULL,
    assigned_at TEXT NOT NULL,
    due_at TEXT,
    source_ref TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS assignment_items (
    assignment_item_id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE CASCADE,
    question_id TEXT REFERENCES questions(question_id),
    item_no TEXT NOT NULL,
    max_score REAL,
    required_flag INTEGER NOT NULL DEFAULT 1,
    UNIQUE(assignment_id, item_no)
);

CREATE TABLE IF NOT EXISTS homework_submissions (
    submission_id TEXT PRIMARY KEY,
    assignment_id TEXT NOT NULL REFERENCES assignments(assignment_id) ON DELETE CASCADE,
    student_id TEXT NOT NULL REFERENCES students(student_id) ON DELETE CASCADE,
    submitted_at TEXT,
    status TEXT NOT NULL,
    completion_rate REAL,
    teacher_score REAL,
    time_spent_min INTEGER,
    teacher_comment TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(assignment_id, student_id)
);

CREATE TABLE IF NOT EXISTS homework_item_results (
    homework_item_result_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES homework_submissions(submission_id) ON DELETE CASCADE,
    assignment_item_id TEXT NOT NULL REFERENCES assignment_items(assignment_item_id) ON DELETE CASCADE,
    score REAL,
    is_correct INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 1,
    error_type TEXT,
    UNIQUE(submission_id, assignment_item_id)
);

CREATE TABLE IF NOT EXISTS homework_corrections (
    correction_id TEXT PRIMARY KEY,
    submission_id TEXT NOT NULL REFERENCES homework_submissions(submission_id) ON DELETE CASCADE,
    corrected_at TEXT,
    correction_status TEXT NOT NULL,
    correction_quality INTEGER,
    teacher_note TEXT
);

CREATE TABLE IF NOT EXISTS daily_observations (
    observation_id TEXT PRIMARY KEY,
    class_id TEXT NOT NULL REFERENCES classes(class_id),
    student_id TEXT NOT NULL REFERENCES students(student_id),
    observed_on TEXT NOT NULL,
    dimension TEXT NOT NULL,
    rating INTEGER,
    evidence TEXT,
    created_by TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS learning_events (
    event_id TEXT PRIMARY KEY,
    student_id TEXT REFERENCES students(student_id),
    class_id TEXT REFERENCES classes(class_id),
    event_time TEXT NOT NULL,
    actor_type TEXT NOT NULL DEFAULT 'student',
    verb TEXT NOT NULL,
    object_type TEXT NOT NULL,
    object_id TEXT,
    result_json TEXT,
    context_json TEXT,
    source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_class_memberships_class ON class_memberships(class_id);
CREATE INDEX IF NOT EXISTS idx_item_results_student ON student_item_results(student_id);
CREATE INDEX IF NOT EXISTS idx_homework_submissions_student ON homework_submissions(student_id);
CREATE INDEX IF NOT EXISTS idx_qkp_kp ON question_knowledge_points(kp_id);
