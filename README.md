# LLMWiki_BGE-M3

本地教学知识库与 RAG 检索框架：把 Markdown 教学资料（教材 / 标准 / 题库 / 讲义 / 视频知识笔记）切片、用 **BGE-M3** 向量化并建立 **FAISS** 索引，支持稠密 / 稀疏 / 混合三种检索模式；配套 Obsidian Wiki 库与结构化学生数据（SQLite）子系统。面向 Windows，一条命令安装，一条命令启动。

> 本仓库是**框架发布版**：不包含原作者的本地知识库内容（教材、题库、讲义、视频转写、学生数据等均未发布），只内置少量无版权问题的示例数据用于演示完整流程。

## What is this?

一个"四库"结构的个人教学知识库框架：

| 目录 | 角色 | 说明 |
| --- | --- | --- |
| `raw/` | 知识源库 | 清洗后的教学 Markdown，是 RAG 索引的唯一内容来源 |
| `LLMWiki/` | Wiki 库 | 自包含 Wiki 页面 + wikilinks 图谱，附带 Obsidian 配置 |
| `BGE-M3/` | RAG 库 | 切片 → BGE-M3 向量化 → FAISS 索引 → 检索 |
| `StudentDataSQL/` | 结构化数据 | 学生成绩/作业的 SQLite 框架，含迁移、导入、脱敏视图与合成演示数据 |

另有 `skills/`（Agent 能力与导入工具链框架）、`source-library/`（二进制原件库，只跟踪说明）。
机器可读路径契约见 `PROJECT_LAYOUT.yaml`，治理规则见 `AGENTS.md` 与 `SCHEMA.md`。

## Features

- **RAG 检索管线**：按文档类型（教材按标题层级切片、题目整题一片）自动切片，BGE-M3 同时产出稠密向量与稀疏词权重，FAISS IndexFlatIP 稠密检索 + 类 BM25 稀疏检索 + 混合模式；
- **交互式检索入口**：`run.bat` 一键启动，首次自动用 `raw/` 资料建索引；
- **模型自动下载**：BGE-M3 权重（约 2.3 GB）由脚本从 Hugging Face 官方仓库下载，支持断点续传与完整性校验；
- **路径契约**：所有脚本通过 `PROJECT_LAYOUT.yaml` 解析路径，无硬编码本机路径，项目可放在任意目录（含带空格路径）；
- **学生数据子系统**：SQL 迁移脚本、CSV 模板、合成演示数据、脱敏安全视图，真实数据默认加密（SQLCipher）且永不入库；
- **Agent 工作流框架**：skills 注册表、导入批次契约、质量门禁（高级用法，见 `AGENTS.md`）。

## System Requirements

| 项目 | 要求 |
| --- | --- |
| 操作系统 | Windows 10 / Windows 11（x64） |
| Python | 3.10 – 3.13（推荐 3.11 / 3.12；本发布版在 3.13 上完整验证，原作者环境为 3.11） |
| GPU（RAG 子系统） | **NVIDIA GPU（CUDA 12.x 兼容驱动）必需**。已验证 RTX 4070 12 GB；建议 ≥8 GB 显存 |
| 磁盘 | 约 10 GB 空闲（虚拟环境 + 依赖约 7 GB，模型约 2.3 GB） |
| 内存 | 建议 16 GB 及以上 |
| 网络 | 首次安装需联网（PyPI / PyTorch 官方源 / Hugging Face） |

> ⚠️ **GPU 说明（如实声明）**：RAG 入库与检索依赖 BGE-M3 向量化，其编码器多进程路径在无 CUDA 设备时会失败（实测 CPU-only 环境报 `ZeroDivisionError`），因此本仓库把 GPU 列为 RAG 子系统的必要条件。Wiki 浏览与 StudentDataSQL 子系统不需要 GPU。不要期望无 NVIDIA 显卡的机器能运行 RAG 功能。

## Installation

### 方式一：Git 克隆（推荐）

```bat
git clone https://github.com/BooksBoss17/LLMWiki_BGE-M3.git
cd LLMWiki_BGE-M3
setup.bat
```

### 方式二：Download ZIP

1. 在 GitHub 页面点击 **Code → Download ZIP** 并解压到任意目录（路径可以含空格和中文）；
2. 双击或命令行运行 `setup.bat`。

