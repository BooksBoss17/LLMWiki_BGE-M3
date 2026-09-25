# Context-Budgeted Video Import

本参考用于多视频或长视频导入。目标是让主控 agent 只读取状态摘要和 bounded per-video 文件，不把全文转写、完整 VLM 长评、完整 RAG 日志塞进聊天上下文。multi-agent 只是可选加速路径；没有 subagent/delegate 能力时也必须能按同一套状态文件串行完成。

## 1. Batch Mode 触发条件

满足任一条件就进入 context-budgeted batch mode：

- 队列有 3-6 条视频。
- 队列总时长超过 60 分钟。
- 队列超过 6 条视频。
- 用户要求继续处理某个 UP 主的大量 backlog。
- 当前上下文接近 50%-60%，继续启动新视频会引入长转写或长 VLM 状态。

批次大小：

- 1-2 条视频：允许单 agent 完整导入。
- 3-6 条视频或总时长超过 60 分钟：使用一个 active batch。
- 超过 6 条视频：拆 wave，每 wave 默认 2-4 条。
- 长视频或复杂板书专题：每 wave 降到 1-2 条。

上下文接近 50%-60% 时停止启动新视频。超过 65% 或用户反馈上下文溢出时，先写断点文件，再继续恢复。

## 2. 状态目录

每个长任务创建一个任务目录：

```text
tmp/logs/legacy-task-status/<task_id>/
  videos_full_queue.json
  videos_active_batch.json
  import_status.json
  batch_import_plan.md
  context_budget_and_delegation_plan.md
  context_compaction_card.md
  context_overflow_pause_status_latest.json
  per_video/
    <BV>/
      video_meta.json
      transcript_compact_2min.md
      visual_cue_lines.md
      chapter_plan.md
      keyframe_candidates.json
      asr_risk_notes.md
```

续接任务只能信任这些文件和 validator 输出，不能依赖聊天记录记忆。

## 3. 队列和状态 schema

`videos_full_queue.json` 和 `videos_active_batch.json` 是 JSON 数组。每行建议字段：

```json
{
  "bvid": "BV...",
  "cid": 123,
  "title": "B站原题名",
  "import_title": "raw/Wiki 使用短标题",
  "wiki_title": "Wiki 页面标题",
  "duration_sec": 610,
  "up": "UP主",
  "keyframes": 10,
  "priority": "P0",
  "status": "queued"
}
```

关键帧数量规则：

- `keyframes` 默认表示最终关键帧的最低期望数量，不要求精确相等。
- 需要精确相等时写 `keyframes_exact: true` 或 `keyframe_count_mode: "exact"`。
- 需要低于 `keyframes` 的门槛时写 `min_keyframes`。
- 最终关键帧通过 VLM 接受后，可运行 `validate_video_import.py --sync-keyframe-count` 把实际数量回写队列。

`import_status.json` 保持短小、机器可读：

```json
{
  "task_id": "video_import_xxx",
  "mode": "multi-agent | serial-agent",
  "current_wave": 1,
  "videos": {
    "BV...": {
      "download": "queued | ok | failed",
      "transcribe": "queued | ok | failed",
      "compact": "queued | ok | failed",
      "keyframes": "queued | ok | failed",
      "raw": "queued | ok | failed",
      "wiki": "queued | ok | failed",
      "rag": "pending | ok | failed",
      "issues": []
    }
  }
}
```

完整日志留在任务目录或脚本 status JSON 中。主控需要汇总时运行 `scripts/summarize_import_status.py`，不要把长日志贴进上下文。

## 4. 下载 Fallback 自动化

下载、合并、音频解码和 retry 决策只能由主控脚本执行，worker 不允许执行。

`scripts/bilibili_playurl_download.py` 先执行一致网络策略，再执行两档 profile：API、CDN 探测与媒体默认全部直连，直连失败才整体切换系统代理；不得出现 API 走代理、curl 直连的混合状态。

1. `standard`：`qn=80`、`fourk=1`，优先 avc1 视频流，音频只接受导入质量门槛内的高音质流。
2. `lowrate`：标准 profile 失败、`curl` 超时或三时长/音频解码门禁失败后自动触发；重新获取 `qn=64`、`fourk=0` playurl，优先低带宽 avc1 视频，但音频仍执行同一高音质门槛。

