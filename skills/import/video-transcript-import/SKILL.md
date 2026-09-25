---
name: video-transcript-import
description: "B站教学视频导入工作流：搜索→下载→转写→LLM驱动抽帧→VLM验证→结构化MD存档→Wiki+RAG同步"
version: 1.5.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [video, transcript, bilibili, physics, education, rag]
    related_skills: [llmwiki-rag-retrieval, textbook-import]
---

# Video Transcript Import — B站教学视频导入工作流

> **🔒 锁定区** — 标准权限下必须按本流程执行，不得跳步、不得改流程、不得省略三库同步。目标是把 B站教学视频转为三层结构化 Markdown：逐字稿 + 知识笔记 + 教学简案，并同步 Wiki + RAG。

> 候选发现：当用户要求补抓某 UP 主更早/漏网视频，或 B站投稿接口被风控时，先按 `references/bilibili-candidate-discovery.md` 做“本地去重 → BV 反查 UID → 搜索 fallback → video_detail 核验 → 候选审计”，等待用户确认后再进入下载导入。

## 0. 核心原则

1. **只导入教学价值视频**：新课讲解、题目讲解、解题方法、题型归纳、专题突破；默认排除学习规划、直播回放、娱乐/Vlog、采访、宣传、搬运合集，除非用户明确要求。
2. **转写先于抽帧**：先得到带时间戳转写稿，再由 LLM/规则分析需要抽帧的板书/PPT/模型时刻；不要用场景检测替代。
3. **VLM 验证不可省**：抽帧时间点可能落在公式半成品，必须检查板书/公式完整性，必要时 ±5–10 秒重抽。
4. **三层文件不可混淆**：逐字稿保留时间戳，知识笔记面向 RAG 检索且无图片，教学简案面向教师备课且含关键帧。
5. **RAG 只切知识笔记**：逐字稿口语噪声大、教学简案有图片/流程，不进入 RAG 切片。
6. **批量导入流水线**：下载/合并/提取音频可批量并行；faster-whisper GPU 转写串行；LLM/VLM 限流时从断点继续。
7. **尊重用户本次范围**：若用户明确只要求“抽帧、VLM验证、raw三层文件生成”等 raw 阶段，不要擅自做 Wiki/RAG；但需在最终报告中明确“未做 Wiki/RAG（不在本次范围）”，并保证 raw 门禁完整。
8. **长任务必须状态文件驱动**：多视频导入不得依赖主对话记忆承载全文转写、完整 RAG 日志或 VLM 长评；必须用任务目录、队列 JSON、per-video 文件和断点卡交接。多 agent 只是可选加速路径，没有多 agent 能力的 agent 必须先生成导入计划，再按同一状态文件逐个串行导入。

## 1. 前置条件

- 知识库：`<KB_ROOT>`
- 临时工作目录统一放知识库根目录 `tmp/tasks/`（例如 `tmp/tasks/video_import_<BV>/`）；根目录禁止残留 `_tmp/`、`tmp/`、散落脚本或下载中间件。
- Python 3.10+、`bilibili-api-python`、`faster-whisper`；解释器、权重、FFmpeg/ffprobe 默认由共享 `model_runtime.py`/`media_tools.py` 解析，不依赖全局 PATH。
- 候选搜索/UP 主遍历需要 manifest 声明的 `bilibili-search` MCP；已知 BV/cid 的导入不强制依赖搜索 MCP。需要检索候选时，通过 `configure_agent_mcp.py` 接入 bundled Node/MCP，并运行 `python skills/_shared/scripts/check_mcp_requirements.py --json`；native MCP 不可用时使用 `mcp_call.py`。
- NVIDIA CUDA；共享模型 ID `faster-whisper-large-v3` 为质量优先默认，`faster-whisper-medium` 为降级，CTranslate2 float16
- 运行 Python310 / BGE-M3 venv 时要清理 agent 注入的 `PYTHONPATH`、`PYTHONHOME`；按当前 shell 使用 Step 5 或 `references/rag-env-pollution.md` 中对应命令。

## 2. 主流程

### Step 1 — 搜索与筛选

- 用 B站 MCP 搜索或 UP 主主页检索，记录标题、BV号、UP主、时长。
- 关注/优先：Lau博士的云组会、EvoAgentX、Agent智能体深度研究院，以及用户指定物理教学 UP 主。
- 搜索结果必须核对 `author`，避免导入搬运/营销号；BV 号必须 `len(bvid)==12 and bvid.startswith('BV')`。
- 若搜索找不到 UP 主全部视频，用已知 BV 的 `video_detail` 反查 `owner.mid` 后查主页；API 限流时改用多关键词搜索覆盖。
- 默认不启动超过 40–50 分钟的大视频下载；脚本默认门禁为 3000 秒。除非用户明确批准长视频，先把超长候选标记为 `deferred_long_video`，不要进入下载/转写队列。

