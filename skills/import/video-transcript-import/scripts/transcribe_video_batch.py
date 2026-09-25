#!/usr/bin/env python
"""Batch transcribe local MP4 files with faster-whisper large-v3.

Run this with the Python environment that contains faster_whisper and clear any
agent-injected PYTHONPATH/PYTHONHOME. MSYS/bash example:
  env -u PYTHONPATH -u PYTHONHOME <PYTHON310>/python.exe \
    scripts/transcribe_video_batch.py --videos videos.json --input-dir ... --out-dir ... --up UP-丁

Input JSON shape:
[
  {"bvid":"BV...", "title":"...", "duration_sec":610, "up":"UP-丁"}
]
"""
from __future__ import annotations

import argparse, importlib.util, json, os, subprocess, sys, time
from pathlib import Path

SHARED_SCRIPTS = Path(__file__).resolve().parents[3] / '_shared' / 'scripts'
if str(SHARED_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SHARED_SCRIPTS))
from media_tools import media_command


def mmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f'{sec//60:02d}:{sec%60:02d}'


def valid_json(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        return isinstance(data, list) and bool(data)
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--videos', required=True)
    ap.add_argument('--input-dir', required=True, help='directory containing <up>_<bvid>.mp4')
    ap.add_argument('--out-dir', required=True, help='directory to write <up>_<bvid>.txt/json')
    ap.add_argument('--up', default=None)
    skills_root = Path(__file__).resolve().parents[3]
    resolver_path = skills_root / '_shared' / 'model-tools' / 'scripts' / 'model_runtime.py'
    spec = importlib.util.spec_from_file_location('llmwiki_shared_model_runtime', resolver_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load shared model resolver: {resolver_path}')
    resolver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(resolver)
    default_model = os.environ.get('LLMWIKI_FASTER_WHISPER_MODEL') or resolver.resolve_component('faster-whisper-large-v3')['resolved_path']
    ap.add_argument('--model', default=default_model)
    ap.add_argument('--device', default='cuda')
    ap.add_argument('--compute-type', default='float16')
    ap.add_argument('--status', default='transcribe_status.json')
    ap.add_argument('--force', action='store_true', help='retranscribe even when valid TXT/JSON already exist')
    args = ap.parse_args()

    from faster_whisper import WhisperModel

    videos = json.loads(Path(args.videos).read_text(encoding='utf-8'))
    input_dir = Path(args.input_dir); out_dir = Path(args.out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    status_path = Path(args.status)
    status = json.loads(status_path.read_text(encoding='utf-8')) if status_path.exists() else {}
    model = WhisperModel(args.model, device=args.device, compute_type=args.compute_type)

    for item in videos:
        bvid = item['bvid']; up = item.get('up') or args.up or 'UP主'
        mp4 = input_dir / f'{up}_{bvid}.mp4'
        txt_out = out_dir / f'{up}_{bvid}.txt'
        json_out = out_dir / f'{up}_{bvid}.json'
        row = status.get(bvid, {'bvid': bvid, 'mp4': str(mp4)})
        if not args.force and txt_out.exists() and valid_json(json_out):
            row.update({'ok': True, 'skipped': True, 'txt': str(txt_out), 'json': str(json_out)})
            status[bvid] = row; status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8'); continue
        if not mp4.exists():
            row.update({'ok': False, 'error': f'missing mp4 {mp4}'})
            status[bvid] = row; status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8'); continue
        wav = out_dir / f'{up}_{bvid}.wav'
        try:
            subprocess.check_call([media_command('ffmpeg'),'-y','-v','error','-i',str(mp4),'-vn','-acodec','pcm_s16le','-ar','16000','-ac','1',str(wav)])
            segments, info = model.transcribe(str(wav), language='zh', vad_filter=True)
            segs=[]; lines=[]
            for s in segments:
                text = (s.text or '').strip()
                if not text: continue
                segs.append({'start': round(float(s.start),2), 'end': round(float(s.end),2), 'text': text})
                lines.append(f'[{mmss(s.start)}-{mmss(s.end)}] {text}')
            txt_out.write_text('\n'.join(lines)+'\n', encoding='utf-8')
            json_out.write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding='utf-8')
            row.update({'ok': bool(segs), 'segments': len(segs), 'info_duration': getattr(info, 'duration', None), 'txt': str(txt_out), 'json': str(json_out)})
        except Exception as e:
            row.update({'ok': False, 'error': repr(e)})
        finally:
            wav.unlink(missing_ok=True)
            status[bvid] = row
            status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
            time.sleep(1)
    print(json.dumps({'status': status, 'ok_count': sum(1 for r in status.values() if r.get('ok'))}, ensure_ascii=False, indent=2))
    return 0 if all(r.get('ok') for r in status.values()) else 1

if __name__ == '__main__':
    raise SystemExit(main())
