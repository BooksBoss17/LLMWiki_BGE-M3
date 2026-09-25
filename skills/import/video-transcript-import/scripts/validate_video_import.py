#!/usr/bin/env python
"""Validate Bilibili video imports for LLMWiki_BGE-M3.

Checks durable gates used by video-transcript-import:
- raw/transcripts/<UP>/<import_title>/ has transcript, knowledge notes, lesson plan, VLM notes, keyframe manifest, media JPGs
- knowledge notes contain no image refs, visible timestamps, or platform/ad noise
- original MP4/TXT/JSON exist; MP4 format/video/audio durations match expected and audio decodes cleanly
- optional Wiki pages are self-contained and Wiki image assets resolve
- optional RAG metadata contains transcript chunks for each title/BV and metadata counts match
- optional current effective Wiki graph/assets check, excluding _meta/before_* backups

videos.json shape:
[
  {
    "bvid":"BV...",
    "title":"原始B站标题",
    "short_title":"导入目录/ Wiki短标题（可选）",
    "import_title":"同 short_title，优先级更高（可选）",
    "wiki_title":"Wiki 页面标题（可选）",
    "up":"UP主（可选，覆盖 --up）",
    "duration_sec":610,
    "keyframes":10
  }
]

By default, `keyframes` is the minimum accepted final frame count. Set
`keyframes_exact: true` only when an exact count is required. Use
`--sync-keyframe-count` to write the accepted media count back to the queue JSON.

Usage:
  python scripts/validate_video_import.py --kb-root <KB_ROOT> \
    --up UP-丁 --videos videos.json --wiki --rag --global-wiki --backfill-manifest
"""
from __future__ import annotations

import argparse, json, os, re, subprocess, sys
from pathlib import Path

SHARED_SCRIPTS = Path(__file__).resolve().parents[3] / '_shared' / 'scripts'
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))
from media_tools import media_command

TMP_PATTERNS = ['*.m4s', '*.wav', '*_compact.txt', '*_tmp_*']
AD_NOISE_RE = re.compile(r'一键三连|点赞|投币|转发|收藏|下期再见|拜拜|关注账号|关注老师|关注我')
TIMESTAMP_RE = re.compile(r'\[[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?(?:-[0-9]{1,2}:[0-9]{2}(?::[0-9]{2})?)?\]')
IMAGE_RE = re.compile(r'!\[[^\]]*\]\(([^)]+)\)')
BODY_RAW_RE = re.compile(r'\^\[\.\./raw/|##\s*raw 原文|\[查看\]\(\.\./raw|\.\./raw/[^\s)]+\.md')


def media_tool_text_kwargs() -> dict:
    kwargs = {'text': True, 'stdout': subprocess.PIPE, 'stderr': subprocess.PIPE}
    if sys.platform.startswith('win') and (os.environ.get('CODEX_SHELL') or os.environ.get('CODEX_THREAD_ID')):
        kwargs.update({'encoding': 'utf-8', 'errors': 'replace'})
    return kwargs


def import_title(video: dict) -> str:
    return str(video.get('import_title') or video.get('short_title') or video['title'])


def wiki_title(video: dict) -> str:
    return str(video.get('wiki_title') or video.get('import_title') or video.get('short_title') or video['title'])


def video_up(video: dict, fallback: str) -> str:
    return str(video.get('up') or fallback)


def strip_frontmatter(txt: str) -> str:
    if txt.startswith('---') and txt.count('---') >= 2:
        return txt.split('---', 2)[2]
    return txt


def frontmatter_type(path: Path, expected: str) -> bool:
    if not path.exists(): return False
    txt = path.read_text(encoding='utf-8', errors='ignore')
    if not txt.startswith('---') or txt.count('---') < 2: return False
    fm = txt.split('---', 2)[1]
    return bool(re.search(rf'^type:\s*["\']?{re.escape(expected)}["\']?\s*$', fm, re.M))


def ffprobe_durations(path: Path) -> tuple[float, float, float]:
    p = subprocess.run([media_command('ffprobe'),'-v','error','-print_format','json','-show_streams','-show_format',str(path)], **media_tool_text_kwargs())
    data = json.loads(p.stdout or '{}')
    fmt = float(data.get('format', {}).get('duration') or 0)
    vs = [s for s in data.get('streams', []) if s.get('codec_type') == 'video']
    au = [s for s in data.get('streams', []) if s.get('codec_type') == 'audio']
    vdur = float(vs[0].get('duration') or 0) if vs else 0.0
    adur = float(au[0].get('duration') or 0) if au else 0.0
    return fmt, vdur, adur


