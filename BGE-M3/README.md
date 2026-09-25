# BGE-M3 教学 RAG 库

本库保存教学 RAG 的配置、脚本、测试和本机运行资产。切片与检索语义由锁定的 `scripts/rag_pipeline.py` 管理。

```text
BGE-M3/
├── config/              轻量配置
├── scripts/             构建、检索和维护脚本
├── tests/               路径与回归测试
└── runtime/             Git 忽略
    ├── env/             Python 环境
    ├── models/          BGE-M3 权重
    ├── data/            chunks.jsonl
    ├── index/           FAISS、稀疏权重、metadata
    └── logs/            运行日志
```

安全运行入口：

```powershell
BGE-M3\scripts\run_rag_safe.bat
```
