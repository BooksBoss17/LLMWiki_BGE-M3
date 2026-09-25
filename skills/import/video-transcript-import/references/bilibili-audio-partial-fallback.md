# B站音频流 partial file / curl(18) 处理经验

## 触发信号

下载 DASH 流时视频流完整，但音频流反复失败，日志类似：

```text
curl: (18) end response with N bytes missing
[mov,mp4,m4a] partial file
[aac] Input buffer exhausted before END element found
```

这种情况常见于某个 CDN 镜像或某个音频流对象异常。不要把残缺 `.m4s` 强行合并成 MP4，也不要为了下载速度把正式导入音频降到低码率。

## 推荐处理

1. **保留已验证完整的视频 m4s**：如果视频 m4s 已通过 `ffmpeg -v error -xerror -i video.m4s -map 0:v:0 -f null -`，可复用，不必重下视频。
2. **丢弃残缺音频 m4s**：删除失败音频文件，不使用 `curl -C -` 续传。
3. **重新获取 playurl**：不要继续使用旧签名 URL。
4. **遍历高音质音频流**：不要只试默认最高码率音频；重新取 playurl 后遍历所有满足导入质量门槛的高音质音频流，默认 `bandwidth >= 96000`。
5. **低码率只作诊断**：`30216` 或其它低码率音频只能用于人工诊断网络/CDN 问题，不能作为知识库导入通过。
6. **每次下载后单独解码门禁**：

```bash
ffmpeg -v error -xerror -i audio.m4s -map 0:a:0 -f null -
```

7. **合并后再做完整 MP4 门禁**：

```bash
ffprobe -v error -print_format json -show_streams -show_format output.mp4
ffmpeg -v error -xerror -i output.mp4 -map 0:a:0 -f null -
```

## 成功判据

- `format`、`video`、`audio` duration 与预期时长相差不超过几秒。
- 音频 m4s 与最终 MP4 的音频解码均无错误。
- 状态文件记录使用的音频 stream id、bandwidth 和 `audio_quality_policy`。
- 最终音频满足导入音质门槛；低码率诊断文件不能进入转写和入库。

## 关键原则

问题不是“B站不能下载”，而是“某个 CDN/码率对象返回残缺”。经验是：**重新取 playurl + 换高音质音频 stream + 从零下载 + 解码门禁**。如果高音质音频都失败，就保留失败状态等待重试，不进入转写和入库。