完成标准：视频符合导入标准；BV/标题/UP 主/时长已记录；非教学视频已排除。

### Step 2 — 查重

- 在 `raw/transcripts/` 按 BV 号和标题双重查重。
- 已导入则跳过或只做补充修复；不要重复生成目录。

完成标准：确认未重复，或明确本次是修复已有视频。

### Step 3 — 获取详情

调用 `mcp_bilibili_search_bilibili_video_detail(videoId="BV...")`，确认 cid、分P、时长、UP 主信息；若视频描述含 arXiv 链接，论文和视频合并为同一 Wiki 页面。

完成标准：拿到可下载的 BV/cid/分P信息，时长用于后续抽帧越界校验。

### Step 4 — 下载、合并、存档

- 优先运行 `scripts/bilibili_playurl_download.py` 获取 DASH 下载流；video/audio 分开下载，优先选 `avc1` 视频流以保证 ffmpeg 兼容。
- 默认 `--network-mode direct --network-fallback system-proxy`：API 与媒体先一起国内直连，失败后才整体切换系统代理，禁止 API/媒体混合选路。每个 base/backup URL 先做 2 MiB Range 测速，按实测吞吐选择镜像，不按域名猜测速度。详见 `references/bilibili-network-policy.md`。
- 用 ffmpeg 合并：`ffmpeg -y -i video.m4s -i audio.m4s -c copy output.mp4`。
- MP4 原件存 `source-library/transcripts/`；`.m4s` 是临时文件，后续清理。

批量策略：多个视频的下载+合并+提取 wav 可在一个脚本循环完成；每个 BV try/except，失败记录，继续下一个。

合并后校验不要只看 `ffprobe -show_format` 的 duration/stream 个数：容器时长可能正常但 AAC 内容已损坏。必须追加至少一次音频解码门禁：

```bash
ffmpeg -v error -i output.mp4 -map 0:a:0 -f null -
```

若出现 AAC decode error、incomplete response、只提取出很短 WAV 等情况，先把坏 MP4/m4s 移到 `tmp/tasks/.../corrupt/` 留痕，再重新获取 playurl 并**从零下载**（不要 `curl -C -` 续传旧文件；B站 CDN 链接过期后续传可能拼接不同对象）。重下后再做音频解码门禁和关键时间点抽帧抽样校验。下载慢可以等待，不能为了速度牺牲导入音频质量。

**音频质量硬门禁**：高中物理视频导入默认必须保留可用于 ASR 的高音质音频。脚本默认只接受 `bandwidth >= 96000` 的音频流；若高音质音频 partial 或下载超时，应重新获取 playurl、换 backup URL/CDN 镜像、遍历其它高音质音频流并从零下载。不要默认回退到 `30216` 或其它低码率音频；若所有高音质音频都失败，标记该 BV 为 `failed/retry` 并保留状态文件，不能进入转写和入库。低码率音频只允许人工诊断，不作为知识库导入来源。详见 `references/bilibili-audio-partial-fallback.md`。

`scripts/bilibili_playurl_download.py` 默认启用两类 fallback：CDN 镜像全失败时刷新一次 playurl，仍失败才整体切换系统代理；标准 profile 失败后可尝试低视频码率 profile，但音频仍执行高音质门槛。`--per-video-timeout-sec` 默认 `0`，不设总下载时限；curl 仅在持续 120 秒低于 1 KiB/s 时判节点失效。只有诊断脚本时才用 `--no-auto-fallback` 或 `--allow-low-audio`。

跨 agent 路径兼容：先运行 `scripts/normalize_video_queue.py`，把单对象/数组统一为 JSON 数组并生成 `original_title`、Windows 安全的 `storage_title`。`--ascii-staging=auto` 仅在 Codex + Windows + 非 ASCII 媒体路径时自动启用；其它 agent 可显式传 `--ascii-staging=on`。旧参数 `--codex-ascii-staging` 保留为兼容别名。

**视频流 DTS fallback**：若视频 m4s 在严格 `ffmpeg -xerror` 下只报 `non monotonically increasing dts`，但去掉 `-xerror` 后像素流可完整解码，不要立刻跳过该 BV。保留坏流留痕，重新获取/严格校验音频，然后用 `ffmpeg -fflags +genpts` 重建时间戳合并；若 copy remux 仍失败，可只重编码视频轨、copy 音频。最终 MP4 仍必须通过 format/video/audio duration + audio decode + 抽样抽帧门禁。详见 `references/bilibili-nonmonotonic-dts-fallback.md`。

完成标准：MP4 可播放；音频可完整解码；原件已存档；失败列表可重试。

### Step 5 — 音频转写

