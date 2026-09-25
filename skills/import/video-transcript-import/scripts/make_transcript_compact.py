#!/usr/bin/env python
"""Create bounded per-video transcript files for context-budgeted imports.

Inputs can be a queue JSON plus originals directory, explicit transcript JSON
paths, or both. The script writes only task-state summaries under
per_video/<BV>; it never writes raw, Wiki, or RAG content.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
VISUAL_CUE_RE = re.compile(
    r"如图|看图|图像|图象|画面|板书|屏幕|PPT|模型|示意图|受力图|"
    r"电路图|图中|这个图|这张图|公式|推导|表格|坐标|图线|图像上|"
    r"实验装置|装置|动画|题干|例题|练习|画一下|写成|得到"
)
ASR_RISK_TERMS = {
    "剪斜": "简谐",
    "电视能": "电势能",
    "能词定律": "楞次定律",
    "词通量": "磁通量",
    "云变数": "匀变速",
    "做正弓": "做正功",
    "做公": "做功",
    "作公": "做功",
    "复攻": "负功",
    "灵势面": "零势面",
    "参照物": "参考系语境下需核对",
    "磨擦": "摩擦",
    "洛伦兹": "洛伦兹力语境下需核对公式",
}
NOISE_ONLY_RE = re.compile(r"^[\[\]【】()（）0-9:：\s,.，。!?！？、…·~\-—_]+$")
PLATFORM_NOISE_RE = re.compile(
    r"^(请不吝点赞|欢迎订阅点赞|点赞订阅|订阅\s*转发\s*打赏|"
    r"一键三连|关注账号|关注老师|关注我|下期再见|拜拜)$"
)
PLATFORM_NOISE_KEYWORDS = (
    "请不吝",
    "点赞",
    "订阅",
    "一键三连",
    "投币",
    "转发",
    "打赏",
    "关注账号",
    "关注老师",
    "关注我",
    "下期再见",
    "拜拜",
)
COMPACT_DISPLAY_REPLACEMENTS = {
    "做公": "做功",
    "作公": "做功",
    "复攻": "负功",
    "做正弓": "做正功",
    "数值方向": "竖直方向",
}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def read_json(path: Path) -> Any:
    data = path.read_bytes()
    errors = []
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return json.loads(data.decode(encoding))
        except Exception as exc:
            errors.append(f"{encoding}: {exc}")
    raise ValueError(f"cannot parse JSON {path}: {'; '.join(errors[-2:])}")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def mmss(seconds: float) -> str:
    sec = max(0, int(round(seconds)))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def one_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def compact_display_text(text: str) -> str:
    text = one_line(text)
    for wrong, right in COMPACT_DISPLAY_REPLACEMENTS.items():
        text = text.replace(wrong, right)
    return text


def should_drop_segment_text(text: str) -> bool:
    text = one_line(text)
    if not text:
        return True
    if NOISE_ONLY_RE.fullmatch(text):
        return True
    if len(text) <= 60:
        if PLATFORM_NOISE_RE.search(text):
            return True
        keyword_hits = sum(1 for keyword in PLATFORM_NOISE_KEYWORDS if keyword in text)
        if keyword_hits >= 2:
            return True
    # Faster-whisper may hallucinate repeated bracket/time shells in silent sections.
    cjk_or_latin = re.search(r"[\u4e00-\u9fffA-Za-z]", text)
    return cjk_or_latin is None


def truncate(text: str, limit: int) -> str:
    text = one_line(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def normalize_segments(data: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    if isinstance(data, dict):
        if isinstance(data.get("segments"), list):
            data = data["segments"]
        elif isinstance(data.get("transcript"), list):
            data = data["transcript"]
    if not isinstance(data, list):
        raise ValueError("transcript JSON must be a list or contain segments[]")

    segments: list[dict[str, Any]] = []
    stats = {
        "raw_segment_count": 0,
        "kept_segment_count": 0,
        "dropped_noise_segment_count": 0,
    }
    for idx, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        stats["raw_segment_count"] += 1
        try:
            start = float(item.get("start", item.get("s", item.get("begin", 0))) or 0)
            end = float(item.get("end", item.get("e", item.get("finish", start))) or start)
        except (TypeError, ValueError):
            stats["dropped_noise_segment_count"] += 1
            continue
        raw_text = one_line(item.get("text", item.get("sentence", item.get("content", ""))))
        text = compact_display_text(raw_text)
        if should_drop_segment_text(text):
            stats["dropped_noise_segment_count"] += 1
            continue
        if end < start:
            end = start
        segments.append({"start": start, "end": end, "text": text, "raw_text": raw_text, "index": idx})
        stats["kept_segment_count"] += 1
    return segments, stats


def load_queue(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    data = read_json(path)
    if isinstance(data, dict):
        for key in ("videos", "queue", "items"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
    if not isinstance(data, list):
        raise ValueError(f"queue must be a JSON array or contain videos[]: {path}")
    return [dict(item) for item in data if isinstance(item, dict)]


def infer_bvid(path: Path) -> str:
    match = BV_RE.search(path.stem)
    if match:
        return match.group(0)
    return path.stem[:60]


def explicit_items(paths: list[Path], fallback_up: str | None) -> list[dict[str, Any]]:
    items = []
    for path in paths:
        bvid = infer_bvid(path)
        stem = path.stem
        title = stem
        up = fallback_up
        if "_" in stem:
            prefix, suffix = stem.split("_", 1)
            if not up:
                up = prefix
            title = suffix if not BV_RE.fullmatch(suffix) else suffix
        items.append(
            {
                "bvid": bvid,
                "title": title,
                "import_title": title,
                "up": up or "UP主",
                "transcript_json": str(path),
            }
        )
    return items


def attach_explicit_transcripts(items: list[dict[str, Any]], paths: list[Path]) -> None:
    by_bvid = {infer_bvid(path): path for path in paths}
    remaining = list(paths)
    for item in items:
        if any(item.get(key) for key in ("transcript_json", "json", "transcript_path")):
            continue
        bvid = str(item.get("bvid") or "")
        path = by_bvid.get(bvid)
        if path is None and remaining:
            path = remaining.pop(0)
        if path is not None:
            item["transcript_json"] = str(path)


def transcript_candidates(item: dict[str, Any], originals_dir: Path | None) -> list[Path]:
    candidates: list[Path] = []
    for key in ("transcript_json", "json", "transcript_path"):
        value = item.get(key)
        if value:
            candidates.append(Path(value))
    if originals_dir is None:
        return candidates

    bvid = str(item.get("bvid") or "")
    up = str(item.get("up") or "")
    names = []
    for value in (
        bvid,
        item.get("import_title"),
        item.get("short_title"),
        item.get("title"),
        item.get("wiki_title"),
    ):
        if value:
            names.append(str(value))
    for name in names:
        if up:
            candidates.append(originals_dir / f"{up}_{name}.json")
            candidates.append(originals_dir / up / f"{up}_{name}.json")
            candidates.append(originals_dir / up / f"{name}.json")
        candidates.append(originals_dir / f"{name}.json")
    return candidates


def resolve_transcript(item: dict[str, Any], originals_dir: Path | None) -> Path | None:
    seen: set[str] = set()
    for path in transcript_candidates(item, originals_dir):
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        if path.exists():
            return path
    return None


def compact_markdown(
    item: dict[str, Any],
    transcript_path: Path,
    segments: list[dict[str, Any]],
    bucket_sec: int,
    bucket_char_limit: int,
    max_buckets: int,
) -> str:
    title = item.get("import_title") or item.get("short_title") or item.get("title") or item.get("bvid")
    bvid = item.get("bvid")
    up = item.get("up") or "UP主"
    duration = max([float(s["end"]) for s in segments], default=0)
    bucketed: dict[int, list[dict[str, Any]]] = {}
    for seg in segments:
        bucketed.setdefault(int(float(seg["start"]) // bucket_sec), []).append(seg)

    lines = [
        f"# {title} transcript compact",
        "",
        f"- bvid: {bvid}",
        f"- up: {up}",
        f"- transcript_json: {transcript_path.as_posix()}",
        f"- segment_count: {len(segments)}",
        f"- raw_segment_count: {int(item.get('_raw_segment_count') or len(segments))}",
        f"- kept_segment_count: {int(item.get('_kept_segment_count') or len(segments))}",
        f"- dropped_noise_segments: {int(item.get('_dropped_noise_segment_count') or 0)}",
        f"- transcript_duration: {mmss(duration)}",
        f"- bucket_sec: {bucket_sec}",
        "",
        "> This compact is for planning only. Use the original transcript JSON for final transcript fidelity.",
        "",
    ]
    for index in sorted(bucketed)[:max_buckets]:
        start = index * bucket_sec
        end = start + bucket_sec
        pieces = [f"[{mmss(seg['start'])}] {seg['text']}" for seg in bucketed[index]]
        body = truncate(" ".join(pieces), bucket_char_limit)
        lines.extend([f"## {mmss(start)}-{mmss(end)}", "", body, ""])
    if len(bucketed) > max_buckets:
        lines.append(f"_Truncated after {max_buckets} buckets; original transcript remains in JSON._")
    return "\n".join(lines).rstrip() + "\n"


def visual_cues_markdown(
    item: dict[str, Any],
    segments: list[dict[str, Any]],
    max_lines: int,
    line_char_limit: int,
) -> str:
    title = item.get("import_title") or item.get("title") or item.get("bvid")
    hits = [seg for seg in segments if VISUAL_CUE_RE.search(seg["text"])]
    lines = [f"# {title} visual cue lines", ""]
    if not hits:
        lines.append("未发现显式视觉提示关键词；worker 需要按章节结构补选候选帧。")
        return "\n".join(lines).rstrip() + "\n"
    for seg in hits[:max_lines]:
        lines.append(f"- [{mmss(seg['start'])}] {truncate(seg['text'], line_char_limit)}")
    if len(hits) > max_lines:
        lines.append(f"- ... truncated {len(hits) - max_lines} additional cue lines")
    return "\n".join(lines).rstrip() + "\n"


def asr_risk_markdown(
    item: dict[str, Any],
    segments: list[dict[str, Any]],
    max_hits_per_term: int,
    line_char_limit: int,
) -> str:
    title = item.get("import_title") or item.get("title") or item.get("bvid")
    lines = [f"# {title} ASR risk notes", ""]
    found = False
    for wrong, expected in ASR_RISK_TERMS.items():
        hits = [seg for seg in segments if wrong in seg.get("raw_text", seg["text"])]
        if not hits:
            continue
        found = True
        lines.append(f"## {wrong} -> {expected}")
        for seg in hits[:max_hits_per_term]:
            lines.append(f"- [{mmss(seg['start'])}] {truncate(seg['text'], line_char_limit)}")
        if len(hits) > max_hits_per_term:
            lines.append(f"- ... truncated {len(hits) - max_hits_per_term} additional hits")
        lines.append("")
    if not found:
        lines.append("未命中内置 ASR 风险词；仍需在知识笔记阶段校对公式、专名和物理量。")
    return "\n".join(lines).rstrip() + "\n"


def process_item(item: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    transcript_path = resolve_transcript(item, Path(args.originals_dir) if args.originals_dir else None)
    bvid = str(item.get("bvid") or (infer_bvid(transcript_path) if transcript_path else "unknown"))
    out_dir = Path(args.out_dir) / bvid
    row: dict[str, Any] = {"bvid": bvid, "ok": False, "out_dir": str(out_dir)}
    if transcript_path is None:
        row["error"] = "transcript JSON not found"
        return row

    try:
        segments, segment_stats = normalize_segments(read_json(transcript_path))
        if not segments:
            raise ValueError("no transcript segments")
        duration = max(float(seg["end"]) for seg in segments)
        title = item.get("import_title") or item.get("short_title") or item.get("title") or bvid
        meta = {
            "generated_at": now_iso(),
            "bvid": bvid,
            "cid": item.get("cid"),
            "title": item.get("title") or title,
            "import_title": title,
            "wiki_title": item.get("wiki_title") or title,
            "up": item.get("up") or args.up or "UP主",
            "duration_sec_expected": item.get("duration_sec") or item.get("duration"),
            "transcript_json": str(transcript_path),
            "segment_count": len(segments),
            "raw_segment_count": segment_stats["raw_segment_count"],
            "kept_segment_count": segment_stats["kept_segment_count"],
            "dropped_noise_segment_count": segment_stats["dropped_noise_segment_count"],
            "transcript_duration_sec": round(duration, 2),
            "outputs": {
                "transcript_compact_2min": "transcript_compact_2min.md",
                "visual_cue_lines": "visual_cue_lines.md",
                "asr_risk_notes": "asr_risk_notes.md",
                "video_meta": "video_meta.json",
            },
        }
        out_dir.mkdir(parents=True, exist_ok=True)
        item["_raw_segment_count"] = segment_stats["raw_segment_count"]
        item["_kept_segment_count"] = segment_stats["kept_segment_count"]
        item["_dropped_noise_segment_count"] = segment_stats["dropped_noise_segment_count"]
        write_json(out_dir / "video_meta.json", meta)
        write_text(
            out_dir / "transcript_compact_2min.md",
            compact_markdown(
                item,
                transcript_path,
                segments,
                args.bucket_sec,
                args.bucket_char_limit,
                args.max_buckets,
            ),
        )
        write_text(
            out_dir / "visual_cue_lines.md",
            visual_cues_markdown(item, segments, args.max_cue_lines, args.line_char_limit),
        )
        write_text(
            out_dir / "asr_risk_notes.md",
            asr_risk_markdown(item, segments, args.max_hits_per_term, args.line_char_limit),
        )
        row.update({
            "ok": True,
            "segment_count": len(segments),
            "raw_segment_count": segment_stats["raw_segment_count"],
            "kept_segment_count": segment_stats["kept_segment_count"],
            "dropped_noise_segment_count": segment_stats["dropped_noise_segment_count"],
            "transcript_duration_sec": round(duration, 2),
        })
    except Exception as exc:
        row["error"] = repr(exc)
    return row


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-dir", required=True, help="task status directory under tmp/logs/legacy-task-status")
    ap.add_argument("--videos", help="queue JSON with bvid/up/title/duration_sec rows")
    ap.add_argument("--transcripts", nargs="*", default=[], help="explicit transcript JSON paths")
    ap.add_argument("--originals-dir", help="directory containing original transcript JSON files")
    ap.add_argument("--out-dir", help="per-video output root; defaults to <task-dir>/per_video")
    ap.add_argument("--up", help="fallback UP name")
    ap.add_argument("--bucket-sec", type=int, default=120)
    ap.add_argument("--bucket-char-limit", type=int, default=1600)
    ap.add_argument("--max-buckets", type=int, default=80)
    ap.add_argument("--max-cue-lines", type=int, default=120)
    ap.add_argument("--line-char-limit", type=int, default=220)
    ap.add_argument("--max-hits-per-term", type=int, default=8)
    args = ap.parse_args()

    task_dir = Path(args.task_dir)
    args.out_dir = args.out_dir or str(task_dir / "per_video")
    items = load_queue(Path(args.videos) if args.videos else None)
    explicit_paths = [Path(p) for p in args.transcripts]
    if items and explicit_paths:
        attach_explicit_transcripts(items, explicit_paths)
    if not items:
        items = explicit_items(explicit_paths, args.up)
    if not items:
        print("No videos or transcripts provided", file=sys.stderr)
        return 2

    summary = {
        "generated_at": now_iso(),
        "task_dir": str(task_dir),
        "out_dir": str(Path(args.out_dir)),
        "count": len(items),
        "videos": [process_item(item, args) for item in items],
    }
    summary["ok_count"] = sum(1 for row in summary["videos"] if row.get("ok"))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok_count"] == summary["count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
