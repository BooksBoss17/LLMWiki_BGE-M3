# Open Source Audit

发布就绪度审计报告（由开源整理流程生成，2026-09-25）。

## Source

原始项目：`本机原项目 LLMWiki_BGE-M3（路径已脱敏）`（约 62 GB，含运行时与知识内容；整理全程只读，未做任何修改）

## Output

开源项目：`本仓库目录 LLMWiki_BGE-M3_OpenSource`（488 个 Git 跟踪文件，约 4.8 MB；不含模型、虚拟环境与索引等运行时产物）

## Removed

从发布版本排除的内容类别：

- **用户知识库内容**：`raw/` 全部真实资料（教材、题库 1400+ 题、试卷分析报告、讲义、视频转写）；`LLMWiki/` 全部 Wiki 页面与 `_meta/audits/` 个人工作流审计记录 —— 以原创示例数据（7 个文件，覆盖全部 5 种来源类型）替代
- **本地向量数据库**：`BGE-M3/runtime/index/`（FAISS 索引、embeddings、稀疏权重）与 `runtime/data/chunks.jsonl`
- **下载的模型权重**：`BGE-M3/runtime/models/`（BGE-M3 约 2.2 GB）、`skills/_shared/model-tools/runtime/`（约 37 GB 多环境模型运行库）、`BGE-M3/runtime/models/hf-cache/`
- **虚拟环境**：`BGE-M3/runtime/env/`（4.9 GB）、`skills/_shared/model-tools/runtime/envs|python/`
- **缓存与临时文件**：`__pycache__`、`.pytest_cache`、`tmp/`（3.7 GB 临时工作区）、`output/` 交付物、`wmf_png/` 运行残留
- **二进制原件库内容**：`source-library/` 12 GB 原件（PDF/DOCX/音视频），仅保留目录说明 README
- **运行状态**：`skills/_runtime/`、`skills/_ops/audits/` 未提交的工作流状态 JSON
- **个人数据**：`StudentDataSQL/runtime/`（真实学生数据库、导入导出、备份、报告）；框架与合成演示数据保留

## Models

| Model | Required | Included | Download method | License |
| ----- | -------- | -------- | --------------- | ------- |
| BAAI/bge-m3（稠密+稀疏向量化） | RAG 子系统必需 | 否（2.2 GB，不入库） | `scripts\download_models.ps1`（Hugging Face 官方仓库，幂等、断点续传、文件校验；支持 `HF_ENDPOINT` 镜像） | MIT |
| skills 导入工具链的可选模型（OCR/公式识别/语音转写等） | 可选高级功能 | 否 | 见 `skills/_shared/model-tools/requirements/` 各子环境说明 | 各异（部分非商用，见 THIRD_PARTY_NOTICES.md 警示） |

## External Tools

| Tool | Required | Installation | Redistributed | License/Source |
| ---- | -------- | ------------ | ------------- | -------------- |
| Python 3.10-3.13 | 必需 | python.org 安装包或 `winget install Python.Python.3.11`（setup.bat 自动探测） | 否 | PSF License / python.org |
| NVIDIA GPU 驱动（CUDA 12.x 兼容） | RAG 子系统必需 | nvidia.com 官方驱动（CUDA 运行时随 pip 轮子分发，无需 CUDA Toolkit） | 否 | NVIDIA |
| PyTorch 2.11.0+cu128 | 必需（pip 依赖） | `requirements.txt` 从 download.pytorch.org 官方源安装 | 否 | BSD-3 / pytorch.org |
| Git | 克隆时需要 | git-scm.com | 否 | GPLv2 |
| Obsidian | 可选（浏览 LLMWiki） | obsidian.md | 否（仅附带 Obsidian 配置 JSON） | 官网 |
| LibreOffice / ffmpeg 等 | 可选（仅 skills 导入工具链） | 各官网（脚本内含指引） | 否 | 各自官方 |

## Dependencies

`requirements.txt`（依据原项目实际运行环境 `BGE-M3/runtime/env` 的已验证版本整理，非 pip freeze 盲拷）：

torch==2.11.0+cu128、FlagEmbedding==1.4.0、faiss-cpu==1.14.3、numpy==2.4.6、transformers==5.12.1、tokenizers==0.22.2、sentencepiece==0.2.1、huggingface-hub==1.20.1、scikit-learn==1.9.0、scipy==1.17.1。

