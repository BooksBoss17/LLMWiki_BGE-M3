# LLMWiki 共享模型运行库

`skills/_shared/model-tools/` 是多个 Skill 共用的 OCR、文档解析、公式识别、语音转录和 portable tools 的唯一本地运行契约。

可跟踪且可导出到整合包的文件位于本目录根部：

- `registry.yaml`：带官方来源的框架、工具和模型清单。
- `profiles.yaml`：面向任务、质量优先的默认选择与降级方案。
- `requirements/`：可重现的环境依赖。
- `scripts/`：模型解析、环境安装、迁移、诊断和基准测试入口。
- `tests/`：路径、模式和失败行为测试。

大型本地资产位于 `runtime/` 下，严禁提交到 Git；整合包仅在显式 `--include-runtime` 授权下按 registry allowlist 复制：

```text
runtime/
  models/
  envs/
  tools/
  python/
  benchmarks/
  .staging/
```

运行库根目录默认为上述 `runtime/`，可用 `LLMWIKI_MODEL_RUNTIME_ROOT` 覆盖。
Skill 必须通过模型 ID 解析路径，不得硬编码 Skill 本地 `models/` 目录或用户缓存路径。
工具同样按组件 ID 解析；bundled Node、FFmpeg、LibreOffice、uv、OfficeCLI 和 MCP server 不得回退为必需的全局依赖。

当前质量优先的默认选择：整页复杂文档使用 `PaddleOCR-VL-1.6 + PP-DocLayoutV3`，普通中英文文字使用 `PP-OCRv6 medium` 检测器和识别器，公式使用 `PP-FormulaNet_plus-L + TexTeller` 交叉证据。Pix2Text 及其专属运行资产已撤除；CnOCR 和 CnSTD 仍保留安装与历史基准，但不在默认候选链中。

常用命令：

```powershell
python skills/_shared/model-tools/scripts/model_runtime.py resolve --id paddleocr-vl-1.6 --json
python skills/_shared/model-tools/scripts/model_runtime.py doctor --all --json
python skills/_shared/model-tools/scripts/model_runtime.py exec --id ffmpeg-8.1.1 -- -version
python skills/_shared/model-tools/scripts/mcp_call.py --server bilibili-search --tool bilibili-hot --args-json '{}' --json
powershell -ExecutionPolicy Bypass -File skills/_shared/model-tools/scripts/setup_runtime.ps1 -Scope all
powershell -ExecutionPolicy Bypass -File skills/_shared/model-tools/scripts/migrate_runtime_assets.ps1 -Apply -UserAuthorized
```

学生私有图片、裁剪图、转录正文和私有绝对路径不属于本运行库。这些内容仍保存在各 Skill 指定的桌面私有运行根目录中。
此处的基准测试摘要只保存已脱敏的测试样例 ID、聚合指标和配置结论。
