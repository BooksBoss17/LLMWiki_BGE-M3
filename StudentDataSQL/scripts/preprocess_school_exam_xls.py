#!/usr/bin/env python
"""Normalize school .xls score sheets and midterm answer-card manifests.

The script reads real score files, but it writes only seat-only CSV outputs:
no names, school numbers, exam numbers, or original answer-card filenames.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from student_data_common import IMPORTS_DIR, ROOT, StudentDataError, assert_child_path, main_guard, print_json


TERM_ID = "2025-2026-s2"
SCHOOL_YEAR = "2025-2026"
TERM_NAME = "second"
DEFAULT_OUT_DIR = IMPORTS_DIR / "normalized"

CLASS_CONFIG = {
    "g2c01": {"class_name": "高二01班", "grade_level": "高二", "file_tokens": ["1班", "01班"]},
    "g2c02": {"class_name": "高二02班", "grade_level": "高二", "file_tokens": ["2班", "02班"]},
}

EXAM_CONFIG = {
    "monthly": {
        "exam_base": "physics_2026_04_monthly",
        "assessment_type": "monthly",
        "title": "2025-2026学年下高二物理4月质量检测",
        "file_tokens": ["4月", "阶段性"],
        "default_date": "2026-04-01",
        "pdf_token": "4月",
        "max_score": 100.0,
    },
    "midterm": {
        "exam_base": "physics_2026_midterm",
        "assessment_type": "midterm",
        "title": "2025-2026学年下高二物理期中质量检测",
        "file_tokens": ["期中", "半期"],
        "default_date": "2026-05-01",
        "pdf_token": "半期",
        "max_score": 100.0,
    },
}

FORBIDDEN_HEADERS = ["姓名", "学号", "考号", "学籍号", "real_name", "school_number", "exam_number"]
COMPOUND_SURNAMES = (
    "欧阳",
    "太史",
    "端木",
    "上官",
    "司马",
    "东方",
    "独孤",
    "南宫",
    "万俟",
    "闻人",
    "夏侯",
    "诸葛",
    "尉迟",
    "公羊",
    "赫连",
    "澹台",
    "皇甫",
    "宗政",
    "濮阳",
    "公冶",
    "太叔",
    "申屠",
    "公孙",
    "慕容",
    "仲孙",
    "钟离",
    "长孙",
    "宇文",
    "司徒",
    "鲜于",
    "司空",
)


@dataclass(frozen=True)
class StudentRecord:
    student_id: str
    pseudonym: str
    family_name: str
    gender: str
    class_id: str
    class_name: str
    grade_level: str
    seat_no: str
    total_score: str
    class_rank: str
    grade_rank: str
    score_band: str


@dataclass
class ItemDef:
    key: str
    item_no: str
    question_id: str
    question_type: str
    correct_answer: str
    score_col: int | None = None
    response_col: int | None = None
    max_score: float = 1.0


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def discover_source_dir() -> Path:
    desktop = Path.home() / "Desktop"
    for d1 in desktop.iterdir():
        if not d1.is_dir():
            continue
        candidates = [d1]
        try:
            candidates += [p for p in d1.iterdir() if p.is_dir()]
        except OSError:
            pass
        for candidate in candidates:
            try:
                if len(list(candidate.glob("*.xls"))) >= 4 and len(list(candidate.glob("*.pdf"))) >= 2:
                    return candidate
            except OSError:
                continue
    raise StudentDataError("could not discover source dir; pass --source-dir")


def classify_class(path: Path) -> str | None:
    name = path.name
    for class_id, cfg in CLASS_CONFIG.items():
        if any(token in name for token in cfg["file_tokens"]):
            return class_id
    return None


def classify_exam(path: Path) -> str | None:
    name = path.name
    for exam, cfg in EXAM_CONFIG.items():
        if any(token in name for token in cfg["file_tokens"]):
            return exam
    return None


def find_score_files(source_dir: Path, class_filter: str, exam_filter: str) -> list[tuple[str, str, Path]]:
    result: list[tuple[str, str, Path]] = []
    for path in sorted(source_dir.glob("*.xls")):
        class_id = classify_class(path)
        exam = classify_exam(path)
        if not class_id or not exam:
            continue
        if class_filter != "all" and class_id != class_filter:
            continue
        if exam_filter != "all" and exam != exam_filter:
            continue
        result.append((class_id, exam, path))
    if not result:
        raise StudentDataError("no matching .xls score files found")
    return result


def excel_matrix(path: Path) -> dict[str, list[list[str]]]:
    try:
        from win32com.client import DispatchEx  # type: ignore
    except ImportError as exc:
        raise StudentDataError("win32com is required to read legacy .xls files through Excel COM") from exc

    excel = DispatchEx("Excel.Application")
    excel.Visible = False
    excel.DisplayAlerts = False
    workbook = None
    try:
        workbook = excel.Workbooks.Open(str(path), None, True)
        sheets: dict[str, list[list[str]]] = {}
        for idx in range(1, workbook.Worksheets.Count + 1):
            sheet = workbook.Worksheets.Item(idx)
            used = sheet.UsedRange
            rows = int(used.Rows.Count)
            cols = int(used.Columns.Count)
            matrix: list[list[str]] = []
            for r in range(1, rows + 1):
                matrix.append([str(sheet.Cells(r, c).Text or "").strip() for c in range(1, cols + 1)])
            sheets[str(sheet.Name)] = matrix
        return sheets
    finally:
        if workbook is not None:
            workbook.Close(False)
        excel.Quit()


def find_total_sheet(sheets: dict[str, list[list[str]]]) -> list[list[str]]:
    for rows in sheets.values():
        if rows and "班内学号（座位号）" in rows[0] and "得分" in rows[0]:
            return rows
    raise StudentDataError("missing total score sheet with seat number column")


def find_item_sheet(sheets: dict[str, list[list[str]]]) -> list[list[str]]:
    for name, rows in sheets.items():
        if "小题分" in name and len(rows) >= 3:
            return rows
    for rows in sheets.values():
        if len(rows) >= 3 and rows[0][:6] == ["序号", "姓名", "班级", "学号", "考号", "总分"]:
            return rows
    raise StudentDataError("missing item score sheet")


def column_index(header: list[str], name: str) -> int:
    try:
        return header.index(name)
    except ValueError as exc:
        raise StudentDataError(f"missing required column: {name}") from exc


def seat_text(value: str) -> str:
    text = str(value).strip()
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]
    if not text:
        raise StudentDataError("blank seat number")
    return str(int(text)) if text.isdigit() else text


def family_name_from_real_name(real_name: str) -> str:
    name = re.sub(r"\s+", "", real_name or "")
    if not name:
        return "未知"
    for surname in COMPOUND_SURNAMES:
        if name.startswith(surname):
            return surname
    return name[0]


def normalize_gender(value: str) -> str:
    text = (value or "").strip().upper()
    if text in {"M", "MALE", "男", "男生"}:
        return "M"
    if text in {"F", "FEMALE", "女", "女生"}:
        return "F"
    return "U"


def load_roster_by_seat(class_id: str, roster_csv: Path | None) -> dict[str, dict[str, str]]:
    path = roster_csv or (DEFAULT_OUT_DIR / f"{class_id}_{TERM_ID}_roster.csv")
    if not path.is_absolute():
        path = ROOT.parent / path
    if not path.exists():
        raise StudentDataError(
            f"missing roster CSV for {class_id}: {path}. "
            "Run preprocess_roster_xlsx.py first; score imports must align by class_id + seat_no."
        )
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
    required = {"student_id", "class_id", "class_name", "family_name", "gender", "seat_no"}
    missing = sorted(required.difference(rows[0].keys() if rows else []))
    if missing:
        raise StudentDataError(f"roster CSV missing required columns: {', '.join(missing)}")
    by_seat: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("class_id") != class_id:
            continue
        seat = seat_text(row.get("seat_no", ""))
        if seat in by_seat:
            raise StudentDataError(f"duplicate roster seat_no for {class_id}: {seat}")
        by_seat[seat] = row
    if not by_seat:
        raise StudentDataError(f"roster CSV has no rows for class_id={class_id}: {path}")
    return by_seat


def parse_total_rows(
    rows: list[list[str]],
    class_id: str,
    roster_by_seat: dict[str, dict[str, str]],
) -> tuple[dict[str, StudentRecord], set[str], set[str]]:
    header = rows[0]
    idx_name = column_index(header, "姓名")
    idx_class = column_index(header, "班级")
    idx_seat = column_index(header, "班内学号（座位号）")
    idx_score = column_index(header, "得分")
    idx_grade_rank = column_index(header, "年级排名")
    idx_class_rank = column_index(header, "班级排名")
    idx_score_band = column_index(header, "档次")
    idx_school = column_index(header, "学号")
    idx_exam = column_index(header, "考号")
    by_name: dict[str, StudentRecord] = {}
    identity_terms: set[str] = set(FORBIDDEN_HEADERS)
    numeric_ids: set[str] = set()
    cfg = CLASS_CONFIG[class_id]
    for row in rows[1:]:
        if len(row) <= max(idx_name, idx_class, idx_seat, idx_score, idx_grade_rank, idx_class_rank, idx_score_band):
            continue
        name = row[idx_name].strip()
        seat_no = seat_text(row[idx_seat].strip())
        if not name or not seat_no:
            continue
        total_score = row[idx_score].strip()
        if parse_float(total_score) is None:
            continue
        roster = roster_by_seat.get(seat_no)
        if not roster:
            raise StudentDataError(
                f"score sheet row has no roster match for class_id={class_id}, seat_no={seat_no}"
            )
        record = StudentRecord(
            student_id=roster["student_id"],
            pseudonym=roster.get("pseudonym") or f"{roster.get('class_name') or cfg['class_name']}-{roster.get('family_name')}-{seat_no}号",
            family_name=roster["family_name"],
            gender=normalize_gender(roster["gender"]),
            class_id=class_id,
            class_name=roster.get("class_name") or row[idx_class].strip() or cfg["class_name"],
            grade_level=roster.get("grade_level") or cfg["grade_level"],
            seat_no=seat_no,
            total_score=total_score,
            class_rank=row[idx_class_rank].strip(),
            grade_rank=row[idx_grade_rank].strip(),
            score_band=row[idx_score_band].strip(),
        )
        by_name[name] = record
        identity_terms.add(name)
        for idx in (idx_school, idx_exam):
            value = row[idx].strip() if idx < len(row) else ""
            if value and value != "-":
                numeric_ids.add(value)
    return by_name, identity_terms, numeric_ids


def extract_item_no(label: str) -> tuple[str, str]:
    answer = ""
    m = re.search(r"（答案(.+?)）", label)
    if m:
        answer = m.group(1).replace("，", ",").strip()
    item_no = re.sub(r"（答案.+?）", "", label).strip()
    return item_no, answer


def item_key(item_no: str) -> str:
    text = item_no.strip()
    text = text.replace("（", "(").replace("）", ")")
    text = re.sub(r"\(([^)]+)\)", r"_\1", text)
    text = text.replace(".", "_").replace("-", "_")
    text = re.sub(r"[^0-9A-Za-z_]+", "_", text).strip("_")
    if text.isdigit() and len(text) == 1:
        text = text.zfill(2)
    return "Q" + text


def parse_float(value: str) -> float | None:
    text = str(value).strip()
    if not text or text in {"-", "None", "null", "缺", "缺考"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_item_defs(rows: list[list[str]], exam: str) -> list[ItemDef]:
    if len(rows) < 3:
        raise StudentDataError("item sheet must have at least two header rows and one data row")
    header1 = rows[0]
    header2 = rows[1] if len(rows) > 1 else []
    items_by_key: dict[str, ItemDef] = {}
    current_key = ""
    exam_base = EXAM_CONFIG[exam]["exam_base"]
    for col in range(6, len(header1)):
        top = header1[col].strip() if col < len(header1) else ""
        sub = header2[col].strip() if col < len(header2) else ""
        if top:
            item_no, answer = extract_item_no(top)
            key = item_key(item_no)
            question_id = f"{exam_base}_q{key[1:].lower()}"
            items_by_key.setdefault(
                key,
                ItemDef(
                    key=key,
                    item_no=item_no,
                    question_id=question_id,
                    question_type="choice" if answer else "subjective",
                    correct_answer=answer,
                ),
            )
            current_key = key
        if not current_key:
            continue
        item = items_by_key[current_key]
        if sub == "作答":
            item.response_col = col
        else:
            item.score_col = col
    items = list(items_by_key.values())
    for item in items:
        scores = []
        if item.score_col is None:
            continue
        for row in rows[2:]:
            if item.score_col >= len(row):
                continue
            value = parse_float(row[item.score_col])
            if value is not None:
                scores.append(value)
        item.max_score = max(scores) if scores else 1.0
    return items


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def score_rows(
    item_rows: list[list[str]],
    items: list[ItemDef],
    students_by_name: dict[str, StudentRecord],
    class_id: str,
    exam: str,
    assessed_on: str,
) -> list[dict[str, str]]:
    cfg = EXAM_CONFIG[exam]
    assessment_id = f"{class_id}_{cfg['exam_base']}"
    result: list[dict[str, str]] = []
    for source_row in item_rows[2:]:
        if len(source_row) < 6:
            continue
        name = source_row[1].strip()
        if not name or name not in students_by_name:
            continue
        student = students_by_name[name]
        if parse_float(student.total_score) is None:
            continue
        row: dict[str, str] = {
            "assessment_id": assessment_id,
            "class_id": class_id,
            "term_id": TERM_ID,
            "title": cfg["title"],
            "assessment_type": cfg["assessment_type"],
            "assessed_on": assessed_on,
            "max_score": str(cfg["max_score"]),
            "source_ref": cfg["exam_base"],
            "student_id": student.student_id,
            "total_score": student.total_score,
            "class_rank": student.class_rank,
            "grade_rank": student.grade_rank,
            "score_band": student.score_band,
        }
        for item in items:
            score = source_row[item.score_col].strip() if item.score_col is not None and item.score_col < len(source_row) else ""
            response = source_row[item.response_col].strip() if item.response_col is not None and item.response_col < len(source_row) else ""
            prefix = item.key
            row[f"{prefix}_score"] = score
            row[f"{prefix}_response"] = response
            row[f"{prefix}_max_score"] = str(item.max_score)
            row[f"{prefix}_item_no"] = item.item_no
            row[f"{prefix}_question_id"] = item.question_id
            row[f"{prefix}_question_type"] = item.question_type
            row[f"{prefix}_correct_answer"] = item.correct_answer
            row[f"{prefix}_kp_id"] = ""
            row[f"{prefix}_kp_title"] = ""
            row[f"{prefix}_wiki_path"] = ""
            row[f"{prefix}_key_point"] = ""
            row[f"{prefix}_difficulty_point"] = ""
            row[f"{prefix}_exam_report_ref"] = ""
            row[f"{prefix}_method_tags_json"] = "[]"
            row[f"{prefix}_scoring_points_json"] = "[]"
            row[f"{prefix}_error_type"] = ""
            row[f"{prefix}_method_error_type"] = ""
            row[f"{prefix}_error_detail"] = ""
            row[f"{prefix}_evidence_ref"] = ""
        result.append(row)
    return result


def item_map_rows(items: list[ItemDef], exam: str) -> list[dict[str, str]]:
    cfg = EXAM_CONFIG[exam]
    rows: list[dict[str, str]] = []
    for item in items:
        rows.append(
            {
                "exam_base": cfg["exam_base"],
                "assessment_item_key": item.key,
                "item_no": item.item_no,
                "question_id": item.question_id,
                "question_type": item.question_type,
                "max_score": str(item.max_score),
                "correct_answer": item.correct_answer,
                "kp_id": "",
                "kp_title": "",
                "key_point": "",
                "difficulty_point": "",
                "wiki_path": "",
                "method_tags_json": "[]",
                "scoring_points_json": "[]",
                "needs_review": "1",
            }
        )
    return rows


def card_name(path: Path) -> str:
    return re.sub(r"\([^)]*\)$", "", path.stem).strip()


def collect_answer_cards(source_dir: Path) -> dict[str, dict[str, Path]]:
    result: dict[str, dict[str, Path]] = {"g2c01": {}, "g2c02": {}}
    for path in source_dir.rglob("*.png"):
        class_id = None
        for part in path.parts:
            if "高二01班" in part and "高二02班" not in part:
                class_id = "g2c01"
            elif "高二02班" in part and "高二01班" not in part:
                class_id = "g2c02"
        if not class_id:
            continue
        result[class_id][card_name(path)] = path
    return result


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def rel_to_repo(path: Path) -> str:
    return str(path.resolve().relative_to(ROOT.parent.resolve())).replace("\\", "/")


def answer_card_manifest_rows(
    source_dir: Path,
    out_dir: Path,
    students_by_name: dict[str, StudentRecord],
    class_id: str,
    exam: str,
    *,
    copy_cards: bool,
) -> tuple[list[dict[str, str]], dict[str, int]]:
    cards = collect_answer_cards(source_dir).get(class_id, {})
    cfg = EXAM_CONFIG[exam]
    assessment_id = f"{class_id}_{cfg['exam_base']}"
    target_dir = IMPORTS_DIR / "redacted_answer_cards" / cfg["exam_base"]
    assert_child_path(target_dir, IMPORTS_DIR)
    rows: list[dict[str, str]] = []
    stats = {"present": 0, "missing_artifact": 0}
    for name, student in sorted(students_by_name.items(), key=lambda item: int(item[1].seat_no) if item[1].seat_no.isdigit() else item[1].seat_no):
        source = cards.get(name)
        status = "present" if source else "missing_artifact"
        stats[status] += 1
        redacted_path = ""
        checksum = ""
        if source:
            target = target_dir / f"{class_id}-seat{student.seat_no}.png"
            redacted_path = rel_to_repo(target)
            checksum = file_sha256(source)
            if copy_cards:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        rows.append(
            {
                "assessment_id": assessment_id,
                "class_id": class_id,
                "student_id": student.student_id,
                "seat_no": student.seat_no,
                "artifact_status": status,
                "redacted_path": redacted_path,
                "sha256": checksum,
                "evidence_ref": f"answer_card:{assessment_id}:{student.student_id}" if source else "missing_artifact",
            }
        )
    return rows, stats


def scan_privacy(paths: list[Path], identity_terms: set[str], numeric_ids: set[str]) -> list[dict[str, str]]:
    violations: list[dict[str, str]] = []
    forbidden = {term for term in identity_terms | numeric_ids if term}
    for path in paths:
        if not path.exists() or path.suffix.lower() != ".csv":
            continue
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for term in sorted(forbidden, key=len, reverse=True):
            if term and term in text:
                violations.append({"path": str(path), "term": term})
                break
    return violations


def build_score_header(items: list[ItemDef]) -> list[str]:
    base = [
        "assessment_id",
        "class_id",
        "term_id",
        "title",
        "assessment_type",
        "assessed_on",
        "max_score",
        "source_ref",
        "student_id",
        "total_score",
        "class_rank",
        "grade_rank",
        "score_band",
    ]
    fields: list[str] = []
    for item in items:
        fields.extend(
            [
                f"{item.key}_score",
                f"{item.key}_response",
                f"{item.key}_max_score",
                f"{item.key}_item_no",
                f"{item.key}_question_id",
                f"{item.key}_question_type",
                f"{item.key}_correct_answer",
                f"{item.key}_kp_id",
                f"{item.key}_kp_title",
                f"{item.key}_wiki_path",
                f"{item.key}_key_point",
                f"{item.key}_difficulty_point",
                f"{item.key}_exam_report_ref",
                f"{item.key}_method_tags_json",
                f"{item.key}_scoring_points_json",
                f"{item.key}_error_type",
                f"{item.key}_method_error_type",
                f"{item.key}_error_detail",
                f"{item.key}_evidence_ref",
            ]
        )
    return base + fields


def run() -> int:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Preprocess school physics exam .xls files into seat-only CSV imports.")
    parser.add_argument("--source-dir", help="source directory containing four .xls score files, two PDFs, and answer-card PNGs")
    parser.add_argument("--class-code", choices=["all", "g2c01", "g2c02"], default="all")
    parser.add_argument("--exam", choices=["all", "monthly", "midterm"], default="all")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="output directory under StudentDataSQL/runtime/imports")
    parser.add_argument("--roster-csv", help="optional canonical roster CSV; default is imports/normalized/<class_id>_<term_id>_roster.csv")
    parser.add_argument("--monthly-date", default=EXAM_CONFIG["monthly"]["default_date"])
    parser.add_argument("--midterm-date", default=EXAM_CONFIG["midterm"]["default_date"])
    parser.add_argument("--copy-answer-cards", action="store_true", help="copy PNGs to de-identified ignored paths")
    parser.add_argument("--dry-run", action="store_true", help="write normalized files only; no database import is performed")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    source_dir = Path(args.source_dir).resolve() if args.source_dir else discover_source_dir()
    if not source_dir.exists():
        raise StudentDataError(f"source dir does not exist: {source_dir}")
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = ROOT.parent / out_dir
    assert_child_path(out_dir, IMPORTS_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)

    score_files = find_score_files(source_dir, args.class_code, args.exam)
    written: list[Path] = []
    summary: dict[str, Any] = {
        "ok": True,
        "source_dir": str(source_dir),
        "out_dir": str(out_dir),
        "dry_run": bool(args.dry_run),
        "copy_answer_cards": bool(args.copy_answer_cards),
        "classes": {},
        "assessments": {},
        "privacy_violations": [],
    }
    all_identity_terms: set[str] = set(FORBIDDEN_HEADERS)
    all_numeric_ids: set[str] = set()
    item_maps_written: set[str] = set()

    for class_id, exam, path in score_files:
        sheets = excel_matrix(path)
        total = find_total_sheet(sheets)
        item_sheet = find_item_sheet(sheets)
        roster_csv = Path(args.roster_csv).resolve() if args.roster_csv else None
        roster_by_seat = load_roster_by_seat(class_id, roster_csv)
        students_by_name, identity_terms, numeric_ids = parse_total_rows(total, class_id, roster_by_seat)
        all_identity_terms.update(identity_terms)
        all_numeric_ids.update(numeric_ids)
        items = parse_item_defs(item_sheet, exam)
        assessed_on = args.midterm_date if exam == "midterm" else args.monthly_date
        assessment_id = f"{class_id}_{EXAM_CONFIG[exam]['exam_base']}"

        scores = score_rows(item_sheet, items, students_by_name, class_id, exam, assessed_on)
        score_path = out_dir / f"{assessment_id}_scores.csv"
        write_csv(score_path, build_score_header(items), scores)
        written.append(score_path)

        exam_base = EXAM_CONFIG[exam]["exam_base"]
        if exam_base not in item_maps_written:
            map_path = out_dir / f"{exam_base}_item_map_review.csv"
            write_csv(
                map_path,
                [
                    "exam_base",
                    "assessment_item_key",
                    "item_no",
                    "question_id",
                    "question_type",
                    "max_score",
                    "correct_answer",
                    "kp_id",
                    "kp_title",
                    "key_point",
                    "difficulty_point",
                    "wiki_path",
                    "method_tags_json",
                    "scoring_points_json",
                    "needs_review",
                ],
                item_map_rows(items, exam),
            )
            written.append(map_path)
            item_maps_written.add(exam_base)

        card_stats = None
        if exam in EXAM_CONFIG:
            manifest, card_stats = answer_card_manifest_rows(
                source_dir,
                out_dir,
                students_by_name,
                class_id,
                exam,
                copy_cards=args.copy_answer_cards,
            )
            manifest_path = out_dir / f"{assessment_id}_answer_card_manifest.csv"
            write_csv(
                manifest_path,
                [
                    "assessment_id",
                    "class_id",
                    "student_id",
                    "seat_no",
                    "artifact_status",
                    "redacted_path",
                    "sha256",
                    "evidence_ref",
                ],
                manifest,
            )
            written.append(manifest_path)

        class_summary = summary["classes"].setdefault(
            class_id,
            {"student_count_max": 0, "assessments": {}},
        )
        class_summary["student_count_max"] = max(class_summary["student_count_max"], len(students_by_name))
        class_summary["assessments"][assessment_id] = len(students_by_name)
        summary["assessments"][assessment_id] = {
            "class_id": class_id,
            "exam": exam,
            "score_file": path.name,
            "student_count": len(students_by_name),
            "score_rows": len(scores),
            "item_count": len(items),
            "answer_cards": card_stats,
            "scores_csv": rel_to_repo(score_path),
        }

    violations = scan_privacy(written, all_identity_terms, all_numeric_ids)
    summary["privacy_violations"] = violations
    summary_path = out_dir / "preprocess_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    written.append(summary_path)
    if violations:
        raise StudentDataError(f"privacy check failed for normalized outputs: {violations[:3]}")

    summary["written"] = [rel_to_repo(path) for path in written]
    if args.json:
        print_json(summary)
    else:
        print(f"Wrote {len(written)} normalized files to {out_dir}")
        print(json.dumps(summary["assessments"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_guard(run))