默认音频门槛是 `bandwidth >= 96000`。下载慢可以等待；不要为了速度把导入原件降到 `30216` 或其它低码率音频。若所有高音质音频都失败，保留 `download_status.json` 并把该 BV 标记为 `failed/retry`，不要进入转写、raw 生成或入库。`--allow-low-audio` 只用于人工诊断，低音质音频即使下载成功也不能作为知识库导入通过。

默认还会跳过超过 `--max-duration-sec` 的视频，脚本默认值为 3000 秒。超过 40–50 分钟的视频必须先标记为延期或等待用户明确批准；批准后再用 `--allow-long-video` 或调整上限。

默认 `--per-video-timeout-sec=0`，慢下载可以等待；curl 仅在低于 1 KiB/s 持续 120 秒时中止当前节点。base/backup URL 先做 2 MiB Range 测速，记录 host/IP/吞吐并选择最快有效节点；全失败则刷新一次 playurl，再进入网络 fallback。

跨 agent 路径策略：`--ascii-staging=auto` 只在检测到 Codex + Windows 且媒体路径含非 ASCII 时自动启用。Hermes/其它 agent 默认保持原路径行为，但可显式传 `--ascii-staging=on`。旧 `--codex-ascii-staging` 是兼容别名。

status 行可能包含：

- `profile`: 实际成功 profile，例如 `standard` 或 `lowrate`。
- `profile_failures`: 失败 profile 的错误摘要。
- `fallback_from`: fallback profile 成功时记录先前失败项。
- `audio_quality_policy`: 最低音频码率、是否允许诊断性低音质流、可用音频流摘要。
- `audio_quality_acceptable`: 最终音频是否满足导入音质门槛。
- `ascii_staging` / `codex_ascii_staging`: 通用字段和兼容字段，记录 staging root 与启用原因。
- `network_policy`: 请求/实际网络模式、是否整体 fallback、CDN host 与 remote IP。
- `cdn_probe`: 各镜像 Range 测速证据，不得包含签名 URL。
- `max_duration_sec` / `duration_sec`: 长视频门禁与实际时长。
- `attempts`: 每个 video/audio stream 的 id、codec、bandwidth、size、短日志。

只有排查脚本本身时才使用 `--no-auto-fallback`。如果所有 profile 失败，保留 status JSON，不要让单个 BV 中断整个 batch。

## 5. Compact 清洗约定

转写完成后先运行 `scripts/make_transcript_compact.py`，为每个 BV 写入：

- `video_meta.json`
- `transcript_compact_2min.md`
- `visual_cue_lines.md`
- `asr_risk_notes.md`

compact 只用于规划章节、候选关键帧和上下文续接，不替代原始 transcript JSON。最终三层文件仍需回查原始 JSON、关键帧和 VLM 记录。

脚本会过滤：

- 空白段。
- 纯标点、括号、时间壳等无内容段。
- 短平台噪声段，例如点赞、订阅、一键三连、投币、转发、关注、下期再见。

`video_meta.json` 和脚本摘要必须包含：

- `raw_segment_count`
- `kept_segment_count`
- `dropped_noise_segment_count`

compact 显示文本可做轻量 ASR 错词替换，例如“做公/作公 -> 做功”、“复攻 -> 负功”、“做正弓 -> 做正功”。`asr_risk_notes.md` 必须基于 `raw_text` 记录命中，避免清洗后丢失风险线索。

## 6. Multi-Agent 模式

仅当当前 agent 有可靠 subagent/delegate 能力时使用。主控负责：

- 队列、active wave、状态文件。
- 下载、合并、音频解码、fallback。
- faster-whisper 转写进程管理。
- compact 文件生成。
- ffmpeg 抽帧、contact sheet、VLM 验证、最终关键帧接受。
- raw 校验、Wiki 生成、RAG 重建、三轮验证、最终报告。

per-video worker 只能读 bounded 输入并写 bounded 输出：

- 输入：`video_meta.json`、`transcript_compact_2min.md`、`visual_cue_lines.md`、主控给出的已接受关键帧/VLM 摘要。
- 输出：`chapter_plan.md`、`keyframe_candidates.json`、`asr_risk_notes.md`，以及主控明确要求的 raw 草稿。