`setup.bat` 会自动完成：

1. 检测 Python（3.10–3.13，优先 3.11/3.12）；
2. 创建项目本地虚拟环境 `BGE-M3\runtime\env`（不污染系统 Python）；
3. 安装依赖（`requirements.txt`，含 CUDA 12.8 版 PyTorch，首次下载约 3 GB）；
4. 验证关键依赖与 CUDA 可用性；
5. 创建运行时目录；
6. 从 Hugging Face 下载 BGE-M3 模型（约 2.3 GB，已存在则跳过）；
7. 路径契约自检。

可选参数：`setup.bat -SkipModel`（跳过模型下载，稍后运行 `scripts\download_models.ps1`）。

## Running

```bat
run.bat
```

- 首次运行会用 `raw/` 中的示例资料自动构建索引，然后进入交互式检索；
- 交互模式内：输入问题回车检索，`/mode dense|sparse|hybrid` 切换模式，`/k N` 改返回条数，`quit` 退出；
- 单次查询：`run.bat --query "自由落体运动的规律" --k 3`。

### 放入你自己的资料

把你自己的教学 Markdown 放入 `raw/` 对应目录（目录约定见 `raw/README.md` 与各示例文件的格式）：

```text
raw/
├─ textbooks/<书名>/<章节>.md        # 按 # 标题层级切片
├─ standards/<文档>.md               # 按 H2/H3 切片
├─ exercises/<大类>/<子分类>/<题目>.md # 每题一个 chunk（frontmatter 含 id/知识点/难度）
├─ lectures/<讲义名>/…               # 讲义本体 + *_设计.md
└─ transcripts/<频道>/<视频>/…_知识笔记.md
```

然后重建索引并检索：

```bat
run.bat --build
```

（等价于运行 `BGE-M3\scripts\rag_pipeline.py`。）

## Models

| 模型 | 用途 | 大小 | 许可证 | 获取 |
| --- | --- | --- | --- | --- |
| BAAI/bge-m3 | 向量化（稠密+稀疏） | 约 2.3 GB | MIT | `scripts\download_models.ps1` 从 https://huggingface.co/BAAI/bge-m3 下载 |

- **为什么不直接放进仓库**：体积超过 GitHub 建议上限，且按"模型不入库、脚本下载"的发布策略管理；
- 模型存放于 `BGE-M3/runtime/models/BAAI/bge-m3`（已被 `.gitignore` 忽略）；
- 下载脚本幂等、支持断点续传、下载后校验必需文件；
- 网络受限环境可设置镜像：`$env:HF_ENDPOINT = "https://hf-mirror.com"` 后重试。

## External Tools

| 工具 | 必需性 | 安装 |
| --- | --- | --- |
| Python 3.10-3.13 | 必需 | https://www.python.org/downloads/ 或 `winget install Python.Python.3.11` |
| NVIDIA 显卡驱动 | RAG 必需 | https://www.nvidia.com/drivers （无需单独安装 CUDA Toolkit，CUDA 运行时随 pip 轮子提供） |
| Git | 克隆时需要 | https://git-scm.com |
| Obsidian | 可选（浏览 Wiki） | https://obsidian.md ，用它打开 `LLMWiki/` 目录即可 |

skills 层的高级导入工具链（PDF/OCR/公式识别/语音转写）依赖更多外部环境，属于可选项，见 `skills/_shared/model-tools/README.md` 与 `THIRD_PARTY_NOTICES.md`。

## Data

- **本仓库不包含原作者的任何本地知识库内容**：真实教材、题库、讲义、视频转写、试卷分析、学生数据全部未发布；
- `raw/` 中的示例文件均为原创演示内容（"示例"前缀），无版权问题，可安全删除后放入你自己的资料；
- `StudentDataSQL/templates/` 提供 CSV 导入模板，`tests/fixtures_synthetic/` 是合成演示数据；真实学生数据属隐私数据，默认存放于 `StudentDataSQL/runtime/`（已被 Git 忽略）且开启加密要求；
- 二进制原件（PDF/DOCX 等）放 `source-library/`（Git 不跟踪其中内容）。

## Configuration

项目零配置即可运行。路径契约集中在 `PROJECT_LAYOUT.yaml`，可用环境变量覆盖：