def audio_decodes_clean(path: Path) -> bool:
    p = subprocess.run([media_command('ffmpeg'),'-v','error','-xerror','-i',str(path),'-map','0:a:0','-f','null','-'], **media_tool_text_kwargs())
    return p.returncode == 0 and not p.stderr.strip()


def original_candidates(kb: Path, up: str, title: str, bvid: str, ext: str) -> list[Path]:
    base = kb/'source-library'/'transcripts'
    names = [f'{up}_{bvid}.{ext}', f'{up}_{title}.{ext}', f'{title}.{ext}']
    out: list[Path] = []
    for name in names:
        out.append(base/name)       # current flat convention
        out.append(base/up/name)    # older/up-subdir convention
    # de-duplicate while preserving order
    seen=set(); uniq=[]
    for p in out:
        s=str(p)
        if s not in seen:
            seen.add(s); uniq.append(p)
    return uniq


def first_existing(paths: list[Path]) -> Path | None:
    for p in paths:
        if p.exists(): return p
    return None


def backfill_keyframe_manifest(raw_dir: Path, video: dict) -> None:
    media = raw_dir / 'media'
    frames = []
    for jpg in sorted(media.glob('keyframe_*.jpg')) if media.exists() else []:
        m = re.search(r'keyframe_(\d+)_(\d+)s\.jpg$', jpg.name)
        frames.append({
            'index': int(m.group(1)) if m else None,
            'time_s': int(m.group(2)) if m else None,
            'file': f'media/{jpg.name}',
            'reason': 'final keyframe; see keyframe_vlm_notes.md',
            'vlm_complete': True,
        })
    obj = {
        'bvid': video.get('bvid'), 'title': import_title(video),
        'generated_by': 'validate_video_import.py --backfill-manifest',
        'note': 'Backfilled from final media/keyframe_*.jpg files because VLM notes and final frames existed but keyframe_candidates.json was missing.',
        'vlm_notes': 'keyframe_vlm_notes.md', 'vlm_all_complete': True,
        'final_keyframes': frames, 'retries': [],
    }
    (raw_dir/'keyframe_candidates.json').write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding='utf-8')


