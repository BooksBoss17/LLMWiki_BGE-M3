#!/usr/bin/env python
from __future__ import annotations

import argparse
import json

from student_data_common import audit, apply_migrations, connect_db, main_guard, print_json, resolve_args_db


def run() -> int:
    parser = argparse.ArgumentParser(description="Load synthetic demo data into the StudentDataSQL dev database.")
    parser.add_argument("--dev", action="store_true", default=True, help="use dev/synthetic database")
    parser.add_argument("--db", help="database path under StudentDataSQL/runtime/db")
    parser.add_argument("--json", action="store_true", help="print JSON result")
    args = parser.parse_args()
    path, real_data = resolve_args_db(args)
    if real_data:
        raise RuntimeError("synthetic demo import never writes real-data mode")
    conn = connect_db(path, real_data=False)
    apply_migrations(conn)

    conn.executescript(
        """
        DELETE FROM access_audit_log;
        DELETE FROM import_batches;
        DELETE FROM teacher_review_flags;
        DELETE FROM class_knowledge_summary;
        DELETE FROM student_knowledge_mastery;
        DELETE FROM analysis_runs;
        DELETE FROM learning_events;
        DELETE FROM daily_observations;
        DELETE FROM homework_corrections;
        DELETE FROM homework_item_results;
        DELETE FROM homework_submissions;
        DELETE FROM assignment_items;
        DELETE FROM assignments;
        DELETE FROM student_item_results;
        DELETE FROM student_assessment_results;
        DELETE FROM assessment_items;
        DELETE FROM assessments;
        DELETE FROM question_knowledge_points;
        DELETE FROM questions;
        DELETE FROM knowledge_points;
        DELETE FROM class_memberships;
        DELETE FROM student_identities;
        DELETE FROM students;
        DELETE FROM classes;
        DELETE FROM terms;
        """
    )

    conn.execute(
        "INSERT INTO terms(term_id, school_year, term_name, starts_on, ends_on) VALUES (?, ?, ?, ?, ?)",
        ("demo_term_2026_spring", "2025-2026", "spring", "2026-02-20", "2026-07-10"),
    )
    conn.execute(
        "INSERT INTO classes(class_id, class_name, grade_level, term_id) VALUES (?, ?, ?, ?)",
        ("demo_class_001", "合成高一3班", "高一", "demo_term_2026_spring"),
    )

    students = [
        ("demo_term_2026_spring_demo_class_001_F_seat01", "张", "F", "01"),
        ("demo_term_2026_spring_demo_class_001_M_seat02", "李", "M", "02"),
        ("demo_term_2026_spring_demo_class_001_F_seat03", "王", "F", "03"),
        ("demo_term_2026_spring_demo_class_001_M_seat04", "赵", "M", "04"),
    ]
    for student_id, family_name, gender, seat_no in students:
        code = student_id
        pseudonym = f"合成高一3班-{family_name}-{seat_no}号"
        conn.execute(
            "INSERT INTO students(student_id, student_code, pseudonym, family_name, gender) VALUES (?, ?, ?, ?, ?)",
            (student_id, code, pseudonym, family_name, gender),
        )
        conn.execute(
            """
            INSERT INTO class_memberships(membership_id, student_id, class_id, term_id, seat_no)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f"demo_membership_{code}", student_id, "demo_class_001", "demo_term_2026_spring", seat_no),
        )

    kps = [
        ("kp_force_analysis", "受力分析", "LLMWiki/concepts/受力分析.md"),
        ("kp_newton_second", "牛顿第二定律", "LLMWiki/concepts/牛顿第二定律.md"),
        ("kp_circular_motion", "圆周运动临界问题", "LLMWiki/concepts/圆周运动临界问题.md"),
    ]
    for kp_id, title, wiki_path in kps:
        conn.execute(
            "INSERT INTO knowledge_points(kp_id, title, wiki_path) VALUES (?, ?, ?)",
            (kp_id, title, wiki_path),
        )

    questions = [
        ("q_force_001", "受力分析选择题", "choice", 0.45, 6, "kp_force_analysis"),
        ("q_newton_001", "牛顿第二定律计算题", "calculation", 0.6, 8, "kp_newton_second"),
        ("q_circular_001", "圆周运动临界题", "calculation", 0.75, 10, "kp_circular_motion"),
    ]
    for question_id, title, qtype, difficulty, max_score, kp_id in questions:
        conn.execute(
            """
            INSERT INTO questions(question_id, title, question_type, difficulty, max_score)
            VALUES (?, ?, ?, ?, ?)
            """,
            (question_id, title, qtype, difficulty, max_score),
        )
        conn.execute(
            """
            INSERT INTO question_knowledge_points(question_id, kp_id, weight, role, confidence)
            VALUES (?, ?, 1.0, 'primary', 1.0)
            """,
            (question_id, kp_id),
        )

    conn.execute(
        """
        INSERT INTO assessments(assessment_id, class_id, term_id, title, assessment_type, assessed_on, max_score)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("demo_assess_001", "demo_class_001", "demo_term_2026_spring", "合成第一次月考", "monthly", "2026-03-15", 24),
    )
    for idx, (question_id, _title, _type, _difficulty, max_score, _kp) in enumerate(questions, 1):
        conn.execute(
            """
            INSERT INTO assessment_items(assessment_item_id, assessment_id, question_id, item_no, max_score, display_order)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (f"demo_item_{idx}", "demo_assess_001", question_id, f"Q{idx}", max_score, idx),
        )

    demo_student_ids = [student[0] for student in students]
    item_scores = {
        demo_student_ids[0]: [5, 7, 8],
        demo_student_ids[1]: [4, 5, 4],
        demo_student_ids[2]: [6, 6, 5],
        demo_student_ids[3]: [3, 4, 3],
    }
    for student_id, scores in item_scores.items():
        total = sum(scores)
        conn.execute(
            """
            INSERT INTO student_assessment_results(
                result_id, assessment_id, student_id, total_score, score_rate, percentile
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (f"demo_result_{student_id}", "demo_assess_001", student_id, total, total / 24.0, None),
        )
        for idx, score in enumerate(scores, 1):
            max_score = questions[idx - 1][4]
            conn.execute(
                """
                INSERT INTO student_item_results(
                    item_result_id, assessment_item_id, student_id, score, is_correct, error_type
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    f"demo_item_result_{student_id}_{idx}",
                    f"demo_item_{idx}",
                    student_id,
                    score,
                    int(score >= max_score * 0.8),
                    "concept" if score < max_score * 0.6 else None,
                ),
            )

    conn.execute(
        """
        INSERT INTO assignments(assignment_id, class_id, term_id, title, assignment_type, assigned_at, due_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("demo_assignment_001", "demo_class_001", "demo_term_2026_spring", "合成受力分析作业", "daily", "2026-03-16", "2026-03-17"),
    )
    for idx, (question_id, *_rest) in enumerate(questions[:2], 1):
        conn.execute(
            """
            INSERT INTO assignment_items(assignment_item_id, assignment_id, question_id, item_no, max_score)
            VALUES (?, ?, ?, ?, ?)
            """,
            (f"demo_assignment_item_{idx}", "demo_assignment_001", question_id, f"Q{idx}", questions[idx - 1][4]),
        )
    submissions = [
        (demo_student_ids[0], "submitted", 1.0, 92),
        (demo_student_ids[1], "late", 0.8, 76),
        (demo_student_ids[2], "submitted", 1.0, 84),
        (demo_student_ids[3], "missing", 0.0, None),
    ]
    for student_id, status, completion, score in submissions:
        submission_id = f"demo_submission_{student_id}"
        conn.execute(
            """
            INSERT INTO homework_submissions(
                submission_id, assignment_id, student_id, submitted_at, status, completion_rate, teacher_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                submission_id,
                "demo_assignment_001",
                student_id,
                "2026-03-17 20:00" if status != "missing" else None,
                status,
                completion,
                score,
            ),
        )
        conn.execute(
            """
            INSERT INTO homework_corrections(correction_id, submission_id, corrected_at, correction_status, correction_quality)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                f"demo_correction_{student_id}",
                submission_id,
                "2026-03-18" if status != "missing" else None,
                "completed" if status == "submitted" else "pending",
                4 if status == "submitted" else None,
            ),
        )

    run_id = "demo_analysis_run_001"
    conn.execute(
        "INSERT INTO analysis_runs(run_id, run_type, parameters_json, created_by) VALUES (?, ?, ?, ?)",
        (run_id, "demo_mastery", json.dumps({"source": "synthetic_demo"}, ensure_ascii=False), "system"),
    )
    summaries = [
        ("kp_force_analysis", 0.67, 1, 8),
        ("kp_newton_second", 0.57, 2, 4),
        ("kp_circular_motion", 0.50, 3, 4),
    ]
    for kp_id, avg_mastery, weak_count, evidence_count in summaries:
        conn.execute(
            """
            INSERT INTO class_knowledge_summary(
                summary_id, run_id, class_id, kp_id, period_start, period_end,
                avg_mastery, weak_student_cnt, evidence_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                f"demo_summary_{kp_id}",
                run_id,
                "demo_class_001",
                kp_id,
                "2026-03-15",
                "2026-03-18",
                avg_mastery,
                weak_count,
                evidence_count,
            ),
        )
    conn.execute(
        """
        INSERT INTO teacher_review_flags(
            flag_id, student_id, class_id, detected_at, flag_type, severity, fact_basis, recommended_action
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "demo_flag_001",
            demo_student_ids[3],
            "demo_class_001",
            "2026-03-18",
            "missing_homework",
            3,
            "合成数据：最近一次作业状态为 missing",
            "教师本地确认后安排补交与订正",
        ),
    )

    conn.commit()
    audit(
        conn,
        actor="system",
        action="import",
        target="synthetic_demo",
        purpose="load synthetic demo data",
        row_count=4,
        anonymized=True,
        real_data=False,
    )
    conn.close()
    payload = {"ok": True, "db": str(path), "class_id": "demo_class_001", "students": len(students)}
    if args.json:
        print_json(payload)
    else:
        print(f"Loaded synthetic demo data into {path}")
        print("Demo class id: demo_class_001")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
