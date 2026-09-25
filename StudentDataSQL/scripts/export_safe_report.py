#!/usr/bin/env python
from __future__ import annotations

import argparse
from pathlib import Path

from student_data_common import (
    EXPORTS_DIR,
    ROOT,
    StudentDataError,
    add_mode_args,
    apply_migrations,
    audit,
    connect_db,
    main_guard,
    print_json,
    resolve_args_db,
    sanitize_rows,
)


def render_weekly_report(class_id: str, summary_rows: list[dict], homework_rows: list[dict]) -> str:
    lines = [
        "# 学生数据脱敏周报",
        "",
        f"- class_id: `{class_id}`",
        "- privacy: safe aggregate / pseudonymized only",
        "",
        "## 薄弱知识点",
        "",
    ]
    if summary_rows:
        for row in summary_rows:
            lines.append(
                f"- {row.get('knowledge_point')}: avg_mastery={row.get('avg_mastery')}, "
                f"weak_student_cnt={row.get('weak_student_cnt')}, wiki_path={row.get('wiki_path')}"
            )
    else:
        lines.append("- 暂无分析结果。")
    lines.extend(["", "## 近期作业", ""])
    if homework_rows:
        for row in homework_rows:
            lines.append(
                f"- {row.get('assignment_title')}: submitted={row.get('submitted_count')}, "
                f"late={row.get('late_count')}, missing={row.get('missing_count')}, "
                f"avg_completion_rate={row.get('avg_completion_rate')}"
            )
    else:
        lines.append("- 暂无作业记录。")
    lines.extend(
        [
            "",
            "## 边界说明",
            "",
            "本报告不包含学生真实姓名、学号、家长联系方式、公开排名或原始教师备注。",
        ]
    )
    return "\n".join(lines) + "\n"


def run() -> int:
    parser = argparse.ArgumentParser(description="Export a safe anonymized StudentDataSQL report.")
    add_mode_args(parser, default_dev=True)
    parser.add_argument("--class-id", required=True)
    parser.add_argument("--type", choices=["weekly"], default="weekly")
    parser.add_argument("--out", required=True, help="output Markdown path under StudentDataSQL/runtime/exports")
    parser.add_argument("--actor", default="agent")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        out = ROOT.parent / out
    exports_dir = EXPORTS_DIR
    if exports_dir.resolve() not in out.resolve().parents and out.resolve() != exports_dir.resolve():
        raise StudentDataError(f"output must be under {exports_dir}")
    out.parent.mkdir(parents=True, exist_ok=True)

    path, real_data = resolve_args_db(args)
    conn = connect_db(path, real_data=real_data)
    apply_migrations(conn)
    summary = sanitize_rows(
        conn.execute(
            "SELECT * FROM v_class_knowledge_summary_safe WHERE class_id = ? ORDER BY avg_mastery ASC",
            (args.class_id,),
        ).fetchall()
    )
    homework = sanitize_rows(
        conn.execute(
            "SELECT * FROM v_homework_recent_safe WHERE class_id = ? ORDER BY assigned_at DESC LIMIT 10",
            (args.class_id,),
        ).fetchall()
    )
    text = render_weekly_report(args.class_id, summary, homework)
    out.write_text(text, encoding="utf-8")
    audit(
        conn,
        actor=args.actor,
        action="export",
        target=str(out.relative_to(ROOT.parent)),
        purpose="safe weekly report export",
        row_count=len(summary) + len(homework),
        anonymized=True,
        real_data=real_data,
    )
    conn.close()
    payload = {"ok": True, "db": str(path), "out": str(out), "summary_rows": len(summary), "homework_rows": len(homework)}
    if args.json:
        print_json(payload)
    else:
        print(f"Wrote safe report: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