def validate_raw(kb: Path, fallback_up: str, video: dict, backfill_manifest: bool=False, sync_keyframe_count: bool=False) -> list[str]:
    issues=[]
    title=import_title(video); bvid=video['bvid']; up=video_up(video, fallback_up); expected=float(video['duration_sec'])
    raw_dir = kb/'raw'/'transcripts'/up/title
    if not raw_dir.exists(): return [f'{title}: raw dir missing']
    files = {
        'transcript': raw_dir/f'{title}.md',
        'knowledge': raw_dir/f'{title}_知识笔记.md',
        'lesson': raw_dir/f'{title}_教学简案.md',
        'vlm': raw_dir/'keyframe_vlm_notes.md',
        'manifest': raw_dir/'keyframe_candidates.json',
    }
    for name, path in files.items():
        if not path.exists():
            if name == 'manifest' and backfill_manifest and files['vlm'].exists() and (raw_dir/'media').exists():
                backfill_keyframe_manifest(raw_dir, video)
            else:
                issues.append(f'{title}: missing {name} {path.name}')
        elif path.stat().st_size < 200: issues.append(f'{title}: suspiciously small {name} {path.stat().st_size}')
    if not frontmatter_type(files['transcript'], 'transcript'): issues.append(f'{title}: transcript frontmatter type missing/bad')
    if not frontmatter_type(files['knowledge'], 'knowledge_notes'): issues.append(f'{title}: knowledge frontmatter type missing/bad')
    if not frontmatter_type(files['lesson'], 'lesson_plan'): issues.append(f'{title}: lesson frontmatter type missing/bad')
    jpgs = sorted((raw_dir/'media').glob('keyframe_*.jpg')) if (raw_dir/'media').exists() else []
    expected_kf = video.get('keyframes')
    exact_kf = bool(video.get('keyframes_exact') or video.get('keyframe_count_mode') == 'exact')
    min_kf = int(video.get('min_keyframes') or (int(expected_kf) if expected_kf is not None else 7))
    if len(jpgs) < min_kf:
        issues.append(f'{title}: too few keyframes ({len(jpgs)} < {min_kf})')
    elif expected_kf is not None and exact_kf and len(jpgs) != int(expected_kf):
        issues.append(f'{title}: keyframe count {len(jpgs)} != exact expected {expected_kf}')
    elif sync_keyframe_count and expected_kf is not None and len(jpgs) != int(expected_kf):
        video['keyframes'] = len(jpgs)
        video['keyframes_synced_by_validator'] = True
    for jpg in jpgs:
        if jpg.stat().st_size < 5000: issues.append(f'{title}: tiny keyframe {jpg.name}')
    for jpg in raw_dir.glob('*.jpg'):
        issues.append(f'{title}: stray raw jpg outside media {jpg.name}')
    if (raw_dir/'extra_candidates').exists(): issues.append(f'{title}: extra_candidates residual')
    if files['vlm'].exists():
        vlm = files['vlm'].read_text(encoding='utf-8', errors='ignore')
        if '不完整' in vlm and not any(x in vlm for x in ['已重抽','重抽后','最终','采用','剔除','不在三层文件中引用']):
            issues.append(f'{title}: unresolved incomplete VLM note')
    if files['knowledge'].exists():
        body = strip_frontmatter(files['knowledge'].read_text(encoding='utf-8', errors='ignore'))
        if IMAGE_RE.search(body): issues.append(f'{title}: knowledge notes contain image reference')
        if TIMESTAMP_RE.search(body): issues.append(f'{title}: knowledge notes contain visible timestamp')
        if AD_NOISE_RE.search(body): issues.append(f'{title}: knowledge notes contain platform/ad noise')
    if files['lesson'].exists():
        body = strip_frontmatter(files['lesson'].read_text(encoding='utf-8', errors='ignore'))
        if not re.search(r'教学|环节|策略|目标|重点', body): issues.append(f'{title}: lesson plan lacks teaching structure keywords')
    for ext in ['mp4','txt','json']:
        if first_existing(original_candidates(kb, up, title, bvid, ext)) is None:
            issues.append(f'{title}: missing original {ext}')
    mp4 = first_existing(original_candidates(kb, up, title, bvid, 'mp4'))
    if mp4:
        fmt, vdur, adur = ffprobe_durations(mp4)
        if abs(fmt-expected)>5: issues.append(f'{title}: format duration {fmt:.2f} != {expected}')
        if abs(vdur-expected)>5: issues.append(f'{title}: video duration {vdur:.2f} != {expected}')
        if abs(adur-expected)>5: issues.append(f'{title}: audio duration {adur:.2f} != {expected}')
        if not audio_decodes_clean(mp4): issues.append(f'{title}: audio decode not clean')
    js = first_existing(original_candidates(kb, up, title, bvid, 'json'))
    if js:
        try:
            data = json.loads(js.read_text(encoding='utf-8'))
            if not isinstance(data, list) or len(data) < 5: issues.append(f'{title}: transcript json too short/invalid')
        except Exception as e: issues.append(f'{title}: transcript json parse error {e}')
    return issues


def validate_wiki(kb: Path, video: dict) -> list[str]:
    title=wiki_title(video); concepts=kb/'LLMWiki'/'concepts'; issues=[]
    for suffix in ['', '-知识笔记', '-教学简案']:
        p = concepts/f'视频-{title}{suffix}.md'
        if not p.exists(): issues.append(f'{title}: missing wiki page {p.name}'); continue
        txt = p.read_text(encoding='utf-8', errors='ignore')
        body = strip_frontmatter(txt)
        if len(txt.encode('utf-8')) > 20000: issues.append(f'{title}: wiki page over 20KB {p.name}')
        if BODY_RAW_RE.search(body): issues.append(f'{title}: visible raw pointer in {p.name}')
        for m in IMAGE_RE.finditer(body):
            rel=m.group(1).split('#')[0]
            if rel.startswith(('http://','https://','data:')): continue
            if not (p.parent/rel).resolve().exists(): issues.append(f'{title}: missing wiki asset {rel}')
    return issues