1. 提取音频：`ffmpeg -y -i input.mp4 -vn -acodec pcm_s16le -ar 16000 -ac 1 output.wav`
2. 调用 `scripts/run_transcribe_with_shared_runtime.bat`，或让 `transcribe_video_batch.py` 通过共享 resolver 获取 `faster-whisper-large-v3`；只有显式 `--model` 才覆盖默认路径。不是 openai-whisper。
3. 输出：`transcript.txt`（全文）和 `transcript.json`（`start/end/text`）。
4. 转写是本地 GPU 任务，用 `terminal(background=true, notify_on_complete=true)`；若通知丢失，以输出文件存在和日志为准。

环境隔离：若 Python310 导入到 agent venv 的 `faster_whisper/av` 导致 DLL 错误，按当前 shell 清理变量。

PowerShell：

```powershell
$env:PYTHONPATH=$null; $env:PYTHONHOME=$null
& "<PYTHON310>/python.exe" script.py
```

CMD：

```bat
set PYTHONPATH=
set PYTHONHOME=
"<PYTHON310>\python.exe" script.py
```

MSYS/bash/POSIX：

```sh
env -u PYTHONPATH -u PYTHONHOME "<PYTHON310>/python.exe" script.py
```

路径坑：Hermes 的 `terminal` 在 Windows 上走 MSYS bash，`/c/Users/...` 可作为 bash/可执行路径使用；但一旦进入原生 Windows Python，`Path('/c/Users/...')` 会变成 `\c\Users\...` 并找不到文件。Python 脚本内部的知识库、输入输出路径必须写成 `C:/Users/...` 或 `C:\\Users\\...`。

完成标准：txt + json 存入 `source-library/transcripts/`，json 可解析，WAV/Whisper `info.duration` 与视频时长一致。注意最后一条语音 segment 可能因片尾静音比视频短十几秒，不能单独用 last segment end 判坏；但若 WAV 或 `info.duration` 明显短于视频，必须回到 Step 4 重下或修复音频。

### Step 6 — LLM 驱动抽帧

把 `transcript.json` 转成 `[MM:SS] 文本` compact 文本，让 LLM/delegate_task 输出：

```json
[{"time": 39, "reason": "展示薄膜模型"}]
```

抽帧标准：老师讲“如图/这个模型/公式/推导/练习/实验装置/动画模型”且画面承载板书/PPT/模型信息；排除开场白、过渡动画、结尾、广告。短视频 10–15 分钟抽 7–9 帧；20 分钟以上 10–12 帧；常规目标 8–15 帧。

限流 fallback：delegate_task 429 时，不等待；直接读 compact 前 30–50 行 + 搜索关键词（如图、模型、公式、练习、定义、推导），按教学结构每 2–3 分钟选候选时间点。此策略已在UP-乙多用电表/伏安法/质谱仪等视频验证可用。

抽帧前必须 `ffprobe` 检查视频时长；LLM 给出超过时长的 time 要调整到有效范围内。

完成标准：得到合法时间点列表，每个 time 在视频时长范围内，避开广告/无板书段。

### Step 7 — ffmpeg 精确抽帧 + VLM 验证