worker 禁止：

- 下载或转写视频/音频。
- 写 Wiki 或重建 RAG。
- 修改 `SCHEMA.md`、`skills/`、`BGE-M3/scripts/rag_pipeline.py`。
- 做最终验收。
- 在聊天回复中返回大段全文转写、长 VLM 文本或完整日志。

主控只信任 worker 落盘文件和 validator 输出，不信任聊天口头完成声明。

## 7. Serial-Agent 模式

没有可靠 subagent/delegate 时使用。串行模式不是降级质量，只是执行模型不同。

必做顺序：

1. 导入第一条视频前写 `batch_import_plan.md`。
2. 写 `videos_full_queue.json`、选择 `videos_active_batch.json`、初始化 `import_status.json`。
3. 每次只处理一条视频：download、transcribe、compact、keyframes、VLM、raw。
4. 每完成一条视频，更新 `import_status.json` 并运行 `scripts/summarize_import_status.py`。
5. 上下文接近 50%-60% 时停止启动新视频。
6. active batch 的 raw + Wiki 全部通过校验后，再统一重建一次 RAG。

串行 agent 仍使用 multi-agent 模式同一套 per-video 文件，因此后续可由支持 multi-agent 的 agent 无缝接手。

## 8. 阶段输入输出

| Stage | Controller input | Durable output | Main-context rule |
| --- | --- | --- | --- |
| discovery | Bilibili MCP/search result | `videos_full_queue.json` | 聊天里只保留已选候选 |
| active wave | full queue | `videos_active_batch.json`, `batch_import_plan.md` | 只列 wave 摘要 |
| download | active batch | MP4 files, `download_status.json` | 只读 profile 和失败摘要 |
| transcribe | MP4 files | TXT/JSON transcript, `transcribe_status.json` | 不粘贴全文转写 |
| compact | transcript JSON | per-video compact/meta/risk files | 只读当前视频 compact |
| chapter/keyframe planning | compact files | `chapter_plan.md`, `keyframe_candidates.json` | worker 输出必须 bounded |
| VLM/keyframe acceptance | contact sheets/candidate frames | `keyframe_vlm_notes.md`, final `keyframe_candidates.json` | 只总结未解决问题 |
| raw generation | accepted compact + keyframes | raw three-layer files | Wiki 前必须 validate raw |
| Wiki/RAG | validated raw | Wiki pages, RAG metadata | 每批统一 RAG rebuild |
| final validation | raw/Wiki/RAG | validation JSON loops | 报 issue_count 和 blocker |

## 9. 断点恢复

触发 65% 上下文阈值或用户反馈上下文溢出时：

1. 立即停止启动新下载、转写、worker、Wiki、RAG。
2. 更新 `import_status.json`。
3. 写 `context_compaction_card.md`，包含 task dir、active wave、已完成视频、未完成视频、下一步命令或下一步要读的文件。
4. 写 `context_overflow_pause_status_latest.json`，包含 status file paths、per-video raw matrix、blocking issues、background process notes、`next_after_compaction`。

新上下文恢复顺序：

1. 读 `context_compaction_card.md`。
2. 读 `context_overflow_pause_status_latest.json`。
3. 检查下载/转写后台进程是否仍在跑。
4. 运行 `scripts/summarize_import_status.py` 重新汇总状态。
5. 只继续 listed blocking issues。
6. Wiki/RAG 前重新跑 validator。

不要因为聊天上下文丢失而重复下载或重复转写已完成视频。

## 10. 验证门禁

声明 batch 完成前必须满足：

- raw-only validation `issue_count=0`。
- raw + Wiki validation `issue_count=0`。
- RAG metadata 命中新导入视频的 `source_type=transcript` chunks。
- 三轮最终 validation 都是 `issue_count=0`。
- 最终 raw 视频目录有 `keyframe_vlm_notes.md` 和 `keyframe_candidates.json`。
- 知识笔记没有可见时间戳、图片链接、平台/广告噪声。

脚本只负责机械门禁和状态摘要，不能替代 VLM 审阅、ASR 校正和最终教学质量判断。