skills 层可选工具链依赖保留在 `skills/_shared/model-tools/requirements/`（6 个子环境定义文件，随仓库分发但不随 setup.bat 安装）。

## Secrets Audit

**PASS** —— 全仓库扫描（API key / token / secret / password / 凭据赋值模式、.env、credentials、证书、SSH key）未发现任何真实 Secret；命中关键词均为普通词汇（文件名 token、TOKENIZERS_PARALLELISM 环境变量、文档语句）。仓库不需要任何 API Key 即可运行（RAG 完全本地）。

## Absolute Path Audit

发现与处理（共 20+ 处，全部修复为相对路径 / `%USERPROFILE%` / `%LOCALAPPDATA%` / 环境变量推导）：

| 位置 | 问题 | 处理 |
| --- | --- | --- |
| `PROJECT_LAYOUT.yaml` | `integration.skills-mirror` 指向本机外部目录 `../hermes_base/...` | 改为 `tmp/integration-skills-mirror`，保留 `LLMWIKI_INTEGRATION_SKILLS_ROOT` 环境变量覆盖机制 |
| `skills/_registry/migration_map.csv` | 14 行含 `C:\Users\<本机用户名>\...` 绝对路径 | 剥离为仓库相对路径 |
| `skills/_shared/conventions/llmwiki-bge-m3-conventions.md` | 引用本机另一私有知识库 `hermes_base\kb-arch\` | 对比表改写为通用表述 |
| `chinese-handwriting-formula-transcriber`（SKILL.md / manifest.yaml / architecture.md / 3 个 ps1） | 硬编码 `C:\Users\<本机用户名>\Desktop\ocr_private_runs\...` 私有目录 | 改为 `%USERPROFILE%\Desktop\...` / `$env:USERPROFILE` 推导 |
| 5 个 skills 文档 | 示例命令含本机用户名路径 / AppData 路径 / 外部暂存区 | 泛化为 `<you>` / `%LOCALAPPDATA%` / 占位说明 |
| `BGE-M3/scripts/kb_maintenance_check.py`（及其 skills 副本） | 文档字符串引用私有知识库名；`RAG_DIR` 指向已废弃的 `BGE-M3/output` 旧路径 | 改为自包含表述；指向 `BGE-M3/runtime/index` 现行路径 |

最终全仓库扫描：`<本机用户名>`、`hermes_base`、`C:\Users` 引用 **0 处残留**。所有脚本经 `PROJECT_LAYOUT.yaml` 路径契约解析目录，项目可运行于任意磁盘位置（含空格/中文路径，已实测）。

## Installation Test

在 `本仓库目录 LLMWiki_BGE-M3_OpenSource` 实际运行 `setup.bat`（真实全流程，非模拟）：

| 步骤 | 结果 |
| --- | --- |
| Python 检测（3.13 自动发现） | PASS |
| 创建 venv `BGE-M3\runtime\env` | PASS |
| pip 安装依赖（torch cu128 2.75 GB 从 PyTorch 官方源真实下载 + 全部依赖） | PASS |
| 关键 import 验证（torch/faiss/FlagEmbedding） | PASS |
| CUDA 检测（RTX 4070 识别） | PASS |
| 运行时目录创建 | PASS |
| 模型下载 | **PARTIAL**（见下） |
| 路径契约自检 | PASS |
| 整体退出码 | 0 |

**模型下载 PARTIAL 说明**：本机当前网络 huggingface.co 与 hf-mirror.com 均连接超时（curl 实测均不可达），2.3 GB 完整在线下载在本环境无法完成。已验证的部分：下载脚本的错误处理与降级路径（连接超时时给出明确原因、镜像指引、setup 继续）；"模型已存在则跳过"路径（把原项目同一官方权重复制到位后重跑，正确识别并跳过，退出码 0）。下载逻辑本身基于 huggingface_hub 标准 `snapshot_download`（断点续传/校验），但**完整在线下载流程未在本环境实测**，网络正常环境下如遇问题请反馈。

**空格路径测试**：将仓库导出到 `Test Project\LLMWiki_BGE-M3（带空格测试目录）`，全新运行 `setup.bat`（重建 venv + 依赖安装 + 模型识别）→ 退出码 0；`run.bat` 建索引 + 检索正常。**PASS**。

## Startup Test

实际运行 `run.bat`（开源副本目录）：

1. 检测 venv / 模型 → PASS
2. 索引缺失时自动调用 `BGE-M3/scripts/rag_pipeline.py`：扫描 `raw/` 示例数据 23 chunks（textbook/standard/exercise/lecture/lesson_design/transcript 六类）→ GPU 向量化 → FAISS 索引（23×1024）→ 持久化 → **PASS**
3. 交互检索：`run.bat --query "自由落体运动的规律是什么" --k 3` 返回语义正确排序（自由落体教材节 0.669 分居首）；sparse 模式 `--query "磁通量" --mode sparse` 正确命中磁通量讲义 → **PASS**
4. 附带测试脚本：`test_retrieval.py`（6 组查询）、`test_bge_m3.py`（GPU 编码相似度）、`BGE-M3/tests/test_runtime_paths.py`（2 用例）、`kb_maintenance_check.py`（静默通过）→ **PASS**
5. StudentDataSQL 子系统：`init_db.py --dev`（8 个迁移）→ `import_synthetic_demo.py --dev` → `query_student_data.py --dev --view class_knowledge_summary`（脱敏视图查询返回合成班级数据）→ **PASS**

## Known Limitations

- **NVIDIA GPU 为 RAG 子系统硬性要求**：原版检索管线在无 CUDA 设备时直接崩溃（FlagEmbedding 多进程池 0 设备 → ZeroDivisionError，实测确认），故不宣称支持 CPU 运行 RAG；Wiki 与 StudentDataSQL 子系统无 GPU 要求。已验证硬件：RTX 4070 12 GB。
- **模型约需 2.3 GB 磁盘 + 首次下载需联网**（Hugging Face；网络受限环境用 `HF_ENDPOINT=https://hf-mirror.com`）。
- 依赖安装需下载约 3 GB（CUDA 版 torch），磁盘总需求约 10 GB。
- 2.3 GB 模型完整在线下载流程因当前网络限制未在本环境实测（详见 Installation Test）。
- skills 层高级导入工具链（marker-pdf / PaddleOCR / TexTeller / faster-whisper 等，约 37 GB 模型运行库）**未纳入 setup.bat，也未在干净环境验证**，作为可选高级功能随框架源码与需求定义文件分发。
- 未在其他 Windows 版本 / 其他 GPU / 纯 CPU 机器上测试。