- 抽帧命令：`ffmpeg -y -ss <time> -i input.mp4 -frames:v 1 -q:v 2 keyframe_XX_<time>s.jpg`
- 命名：`keyframe_{序号:02d}_{时间秒}s.jpg`
- 抽帧前不只看 `format.duration`，还要检查 `v:0` 视频流时长；若 MP4 的音频/format 时长完整但视频流只到前几十秒，说明 MP4 视频轨损坏或合并异常，改用同 BV/cid 重新获取的 avc1 DASH 视频 m4s 抽帧。下载 m4s 优先用 `curl -L --fail --retry 3 --retry-all-errors` 并带 B站 Referer/UA，避免 Python urllib 静默得到短文件。详见 `references/bilibili-video-stream-duration-mismatch.md`。
- VLM prompt：`提取画面中所有文字、公式、标注。只输出提取的内容，不要解释。`
- 若要判断“完整/不完整”，prompt 必须指定目标知识点，避免 VLM 因“缺少本节其它知识点/其它推导环节”误判。例如：`目标知识点：两个等大共点力夹角为θ时的合力公式。请提取画面中所有文字、公式、标注，并仅根据这个目标知识点判断画面是否完整。`
- **目标范围要写准**：题干/模型帧只要求“题干与模型完整”，不要因未出现答案或推导而判不完整；解题帧才要求公式最终变形和答案出现。若一个知识点天然分成“题干帧 + 解法帧”或“上升阶段 + 下降阶段”等，不要强行要求单帧覆盖全部内容，应拆成多个目标帧并在 VLM 记录中说明互补关系。
- VLM 每批 5 帧，限流时保存已完成结果，从断点继续。
- **优先用关键帧拼图审阅**：板书类视频先把 4 张关键帧拼成 2×2 contact sheet，每格标注编号+时间，让 VLM 逐格提取文字/公式并判定“完整/基本完整/不完整/被遮挡需重抽”。若发现遮挡或未写完帧，围绕该时间点 ±20–120 秒批量补抽候选，再做第二轮拼图比较；最终 manifest 只引用 complete/basic 帧，宁少勿滥。详见 `references/keyframe-contact-sheet-review.md`。
- **例题讲解必须保留结论帧**：算法按转写关键词/均匀结构抽出的候选常会停在“题干/推导中段”，漏掉最后答案或方法总结。contact sheet 审阅后，必须对照 compact transcript 的结尾段和每道例题的收束段，额外补抽“答案已圈出/公式已配平/方法总结已写完”的结论帧；若结论帧比原候选更完整，用结论帧替换原过渡帧，并在 `keyframe_candidates.json.retries` 与 `keyframe_vlm_notes.md` 写明“替换原因 + 最终采用时间”。
- 若要判断“完整/不完整”，prompt 必须指定目标知识点，避免 VLM 因“缺少本节其它知识点/其它推导环节”误判。例如：`目标知识点：两个等大共点力夹角为θ时的合力公式。请提取画面中所有文字、公式、标注，并仅根据这个目标知识点判断画面是否完整。`
- **目标要随重抽动态收窄**：若 VLM 因你把目标写得过宽而判“不完整”（例如同一帧只负责“滑块受力核心板书”，却要求它同时包含右侧 v-t 图），不要盲目继续后移；先把 prompt 收窄到该帧在三层文件中实际承担的知识点，再重新验证。关键帧可以分工覆盖模型题干、受力公式、v-t 趋势、练习结果；不要求每一帧包含整段推导。
- VLM 每批 5 帧，限流时保存已完成结果，从断点继续。
- 公式/板书不完整、题目答案尚未出现、推导公式尚未最终变形、只处于讲解中段时，优先在原时间点后移 5–60 秒重抽；若已错过完整画面再前移。最多重试 2 次；若字幕/悬浮按钮遮挡题干或知识框，也按不完整处理并记录原帧问题与采用的重抽帧。
- 若重抽替换了时间点，必须同步更新 `keyframe_candidates.json` / 逐字稿关键帧总览 / 教学简案引用，并删除被判定不完整的旧 jpg，避免 media 中出现“废弃关键帧”被后续误引用。`keyframe_candidates.json` 建议记录 `ffprobe_check`、最终 `time_s/file/reason/vlm_complete`、以及 `retries:[{time_s, issue|result}]`，方便后续审计为什么选用最终帧。
- 建议额外写 `keyframe_vlm_notes.md`：记录每帧 VLM 提取要点、目标知识点、完整性、重抽原因。最终检查以此文件和 manifest 中 `vlm_all_complete=true` 双重确认。

完成标准：关键帧均有 VLM 提取结果；重要公式/模型帧完整；manifest、三层文件引用、media 实际文件三者一致，无废弃重抽帧。

完成标准：关键帧均有 VLM 提取结果；重要公式/模型帧完整；若有替换帧，VLM 记录中能追溯替换原因。

### Step 8 — 生成三层文件

通过 delegate_task 或主 agent 生成三层文件；子 agent 可能因 429 部分写入，必须逐个文件检查。

**8.1 逐字稿 `<标题>.md`**
- `type=transcript`，frontmatter 含 title、up_master、bvid、duration、sources。
- 文档总结；按关键帧时间点分章；每章有关键帧引用和逐句 `[MM:SS]`。
- 可修正明显 ASR 错词，但保留转写性质。

**8.2 知识笔记 `<标题>_知识笔记.md`**
- `type=knowledge_notes`；纯文本，无图片引用。
- 去口语、时间戳、重复、广告；保留定义、公式推导、物理结论、例题方法；板书公式转 LaTeX；ASR 错词全部修正。
- **RAG 只切此层**，`source_type=transcript`。

**8.3 教学简案 `<标题>_教学简案.md`**
- `type=lesson_plan`；按教学环节：引入 → 新知 → 例题 → 总结。
- 记录“教了什么、怎么教、如何实现”；保留关键帧引用；广告标注“不纳入教学流程”。

ASR 常见错词：剪斜→简谐，电视能→电势能，能词定律→楞次定律，词通量→磁通量，云变数→匀变速，做正弓→做正功，灵势面→零势面等。完整映射见 `references/asr-error-mapping.md`。

完成标准：三层文件都存在且 frontmatter 正确；知识笔记无广告/图片/时间戳噪声；教学简案含关键帧与教学策略。

### Step 9 — 存档与清理

目录结构：

```text
raw/transcripts/<UP主>/<视频标题>/
├── <视频标题>.md
├── <视频标题>_知识笔记.md
├── <视频标题>_教学简案.md
└── media/keyframe_XX_XXXs.jpg

source-library/transcripts/
├── <UP主>_<标题>.mp4
├── <UP主>_<标题>.txt
└── <UP主>_<标题>.json
```

