# BGE-M3 框架变更记录

## 2026-07-13

- 将 `.venv`、`models`、`data`、`output` 迁入 `runtime/{env,models,data,index}`。
- `rag_pipeline.py` 仅切换到统一路径 resolver，未改变切片、向量化或检索语义。
- 新增 `config/`、`tests/` 与 `runtime/logs/` 目录契约。
