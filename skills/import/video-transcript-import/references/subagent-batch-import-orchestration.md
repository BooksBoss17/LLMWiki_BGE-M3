# 子 agent 分批导入编排经验

适用场景：用户要求批量导入一组 B站教学视频，且候选已查重/分级；总控对话需要避免被下载日志、完整逐字稿、VLM 长输出和三层草稿撑爆上下文。

## 总控原则

- 主 agent 负责：任务目录、候选队列、边界声明、后台进程管理、最终验收、Wiki/RAG 同步、三圈 loop。
- 子 agent 负责：能封装成“输入文件 → 输出文件 + 短摘要”的局部工作。
- 主对话不要读取完整逐字稿、长 VLM OCR 日志或完整三层草稿；只读 compact、manifest、issue 摘要和抽样片段。
- 子 agent 的成功是自报告，涉及文件写入后必须由主 agent 验证文件存在、JSON 可解析、关键字段齐全，再继续。

## 推荐批次

1. 从候选 JSON 中先选 4–6 条 P0 视频做第一批，不要一次导入 20–30 条。
2. 下载/合并可批量；faster-whisper GPU 转写串行或小批次补跑。
3. raw/Wiki/RAG 不要在下载完成后一次性全自动推进；逐视频完成 compact → 抽帧 → VLM → raw 三层，再批量 Wiki/RAG。
4. 对版权/来源标记异常的视频（例如 owner 是官方但 copyright=转载）先放入暂缓队列，导入前单独复核。

## 易撑爆上下文的环节与处理

| 环节 | 风险 | 推荐处理 |
|---|---:|---|
| 下载/合并 | 低 | 后台脚本 + status JSON；主 agent 只读 ok_count/失败摘要。 |
| 转写 | 中 | 写 txt/json 到原件目录；主 agent 不读完整逐字稿。 |
| compact/章节分析 | 高 | 脚本生成 `transcript_compact_2min.md` 与 `visual_cue_lines.md`；子 agent 只读这两个文件。 |
| 抽帧候选 | 中高 | per-video 子 agent 输出 `chapter_plan.md`、`keyframe_candidates.json`、`asr_risk_notes.md`。 |
| VLM/contact sheet | 高 | 主 agent 或子 agent 生成 `keyframe_vlm_notes.md` 和 `final_keyframes.json`；主 agent 只验收 `vlm_all_acceptable` 与 basic/complete 分工。 |
| 三层 raw 生成 | 极高 | per-video 子 agent 写 `_知识笔记.md` 与 `_教学简案.md`；主 agent 验证 frontmatter、噪声、图片引用边界。 |
| Wiki/RAG | 中 | 主 agent 统一生成/验收；RAG 批量完成后统一重建。 |

## per-video 子 agent 输入/输出契约

### compact 分析 agent

输入：
- `video_meta.json`
- `transcript_compact_2min.md`
- `visual_cue_lines.md`

输出：
- `chapter_plan.md`
- `keyframe_candidates.json`
- `asr_risk_notes.md`

约束：不读取原始逐字稿；不下载、不抽帧、不写 raw/Wiki/RAG。

### raw 三层生成 agent

输入：
- raw 目录中已生成的 `<标题>.md`
- `keyframe_manifest.json`
- `chapter_plan.md`
- `asr_risk_notes.md`
- `keyframe_vlm_notes.md`
- compact 文件

输出：
- `<标题>_知识笔记.md`
- `<标题>_教学简案.md`

约束：只写指定 raw 目录；禁止写 LLMWiki、BGE-M3、SCHEMA、skills；禁止运行 RAG。

## 主 agent 验收清单

- 下载：`download_status.json` 中每条 `ok=true`，format/video/audio 时长一致，`audio_decode_clean=true`。
- 转写：`transcribe_status.json` 中每条 `ok=true`，txt/json 存在，segments 非空。
- 抽帧：`keyframes_manifest.json` 中 ok_count == candidate_count；media 文件存在。
- VLM：`final_keyframes.json` 中 `vlm_all_acceptable=true`；basic 帧只承担题干/引入/过渡，不承担公式结论。
- raw：三层文件齐全；知识笔记无图片、无时间戳噪声、无广告/校园广播；教学简案有关键帧引用。
- Wiki/RAG：Wiki 自包含无可见 raw 链接；RAG metadata 有新增 `source_type=transcript` chunks。

## 状态目录建议

每批任务在：

```text
<KB_ROOT>/tmp/logs/legacy-task-status/video_import_<batch_id>/
├── videos_active_batch.json
├── videos_full_queue.json
├── download_status.json
├── transcribe_status.json
├── per_video/<BV>_<short_title>/
│   ├── video_meta.json
│   ├── transcript_compact_2min.md
│   ├── visual_cue_lines.md
│   ├── chapter_plan.md
│   ├── keyframe_candidates.json
│   ├── keyframes_manifest.json
│   ├── keyframe_vlm_notes.md
│   └── final_keyframes.json
└── context_budget_and_delegation_plan.md
```

保留状态文件供交接；不要把临时状态散落在知识库根目录。