**Windows 标题路径门禁**：视频原始标题可保留在 frontmatter 的 `title`、`sources` 和任务状态中；但用于 `raw/transcripts/`、`LLMWiki/concepts/`、`LLMWiki/assets/` 的目录名、文件名和 `import_title/wiki_title` 必须先规范化。Windows 禁止字符 `<>:"/\\|?*` 替换为连字符，移除控制字符并去掉末尾空格或句点；发生规范化时在任务状态记录 `original_title` 与 `storage_title`。不要因标题含冒号等保留字符跳过已通过媒体门禁的视频。

清理 `.m4s/.wav/_compact.txt/_tmp_`，保留 MP4/txt/json 原件；需要保留的脚本、状态 JSON、坏件和审计留痕统一迁入 `tmp/tasks/`，不要留在知识库根目录。

完成标准：raw 有三层 + media，原件有 MP4/txt/json，临时文件无残留。

### Step 10 — Wiki + RAG 同步

**三库关系门禁**：Wiki 不是 raw 的索引壳。视频 Wiki 页必须把 raw 中已经整理好的 `知识笔记` 和 `教学简案` 导入 Wiki 正文，或在 Wiki 中二次加工成自包含页面；不得生成 `^[../raw/...]` transclusion、`## raw 原文` 或可见 `[查看](../raw/...)` 链接。回答 agent 读取 Wiki 页/子页，不回 raw。

Wiki 页命名：`LLMWiki/concepts/视频-<标题>.md`。正文导入知识笔记和教学简案，不导入逐字稿；若合并后超过 20KB，则父页保留总览和 `[[Wiki子页]]`，把 `知识笔记`、`教学简案` 拆成 `concepts/视频-<标题>-知识笔记.md`、`concepts/视频-<标题>-教学简案.md`。frontmatter `sources` 可保留 raw 路径作溯源元数据，但正文不得有可见 raw 链接。

```markdown
---
title: <标题>——<UP主>视频
type: video_transcript
tags: [video, transcript, physics, <UP主>]
sources:
  - ../raw/transcripts/<UP主>/<标题>/<标题>_知识笔记.md
  - ../raw/transcripts/<UP主>/<标题>/<标题>_教学简案.md
up_master: "<UP主>"
bvid: "BV..."
---

# <标题>——<UP主>视频

## 知识笔记
<从 raw 整理稿导入或二次加工后的 Wiki 内容>

## 教学简案
<从 raw 整理稿导入或二次加工后的 Wiki 内容>
```

若教学简案引用关键帧，需把对应图片复制到 `LLMWiki/assets/` 并改成 Wiki 内部资产路径。更新 `index.md` 和 `log.md`。若视频描述含 arXiv，论文+视频合并为同一 Wiki 页面。

RAG 重建按当前 shell 清理环境；Windows/Codex 优先使用仓库 guard：

```powershell
python .codex/helpers/windows_utf8_guard.py rag --kb-root .
```

其它 shell 见 `references/rag-env-pollution.md`。

不要把输出管道到 `tail`；管道提前关闭可能让保存阶段中断但外层看似成功。批量导入时等全部视频完成后统一重建一次。

完成标准：metadata 中存在新增视频标题且 `source_type='transcript'`；`metadata.json` 兼容 dict/list，dict 时用 `meta.get('chunks', [])`。

### Step 11 — 入库后 3 圈 Loop 检查

每圈检查：

1. raw：每个视频目录 3 个 MD + media 关键帧齐全。
2. Wiki：`concepts/视频-<标题>.md` 存在，sources 指向知识笔记+教学简案。
3. RAG：metadata chunks 中有新增视频，`source_type=transcript`。
4. 临时文件：raw/原件目录无 `.m4s/.wav/_compact.txt/_tmp_`；知识库根目录无 `_tmp/` 或 `tmp/`；需要保留的批次脚本、状态、坏件统一在 `tmp/tasks/`。
5. 质量：知识笔记无广告、无时间戳噪声、无图片；教学简案含关键帧和教学策略。

完成标准：至少 3 圈，最终 0 issues。批量任务优先用随技能附带的 `scripts/validate_video_import.py` 做机械门禁（raw 文件、关键帧、知识笔记噪声、原件三时长、Wiki 自包含、RAG chunks），再做人工抽样复核；脚本不能替代 VLM/内容判断，但可防止漏文件、坏 MP4、raw 指针壳、知识笔记误含图片/时间戳等回归。

若是在另一个对话续接导入后做健康检查，按 `references/video-import-health-check.md` 复核当前有效库：frontmatter `sources: ../raw/...` 只作溯源不算 raw 指针；`LLMWiki/_meta/` 全部审计/历史备份不参与当前 active Wiki 断链、缺图、超 20KB 或 wikilink 判断，避免把备份页缺失旧 media 当成当前图谱问题。若只缺 `keyframe_candidates.json`，但最终 `media/keyframe_*.jpg` 与 `keyframe_vlm_notes.md` 已完整存在，可从实际 media 文件名补生成 manifest；这是审计修复，不改知识笔记，不需要重建 RAG。

