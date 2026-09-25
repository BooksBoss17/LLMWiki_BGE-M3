# Bilibili import integrity lessons (2026-07-02)

Session pattern: UP-丁 batch import exposed media files whose `ffprobe format.duration` looked correct while the video or audio stream was damaged.

## Durable checks

1. **Do not trust container duration alone.** Check all three durations:
   - `format.duration`
   - first video stream duration (`v:0`)
   - first audio stream duration (`a:0`)
2. **Decode audio as a gate**, not just probe metadata:

```bash
ffmpeg -v error -i output.mp4 -map 0:a:0 -f null -
```

Any stderr means the file is suspect even if exit code is 0.

3. **Sample late video frames** before accepting a merged MP4:

```bash
ffmpeg -v error -ss <time-near-end> -i output.mp4 -frames:v 1 -f null -
```

This catches cases where `format/audio` are full length but `v:0` only lasts ~60s.

## Redownload rule

When an MP4/m4s is corrupt or stream durations mismatch:

- Move bad MP4/m4s to `tmp/tasks/.../corrupt/` for audit (never leave `_tmp/` in the knowledge-base root).
- Fetch a fresh Bilibili playurl.
- Download from zero. Do **not** `curl -C -` resume old partial m4s after link expiry; it can append bytes from a different CDN object and create a container that probes but decodes badly.
- Prefer avc1 video stream for ffmpeg compatibility.
- Keep API and media on the same network route. Default to direct for both; if direct fails, retry both through the system proxy.
- Probe each base/backup mirror with a 2 MiB HTTP Range request and choose by measured throughput. Require HTTP 206, a valid Content-Range and complete requested bytes.
- Use curl with Bilibili headers:

```bash
curl -L --fail --retry 6 --retry-delay 2 --retry-all-errors \
  -H 'User-Agent: Mozilla/5.0 ...' \
  -H 'Referer: https://www.bilibili.com/video/BV...' \
  -H 'Origin: https://www.bilibili.com' \
  -o stream.m4s '<dash-url>'
```

## Windows path rule

Hermes terminal on Windows uses MSYS bash, so `/c/Users/...` works at the shell boundary. But native Windows Python interprets `Path('/c/Users/...')` as `\c\Users\...` and fails. Inside Python scripts, use `C:/Users/...` or `C:\\Users\\...` paths.

Run `scripts/normalize_video_queue.py` before download so a PowerShell single-object queue becomes a JSON array and titles receive portable `storage_title` values. `--ascii-staging=auto` is Codex/Windows auto-detection; any agent may explicitly use `--ascii-staging=on`.

## Validator

The skill ships `scripts/validate_video_import.py` to automate these gates for raw/Wiki/RAG stages. Use it after raw generation and again after Wiki+RAG sync.
