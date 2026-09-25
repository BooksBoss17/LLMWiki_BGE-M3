# B站视频流时长不匹配 / 抽帧失败排查

适用场景：原件 MP4 的 `ffprobe format.duration` 与音频时长正常，但 `v:0` 视频流 `duration` / `nb_frames` 只覆盖前几十秒；60s 后抽帧报 H.264 `Invalid NAL unit size`、`missing picture in access unit`、`Output file is empty`，导致关键帧无法从 MP4 抽取。

## 诊断

```bash
ffprobe -v error -show_entries format=duration \
  -show_entries stream=index,codec_type,codec_name,width,height,duration,nb_frames \
  -of default=noprint_wrappers=1 input.mp4
```

若 `format.duration≈完整视频时长`，但 `stream video duration` 明显偏短（如 60s），说明合并/下载得到的 MP4 视频轨不完整或损坏；不要继续反复用同一个 MP4 抽 60s 后的帧。

## 处理

1. 保留原件 MP4/txt/json（不要删除可用转写产物）。
2. 重新获取同 BV/cid 的 DASH 视频流，优先 `avc1`。
3. 下载视频 m4s 时优先用 `curl -L --fail --retry 3 --retry-all-errors`，并带 `User-Agent`、`Referer`、`Origin`；避免 Python `urllib` 读到短文件却未报错。
4. 对重新下载的视频 m4s 再跑 `ffprobe`，确认视频流时长完整。
5. 用该临时视频 m4s 抽帧；抽帧完成后删除 raw/media 下的 `_tmp*.m4s`。

示例：

```bash
curl --http1.1 -L --fail --retry 3 --retry-all-errors \
  -H 'User-Agent: Mozilla/5.0' \
  -H 'Referer: https://www.bilibili.com/video/<BV>' \
  -H 'Origin: https://www.bilibili.com' \
  -o _tmp_<BV>_video.m4s '<dash_video_url>'

ffprobe -v error -show_entries format=duration \
  -show_entries stream=index,codec_type,codec_name,width,height,duration \
  -of default=noprint_wrappers=1 _tmp_<BV>_video.m4s

ffmpeg -hide_banner -loglevel error -y -ss <time> -i _tmp_<BV>_video.m4s \
  -frames:v 1 -q:v 2 -pix_fmt yuvj420p -strict unofficial \
  media/keyframe_XX_<time>s.jpg
```

## 验证

- `media/keyframe_*.jpg` 数量符合计划。
- 每张 jpg 文件大小非 0，VLM 能读取。
- raw 目录无 `.m4s/.wav/_tmp/_compact` 临时文件。
- 在 VLM 判断字幕/按钮遮挡关键内容时，按主流程 ±5–10s 重抽，并在 `keyframe_vlm_notes.md` 记录原帧问题与采用帧。