## 3. 批量导入策略

- IO 任务（下载、合并、提取 wav）可批量执行。
- GPU 任务（faster-whisper）串行；同一时刻最多 1 个转写。
- 若同一 BV 的 MP4 已因音质、时长或解码门禁被重新下载，转写必须传 `--force`；已有 TXT/JSON 不能作为新原件转写完成的证据。
- API 任务（delegate_task/VLM）限流敏感，同一时刻 1–2 个；VLM 每批 5 帧。
- API 429 时继续做本地下载/合并/转写；抽帧可用 fallback；三层文件生成等 API 恢复后只补缺失文件。
- **强制批量模式判定**：1–2 条视频允许单 agent 完整导入；3–6 条视频或累计时长超过 60 分钟必须进入 context-budgeted batch mode；超过 6 条必须拆 wave，每 wave 默认 2–4 条，长视频或复杂专题降到 1–2 条。候选很多或用户要求“逐个导入/完成后继续即可”时，不要把全队列塞进主对话一次性执行。
- **状态文件约定**：先在 `tmp/logs/legacy-task-status/<task_id>/` 写 `videos_full_queue.json`、`videos_active_batch.json`、`import_status.json`、`batch_import_plan.md`、`context_budget_and_delegation_plan.md`；每个视频用 `per_video/<BV>/video_meta.json`、`transcript_compact_2min.md`、`visual_cue_lines.md`、`chapter_plan.md`、`keyframe_candidates.json`、`asr_risk_notes.md` 交接。转写完成后优先运行 `scripts/make_transcript_compact.py` 生成 compact 文件；下载/转写/校验/RAG 后用 `scripts/summarize_import_status.py` 汇总短状态，不要把完整日志贴进主上下文。
- compact 文件只供规划，不替代原始 transcript JSON。`make_transcript_compact.py` 会过滤空白、纯标点、重复平台噪声段，并在 `video_meta.json` 记录 `raw_segment_count`、`kept_segment_count` 与 `dropped_noise_segment_count`；若 compact 看起来仍有噪声，生成知识笔记时必须回查原始 JSON 与关键帧。
- **多 agent / 弱 agent 兼容**：若当前环境有可靠 subagent/delegate 能力，可用“主控 + per-video worker”；worker 只读 per-video 输入并写 bounded output，不下载、不转写、不写 Wiki/RAG、不改 schema/skills/pipeline、不做最终验收。若没有多 agent 能力，必须先写 `batch_import_plan.md`，再按 `videos_active_batch.json` 逐个串行导入，每完成一个视频更新 `import_status.json`；串行模式仍使用同一套 per-video 文件和校验门禁。
- **主控上下文禁区**：主 agent 不得读取全文转写、完整 RAG 日志、完整 VLM 长评或大批原始搜索结果，除非定位一个具体失败；主控只读 status JSON、compact 摘要、validator issue summary 和最终 manifest。
- **上下文硬阈**：长任务总控估计上下文超过模型窗口 50%–60% 时，不再启动新视频；超过 65% 或用户反馈上下文溢出时，必须先暂停手中工作，不再启动新下载/转写/子 agent/Wiki/RAG；先写或更新 `context_compaction_card.md` 与 `context_overflow_pause_status_latest.json`（包含 status_files、per_video_raw_matrix、blocking_issues、next_after_compaction）。压缩/新上下文后第一步必须检查后台进程、子 agent 文件落盘结果、`download_status.json`、`transcribe_status.json` 与 raw/Wiki/RAG 状态，再从状态文件接上工作，避免重复导入或漏验收；详见 `references/context-compaction-resume.md` 与 `references/context-budgeted-video-import.md`。
- **raw 审计文件门禁**：若 VLM/抽帧审计先写在 `tmp/tasks/.../per_video/<BV>/`，在运行 `validate_video_import.py` 前必须把 `keyframe_vlm_notes.md` 和可解析的 `keyframe_candidates.json` 复制/汇总到正式 raw 视频目录。缺这两个文件会导致 raw 校验失败；这属于审计补齐，不需要改知识笔记或重建 RAG。
- 同一 UP 主多个视频放 `raw/transcripts/<UP主>/`；已导入 UP 主包括 `UP-丁/`、`教物理的UP-甲/`、`只教物理的UP-甲/`、`UP-乙有美有物理/`。

## 4. 常见事故与硬门禁

