# Bilibili non-monotonic DTS video-stream fallback

Use when `bilibili_playurl_download.py` fails a video stream under strict `ffmpeg -xerror` validation with messages like:

```text
Application provided invalid, non monotonically increasing dts to muxer in stream 0
```

## What this means

Some B站 DASH video streams can have malformed/non-monotonic DTS timestamps while the actual pixels still decode. The normal strict gate is correct for most imports, but this specific failure can block an otherwise usable official/original teaching video.

Do **not** treat the failure as a reason to skip the BV immediately. Use a bounded fallback and still keep final MP4 gates strict.

## Fallback pattern

1. Keep the failed `.m4s` in the task `download_work/` for audit.
2. Confirm the video stream decodes without `-xerror`:

```bash
ffmpeg -v error -i <BV>_video.m4s -map 0:v:0 -f null -
```

If this exits non-zero with real decode corruption, re-fetch another video stream/mirror instead of continuing.

3. Re-fetch the audio stream with normal strict audio decode checking.
4. Try remuxing with generated timestamps:

```bash
ffmpeg -y -v error -fflags +genpts -i <BV>_video.m4s -i <BV>_audio.m4s -c:v copy -c:a copy <UP>_<BV>.mp4
```

5. If copy remux still fails, re-encode only the video track and copy audio:

```bash
ffmpeg -y -v error -fflags +genpts -i <BV>_video.m4s -i <BV>_audio.m4s \
  -c:v libx264 -preset veryfast -crf 20 -c:a copy <UP>_<BV>.mp4
```

6. Final acceptance gates remain strict:

- `ffprobe` format/video/audio durations match expected duration within ~3–5 seconds.
- `ffmpeg -v error -xerror -i output.mp4 -map 0:a:0 -f null -` passes.
- A few representative frame extractions after the DTS-warning region succeed.
- Record the fallback method in task status JSON.

## When not to use

Do not use this for:

- Missing/incomplete video stream duration.
- H.264 decode errors that persist without `-xerror`.
- Audio corruption or partial AAC. Use the audio partial fallback instead.
- Videos with unresolved copyright/source ambiguity.