def validate_rag(kb: Path, videos: list[dict]) -> list[str]:
    meta_path = kb/'BGE-M3'/'output'/'metadata.json'; issues=[]
    if not meta_path.exists(): return ['RAG metadata.json missing']
    meta = json.loads(meta_path.read_text(encoding='utf-8'))
    chunks = meta.get('chunks', meta if isinstance(meta, list) else []) if isinstance(meta, (dict, list)) else []
    if isinstance(meta, dict):
        if meta.get('total_chunks') != len(chunks): issues.append(f"RAG total_chunks mismatch {meta.get('total_chunks')} != {len(chunks)}")
        if meta.get('total_vectors') != len(chunks): issues.append(f"RAG total_vectors mismatch {meta.get('total_vectors')} != {len(chunks)}")
    for v in videos:
        keys = [import_title(v), wiki_title(v), str(v.get('title','')), str(v.get('bvid',''))]
        blob_keys = [k for k in keys if k]
        hits=[c for c in chunks if c.get('source_type') == 'transcript' and any(k in str(c.get('source','')) or k in json.dumps(c,ensure_ascii=False) for k in blob_keys)]
        if not hits: issues.append(f"{import_title(v)}: no RAG transcript chunks")
    return issues


def validate_global_wiki(kb: Path) -> list[str]:
    wiki = kb/'LLMWiki'; issues=[]
    if not wiki.exists(): return ['LLMWiki directory missing']
    pages=[]
    for p in wiki.rglob('*.md'):
        rel=p.relative_to(wiki).parts
        # _meta contains audit notes and historical backups, not active Wiki graph content.
        # Do not validate image assets or wikilinks inside _meta backups as current-page issues.
        if '_meta' in rel:
            continue
        pages.append(p)
    page_names={p.stem for p in pages}
    for p in pages:
        txt=p.read_text(encoding='utf-8', errors='ignore'); body=strip_frontmatter(txt); rel=str(p.relative_to(wiki))
        if p.parent.name == 'concepts' and len(txt.encode('utf-8')) > 20000: issues.append(f'global: concepts page over 20KB {rel}')
        if BODY_RAW_RE.search(body): issues.append(f'global: visible raw pointer {rel}')
        for m in IMAGE_RE.finditer(body):
            target=m.group(1).split('#')[0]
            if target.startswith(('http://','https://','data:')): continue
            if not (p.parent/target).resolve().exists(): issues.append(f'global: missing asset {rel} -> {target}')
        for m in re.finditer(r'\[\[([^\]|#]+)', body):
            name=m.group(1).strip()
            if name and name != 'wikilinks' and name not in page_names: issues.append(f'global: broken wikilink {rel} -> [[{name}]]')
    return issues


def main() -> int:
    ap=argparse.ArgumentParser()
    ap.add_argument('--kb-root', required=True)
    ap.add_argument('--up', required=True, help='fallback UP name; per-video "up" overrides this')
    ap.add_argument('--videos', required=True, help='JSON list of {bvid,title,duration_sec,short_title/import_title?,wiki_title?,up?,keyframes?}')
    ap.add_argument('--wiki', action='store_true')
    ap.add_argument('--rag', action='store_true')
    ap.add_argument('--global-wiki', action='store_true')
    ap.add_argument('--backfill-manifest', action='store_true')
    ap.add_argument('--sync-keyframe-count', action='store_true', help='write actual accepted keyframe count back to videos JSON when actual count is above the minimum')
    args=ap.parse_args()
    kb=Path(args.kb_root)
    videos_path = Path(args.videos)
    videos=json.loads(videos_path.read_text(encoding='utf-8-sig'))
    original_videos_json = json.dumps(videos, ensure_ascii=False, sort_keys=True)
    issues=[]
    for v in videos: issues.extend(validate_raw(kb, args.up, v, args.backfill_manifest, args.sync_keyframe_count))
    roots = [kb/'raw'/'transcripts', kb/'source-library'/'transcripts']
    for up in {video_up(v, args.up) for v in videos}:
        roots.extend([kb/'raw'/'transcripts'/up, kb/'原件'/'transcripts'/up])
    seen_roots=set()
    for root in roots:
        if str(root) in seen_roots: continue
        seen_roots.add(str(root))
        if root.exists():
            for pat in TMP_PATTERNS:
                for p in root.rglob(pat): issues.append(f'temp residual: {p}')
    if args.wiki:
        for v in videos: issues.extend(validate_wiki(kb, v))
    if args.rag: issues.extend(validate_rag(kb, videos))
    if args.global_wiki: issues.extend(validate_global_wiki(kb))
    if args.sync_keyframe_count and json.dumps(videos, ensure_ascii=False, sort_keys=True) != original_videos_json:
        videos_path.write_text(json.dumps(videos, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    result={'issue_count': len(issues), 'issues': issues}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if issues else 0

if __name__ == '__main__':
    sys.exit(main())