1. **不要场景检测抽帧**：场景检测会抽开场/过渡动画，必须以转写稿内容驱动。
2. **BV 号错误会中断批量下载**：下载前校验 12 位 `BV` 开头，单个失败用 try/except 记录后继续。
3. **B站 CDN SSL EOF**：换 backup URL 或重新获取下载链接。
4. **LLM 抽帧越界**：抽帧前用 ffprobe 校验时长，超范围 time 调整。
5. **VLM 限流**：分批 5 帧，保存断点，不从头重跑。
6. **subagent 部分成功**：返回 completed 不代表 3 个 MD 都写了；逐个文件检查，只补缺失。
7. **广告处理差异**：UP-丁广告常在中间；UP-乙广告常在开头 0–40 秒。知识笔记排除，教学简案标注不纳入教学。
8. **逐字稿不进 RAG**：只切知识笔记，避免口语噪声污染检索。
9. **RAG 环境污染**：必须按当前 shell 清空 `PYTHONPATH`、`PYTHONHOME`，避免 FlagEmbedding/CUDA/FAISS DLL 冲突或 exit 139；不要把 POSIX 的 `env -u` 直接复制到 PowerShell/CMD。
10. **视频流时长不匹配**：`format.duration`/音频完整但 `v:0 duration` 只到 60s 左右时，MP4 可能只有前段视频轨；60s 后抽帧会报 H.264 NAL 错误或输出空文件。改用同 BV/cid 重新获取 avc1 DASH 视频 m4s 抽帧，下载用 curl 重试并校验大小/时长，抽完清理临时 m4s。
11. **视频流 DTS 非单调**：若严格视频解码门禁只因 `non monotonically increasing dts` 失败，先确认去掉 `-xerror` 后像素可完整解码；可用 `-fflags +genpts` remux，必要时只重编码视频轨。不要放松最终 MP4 的时长、音频解码和抽样抽帧门禁。详见 `references/bilibili-nonmonotonic-dts-fallback.md`。

## 5. Verification Checklist

- [ ] 视频筛选通过，BV/UP主/时长记录，未重复导入。
- [ ] 超过 3000 秒或用户设定上限的视频已跳过、延期或获得明确批准。
- [ ] MP4 下载合并成功并存档到 `source-library/transcripts/`。
- [ ] `network_policy` 显示 API/媒体同路由，`cdn_probe` 有被选镜像的 host/IP/吞吐证据且不含签名 URL。
- [ ] 下载状态包含 `audio_quality_policy`，最终音频流满足高音质门槛；未用低码率音频冒充导入原件。
- [ ] txt/json 转写完成，json 可解析。
- [ ] 抽帧时间点由 LLM 或 fallback 产生，均在视频时长内。
- [ ] 抽帧前已校验 `format.duration` 与 `v:0` 视频流时长一致；若不一致，已按视频流时长不匹配 workaround 重下 avc1 m4s 抽帧。
- [ ] 关键帧已抽取；VLM 验证文字/公式/模型完整；不完整帧已重抽。
- [ ] 三层文件存在：逐字稿、知识笔记、教学简案。
- [ ] 知识笔记：无图片、无广告、无时间戳噪声、ASR 错词已修正、公式为 LaTeX。
- [ ] 教学简案：有教学环节、策略、关键帧引用，广告标注不纳入教学。
- [ ] raw/transcripts 目录结构正确，media 关键帧齐全。
- [ ] 临时 `.m4s/.wav/_compact.txt/_tmp_` 已清理。
- [ ] Wiki 页已创建/更新，引用知识笔记+教学简案。
- [ ] RAG 已重建，metadata 有新增 `source_type=transcript` chunks。
- [ ] 入库后 loop 检查至少 3 圈，最终 0 issues。

## 6. References（按需查看，不替代主流程）

