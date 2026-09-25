# B站下载网络与 CDN 选择

## 适用范围

用于 `scripts/bilibili_playurl_download.py` 和 `scripts/probe_bilibili_cdn.py`。网络策略只约束当前 Python/curl 子进程，不修改 Windows 全局 VPN 或代理。

## 一致选路

- 默认：`--network-mode direct --network-fallback system-proxy`。
- `direct`：Python API 使用空 `ProxyHandler`，curl 使用 `--noproxy '*'`。
- `system-proxy`：从系统 HTTP/HTTPS 代理中解析一个 URL，Python API 与 curl 都使用该 URL。
- `explicit-proxy`：必须同时传 `--proxy-url`；该值只用于此模式。
- 直连的 API、playurl、CDN 探测或媒体下载失败后，整个网络尝试才切换系统代理。禁止复用代理 API 得到的签名 URL 再让媒体直连。

## CDN 测速

每个待尝试 DASH stream 的 base/backup URL 都下载 `0-2097151` Range，单镜像默认最多 8 秒。只有满足以下条件才有效：

- HTTP `206`；
- 有合法 `Content-Range`；
- 实收字节数与 Content-Range 完全一致；
- curl 正常结束。

同一 stream 按 `speed_bytes_sec` 从高到低尝试。所有镜像均无效时刷新一次 playurl；再次失败后才进入网络 fallback。不要按 `bilivideo.com`、`akamaized.net` 域名直接猜快慢。

差分测速示例：

```powershell
python skills/import/video-transcript-import/scripts/probe_bilibili_cdn.py --bvid BVxxxxxxxxxx --network-mode direct --network-fallback none
```

输出只能包含 host、remote IP、字节数、耗时、吞吐和选择结果，不得包含签名 URL。

## 超时与质量

- `--per-video-timeout-sec 0` 是默认值，表示不设 API + 探测 + 下载 + fallback + 合并的总 deadline。
- 显式非零 deadline 覆盖全部阶段；超时状态写 `timeout_stage`。
- 无总 deadline 时，媒体 curl 不设 `--max-time`，只保留低于 1 KiB/s 持续 120 秒的无吞吐中止。
- 3000 秒视频时长门禁独立存在，不得把“下载慢”当成“视频超长”。
- 音频仍必须 `bandwidth >= 96000`；网络变慢不能触发低音质入库。

## 队列与路径

正式下载前先规范化队列：

```powershell
python skills/import/video-transcript-import/scripts/normalize_video_queue.py --input videos_source.json --output videos.json
```

输入可以是单对象或数组，输出始终是数组。`original_title` 保留原题名；`storage_title`、`import_title`、`wiki_title` 会替换 Windows 禁止字符、控制字符、尾部空格/句点和保留设备名。

`--ascii-staging=auto` 只对 Codex + Windows + 非 ASCII 路径自动生效。Hermes、Claude、Gemini 等 agent 如遇本机路径工具兼容问题，可显式使用 `--ascii-staging=on`；旧 `--codex-ascii-staging` 仍是兼容别名。

## 状态门禁

`download_status.json` 至少检查：

- `network_policy.requested_mode`、`actual_mode`、`fallback_used`；
- 每次网络尝试的 mode 与脱敏 proxy；
- `cdn_probe` 中 video/audio 的 host、remote IP、bytes、elapsed、speed、selected；
- `audio_quality_acceptable=true`；
- format/video/audio 三时长和最终音频解码通过。

状态、异常摘要和日志不得包含代理账号密码或带 query 的签名 URL。