| 环境变量 | 作用 |
| --- | --- |
| `LLMWIKI_KB_ROOT` | 覆盖项目根目录（默认从脚本位置自动发现） |
| `LLMWIKI_MODEL_RUNTIME_ROOT` | 覆盖共享模型运行库位置 |
| `HF_ENDPOINT` | Hugging Face 下载镜像（如 `https://hf-mirror.com`） |
| `HF_TOKEN` | 可选，访问受限模型仓库时使用；**任何情况下不要把 Token 写进仓库文件** |

本仓库不需要 API Key；RAG 检索完全本地运行，不调用外部 LLM 服务。

## Troubleshooting

<details>
<summary><b>Python 未安装或版本不符</b></summary>

`setup.bat` 报"未找到 Python 3.10-3.13"：从 https://www.python.org/downloads/ 安装 3.11/3.12（勾选 *Add python.exe to PATH*），或 `winget install Python.Python.3.11`，然后重跑 `setup.bat`。
</details>

<details>
<summary><b>PowerShell 执行策略限制</b></summary>

`setup.bat` 已自动带 `-ExecutionPolicy Bypass` 参数，无需手动改系统策略。若手动运行 ps1 报执行策略错误，使用：
`powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1`
</details>

<details>
<summary><b>模型下载失败 / 速度慢</b></summary>

- 重跑 `scripts\download_models.ps1`，已下载部分自动续传；
- 网络受限时设置镜像：`$env:HF_ENDPOINT = "https://hf-mirror.com"` 再运行；
- 校验失败会列出缺失文件，完整删除 `BGE-M3\runtime\models\BAAI\bge-m3` 后重下。
</details>

<details>
<summary><b>GPU / CUDA 问题</b></summary>

- `run.bat` 或 setup 提示未检测到 CUDA：确认 NVIDIA 显卡 + 官方驱动已装（`nvidia-smi` 能否正常输出）；
- RAG 向量化必须有 NVIDIA GPU（见 System Requirements），无 GPU 时 Wiki 与 StudentDataSQL 子系统仍可用；
- 显存不足（OOM）：关闭其他占显存程序后重试；索引构建默认 batch_size=16，可在具备管理员权限时调整 `BGE-M3/scripts/rag_pipeline.py`（见 SCHEMA.md 锁定区规则）。
</details>

<details>
<summary><b>路径包含空格或中文</b></summary>

脚本已按带引号路径处理，克隆/解压到含空格目录（如 `C:\Test Project\LLMWiki_BGE-M3`）可以正常工作；若手动调用其中脚本，请给路径加引号。
</details>

<details>
<summary><b>网络问题导致 pip 安装失败</b></summary>

- 重跑 `setup.bat`（pip 会续用缓存）；
- 公司/校园网络可配置镜像后重试：`pip config set global.index-url https://pypi.org/simple`（或你信任的镜像）。
</details>

## Repository Layout

```text
LLMWiki_BGE-M3/
├─ README.md / CHANGELOG.md / AGENTS.md / SCHEMA.md / PROJECT_LAYOUT.yaml
├─ setup.bat / run.bat            # 一键安装 / 一键启动
├─ requirements.txt               # RAG 子系统依赖（GPU 版）
├─ scripts/
│  ├─ setup.ps1                   # 安装编排
│  ├─ download_models.ps1|.py     # BGE-M3 模型下载
│  └─ query.py                    # 交互式检索入口
├─ BGE-M3/                        # RAG 库（scripts + tests；runtime/ 本地生成）
├─ StudentDataSQL/                # 学生数据框架（迁移/脚本/模板/合成示例）
├─ raw/                           # 知识源（内置示例，替换为你自己的资料）
├─ LLMWiki/                       # Wiki 库（Obsidian 配置 + 占位索引）
├─ source-library/                # 二进制原件库（只含说明）
├─ skills/                        # Agent 能力与工具链框架
└─ LICENSE / THIRD_PARTY_NOTICES.md / OPEN_SOURCE_AUDIT.md
```

## License

本项目采用 [MIT License](LICENSE)（Copyright (c) 2026 BooksBoss17）。

## Third-party licenses

模型、依赖与外部工具的许可证汇总见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