- `references/asr-error-mapping.md` — 物理 ASR 同音字错误映射。
- `references/bilibili-candidate-discovery.md` — B站候选发现与审计。
- `references/bilibili-video-stream-duration-mismatch.md` — MP4 视频流时长短于音频/format 时的重下 avc1 m4s 抽帧 workaround。
- `references/bilibili-nonmonotonic-dts-fallback.md` — B站 DASH 视频流 DTS 非单调但像素可解码时的 `-fflags +genpts`/视频轨重编码 fallback；最终门禁仍保持严格。
- `references/bilibili-import-integrity.md` — B站下载/合并完整性门禁：format/video/audio 三时长、音频解码、禁用过期 CDN 续传、Windows Python 路径规则。
- `references/bilibili-network-policy.md` — API/媒体一致选路、CDN Range 测速、fallback、超时和安全状态字段。
- `scripts/normalize_video_queue.py` — 把单对象/数组队列统一为数组，并生成可跨 Windows/agent 使用的存储标题。
- `scripts/probe_bilibili_cdn.py` — 对一个 BV 的视频/音频 CDN 做 2 MiB 差分测速，只输出 host/IP/吞吐，不输出签名 URL。
- `scripts/bilibili_playurl_download.py` — playurl API + CDN 测速 + curl + ffmpeg 下载合并，并执行网络一致性、时长、音质和解码门禁。
- `scripts/transcribe_video_batch.py` — 批量调用 faster-whisper large-v3 转写 MP4，输出 txt/json；运行时必须清理 Hermes 注入环境。
- `scripts/make_transcript_compact.py` — 从转写 JSON 生成 per-video `transcript_compact_2min.md`、`visual_cue_lines.md`、`asr_risk_notes.md` 与 `video_meta.json`，过滤空白/纯标点/平台噪声段，避免主上下文读取全文转写。
- `scripts/summarize_import_status.py` — 汇总下载、转写、校验、RAG metadata/log 为短 JSON，供主控续接和最终报告使用。
- `scripts/validate_video_import.py` — raw/Wiki/RAG 三阶段视频导入验证脚本；支持 `--global-wiki` 当前有效 Wiki 检查、`--backfill-manifest` 补齐关键帧审计 manifest、`--sync-keyframe-count` 把最终关键帧数回写队列。`keyframes` 默认是最低期望数量，只有 `keyframes_exact: true` 才要求精确相等。
- `references/rag-env-pollution.md` — RAG 环境污染与 segfault 修复。
- `references/transcript-three-layer-pattern.md` — 三层文件结构范式。
- `references/video-import-health-check.md` — 断点续接/跨会话导入后的 raw/Wiki/RAG 健康检查规则；说明 frontmatter raw sources 与可见 raw 指针的区别、备份目录排除、keyframe manifest 补齐规则。
- `references/video-wiki-three-page-normalization.md` — 视频 Wiki 三页结构统一整理：父页/总览页 + 知识笔记 + 教学简案；旧单页视频从 raw 三层稿补齐子页，知识笔记保持纯文本。
- `references/screenshot-missing-video-lesson.md` — 截图候选漏网视频经验：B站关键词搜不到不等于不存在；保留 unresolved 候选，用户补 BV 后用 video_detail+本地查重确认并导入；同时记录 VLM 局部目标设置经验。
- `references/local-video-coverage-baseline.md` — 正式搜新候选前的本地覆盖/查重基线扫描：盘点 raw+Wiki 已导入视频、BV/标题/UP 别名、课程模块覆盖和未覆盖方向，输出给主 agent 汇总筛选。
- `references/two-account-candidate-discovery.md` — UP-甲双官方账号候选发现经验：两个账号 UID/name 核验、本地已入库 BV 排除、搜索组合、弱覆盖主题判断、JSON 审计字段。
- `references/subagent-batch-import-orchestration.md` — 大批量视频导入的总控/子 agent 分工：首批 4–6 条 P0，下载/转写用后台状态文件，compact/抽帧/VLM/raw 三层交给 per-video 子 agent，主 agent 只做验收、Wiki/RAG 和三圈 loop。
- `references/batch-subagent-orchestration.md` — 本次批量导入沉淀的上下文预算模板：任务目录/队列 JSON、per-video 子 agent 文件边界、raw 校验前复制 `keyframe_vlm_notes.md` 与 `keyframe_candidates.json`、Wiki/RAG/三圈 loop 顺序。
- `references/context-compaction-resume.md` — 大批量视频导入触发 65% 上下文硬阈或用户反馈上下文溢出时的暂停、状态矩阵、恢复顺序与防重复导入规则。
- `references/context-budgeted-video-import.md` — 长视频/多视频导入的强制 batch mode、multi-agent 与 serial-agent 兼容、状态文件接口和断点恢复顺序。

## 7. Overlap Note

`video-to-study-notes` 是通用视频→学习笔记流水线；本 skill 是 B站教学视频→LLMWiki_BGE-M3，强调 LLM 抽帧、VLM 验证、三层文件、Wiki/RAG 同步。二者技术有重叠，但目标库和输出格式不同，当前不合并。

## 8. Role D 强制图谱后置阶段

三页 Wiki 同步和视频质量门禁通过后，以 `source_skill=video-transcript-import` 生成 handoff，并在最终 RAG 重建前执行：

```bash
python skills/_shared/scripts/import_handoff.py create --spec <handoff-spec.json> --json
python skills/learn/physics-knowledge-graph/scripts/update_graph.py --handoff <import_handoff.json> --dry-run --json
python skills/learn/physics-knowledge-graph/scripts/update_graph.py --handoff <import_handoff.json> --apply --result-out <skills/_ops/runtime/state/.../graph_update_result.json> --json
python skills/_shared/scripts/import_handoff.py verify --handoff <import_handoff.json> --graph-result <graph_update_result.json> --json
```

handoff 记录新增视频三页、相关 kp ids、质量报告和 hash。安全横向链接必须 apply；破坏性图谱变更只输出审批项。`no_change` 或 completion-ready 图谱结果通过 verify 后，才重建 RAG 并声明视频导入完成。
