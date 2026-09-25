# 批量视频导入工作流

## 适用场景

从同一UP主批量导入多个视频（如UP-丁13个、UP-乙13个）。需要高效并行处理同时保证质量。

## 并行策略

### GPU串行 + LLM并行

- **转写**用 GPU（faster-whisper CUDA），必须串行（显存限制）
- **LLM抽帧分析**用 delegate_task，可与转写并行
- **三层文件生成**用 delegate_task，可与转写+抽帧并行

### 实际流程（3个并行通道）

```
时间轴 →
通道1(GPU):  转写视频A → 转写视频B → 转写视频C → ...
通道2(LLM):  [等A转完] 抽帧A → [等B转完] 抽帧B → ...
通道3(LLM):  [等A抽完] 三层A → [等B抽完] 三层B → ...
```

### 下载批量预处理

一次性下载全部视频+合并+提取音频，然后逐个转写：

```python
# 批量下载
for name, bvid in videos:
    download_video(bvid, output_path)  # bilibili-api-python
    ffmpeg合并 → ffmpeg提取WAV(16kHz mono)

# 串行转写
for name, bvid in videos:
    faster_whisper转写 → JSON
```

## API限流应对策略

### 429限流时的fallback

1. **LLM抽帧分析**：delegate_task 429失败时，主agent自己读取转写稿compact.txt，人工分析确定抽帧时间点
2. **三层文件生成**：delegate_task 429失败时，等API恢复后重新dispatch
3. **静默失败检测**：子agent返回status=completed但没写文件。必须检查：
   - 三层文件目录下是否有3个MD文件
   - JSON配对文件是否存在且非空
   - 缺失的重新dispatch

### compact.txt临时文件

转写完成后构建 `_compact.txt`（`[MM:SS] 文本` 格式），供LLM抽帧分析读取。三层文件生成完成后必须删除。

## 音频流CDN SSL错误处理

bilibili-api-python下载DASH流时，音频流CDN可能SSL错误。解决方案：
- 使用 `backup_url` 列表中的备用URL
- 遍历所有备份URL直到成功
- 某些视频的特定CDN节点持续故障，换backup_url可解决

## metadata.json 结构

RAG pipeline的metadata.json是dict结构：
```python
meta = json.load(f)  # dict
chunks = meta.get('chunks', [])  # 不是 meta 直接遍历
transcript_count = sum(1 for c in chunks if c.get('source_type') == 'transcript')
```

## 质量检查（入库后loop 3圈）

检查项：
1. raw/transcripts 每个视频目录有3个MD（逐字稿+知识笔记+教学简案）
2. media/ 目录有关键帧图片
3. Wiki页 视频-<标题>.md 存在
4. RAG transcript chunks > 0
5. 临时文件：_compact.txt、.wav、.m4s 已清理
6. _tmp_ 目录无残留
7. 子agent残留的.py文件已清理

## 实际案例

### UP-丁 13个视频
- 总时长 ~300分钟
- 143关键帧
- 36MD（13×3）+ 36Wiki页
- 流程：批量下载→串行转写→并行LLM抽帧→并行三层文件

### UP-乙有美有物理 13个视频  
- 部分视频较短（10-19分钟），LLM抽帧可自己分析（不用delegate_task）
- API限流时fallback到主agent自分析
- 短视频关键帧数较少（7-10帧 vs 长视频12-15帧）
