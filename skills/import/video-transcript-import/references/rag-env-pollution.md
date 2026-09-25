# RAG 重建环境污染排查记录

## 触发场景

在 Hermes 会话中导入 B站视频后，运行 `BGE-M3/runtime/env/Scripts/python.exe scripts/rag_pipeline.py` 重建 RAG。

## 症状

- 扫描阶段正常，能看到新增视频知识笔记并统计到新的 chunk 数。
- 到 `[2/3] 向量化 + 构建 FAISS 索引...` 后崩溃。
- 退出码为 `139` / `Segmentation fault`。
- 旧的 `metadata.json` 未更新，新增视频标题不在 metadata 中。
- 若命令写成 `... rag_pipeline.py | tail -10`，管道可能让外层看起来 exit 0，但实际 Python 进程未完成保存。

## 根因

Hermes 会话可能设置了：

```text
PYTHONPATH=%LOCALAPPDATA%\hermes\hermes-agent;...
```

导致即使调用 `BGE-M3/runtime/env/Scripts/python.exe`，`sys.path` 前部仍优先加载 Hermes Agent 的包和 venv。`FlagEmbedding`、torch、CUDA、FAISS 或 DLL 组合被混用后，底层直接 segfault。

## 最小复现

```bash
cd BGE-M3
.venv/Scripts/python.exe - <<'PY'
from FlagEmbedding import BGEM3FlagModel
model = BGEM3FlagModel('models/BAAI/bge-m3', use_fp16=True)
out = model.encode(['测试文本'], batch_size=1, max_length=1024, return_dense=True, return_sparse=True, return_colbert_vecs=False)
print(out['dense_vecs'].shape)
PY
```

如果此命令 segfault，检查 `PYTHONPATH` 和 `sys.path`。

## 修复命令

始终用干净环境运行 BGE-M3，并按当前 shell 选择命令。

PowerShell / Codex：

```powershell
cd <KB_ROOT>
python .codex/helpers/windows_utf8_guard.py rag --kb-root .
```

PowerShell 手工清理：

```powershell
cd <KB_ROOT>/BGE-M3
$env:PYTHONPATH=$null; $env:PYTHONHOME=$null
& runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

CMD：

```bat
cd /d <KB_ROOT>\BGE-M3
set PYTHONPATH=
set PYTHONHOME=
runtime\env\Scripts\python.exe scripts\rag_pipeline.py
```

MSYS/bash/POSIX：

```sh
cd BGE-M3
env -u PYTHONPATH -u PYTHONHOME runtime/env/Scripts/python.exe scripts/rag_pipeline.py
```

验证模型最小编码：

```sh
cd BGE-M3
env -u PYTHONPATH -u PYTHONHOME .venv/Scripts/python.exe - <<'PY'
from FlagEmbedding import BGEM3FlagModel
model = BGEM3FlagModel('models/BAAI/bge-m3', use_fp16=True)
out = model.encode(['测试文本'], batch_size=1, max_length=1024, return_dense=True, return_sparse=True, return_colbert_vecs=False)
print(out['dense_vecs'].shape, len(out['lexical_weights']))
PY
```

## 入库后必须验证

不要只相信终端最后几行。读取：

```text
BGE-M3/runtime/index/metadata.json
```

检查：

- `total_chunks` 是否更新。
- `total_vectors == total_chunks`。
- `chunks` 中是否包含新增视频标题。
- `source_type='transcript'` 数量是否增加。

## 禁止模式

```bash
python scripts/rag_pipeline.py | tail -10
```

原因：`tail` 可能提前关闭管道，使 Python 进程在向量化/保存阶段被中断，但外层只看到 `tail` 的返回码。