## GitHub Readiness

**READY**（许可证已由项目所有者选定：MIT，Copyright (c) 2026 BooksBoss17，2026-09-25 生效；克隆 → `setup.bat` → `run.bat` 全流程已在干净虚拟环境 + 空格路径下实测通过）

发布前剩余待办（均非阻塞）：

1. （可选）审视 `AGENTS.md` 第 8 行的教师背景描述是否保留；
2. 自行执行 `git push` 发布（按约定本任务不执行任何发布操作）。

README 中的克隆地址已预填为 `https://github.com/BooksBoss17/LLMWiki_BGE-M3.git`；若实际仓库名不同请同步修改。

## Privacy Pass（发布后隐私复查，2026-09-25）

公开后对全部已推送内容（含 git 历史）复查，处理了以下个人信息痕迹（均已泛化/脱敏，并通过历史重写彻底移除）：

- 审计文档中的本机用户名路径（含 Windows 用户名）→ 已脱敏；
- 学生数据模板中疑似真实学生示例行（班级+座位+姓氏）→ 替换为合成演示行；
- 预处理脚本中的真实班级配置（班级号/班号文件标识）→ 泛化为示例班级（高二01班/高二02班）；
- 视频导入参考文档中的真实 B 站 UP 主频道名与视频 BV 号 → 泛化为 UP-甲/乙/丙/丁 与占位 BV；
- 检索规则文档中的同事称呼（真实姓氏与作者昵称）→ 改为"教师A/教师B"。

复查同时确认：无邮箱/手机号/身份证号/IP/Token 泄露；远端提交者均为 noreply 身份。AGENTS.md 中的职业背景自述与 .codex 内部模型代号经所有者确认保留。
