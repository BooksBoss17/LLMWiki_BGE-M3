# Third-Party Notices

本仓库按原样分发项目自有代码，不包含下列第三方模型权重与依赖的源码或二进制。
安装脚本会在用户机器上从官方来源下载/安装它们。各项版权归各自所有者所有。

## 模型

| 模型 | 用途 | 获取方式 | 许可证 | 来源 |
| --- | --- | --- | --- | --- |
| BAAI/bge-m3 | RAG 向量化（稠密+稀疏检索） | `scripts/download_models.ps1` 从 Hugging Face 下载，约 2.3 GB，不入库 | MIT | https://huggingface.co/BAAI/bge-m3 |

skills 层的导入工具链（见 `skills/_shared/model-tools/requirements/`）按需引用更多模型（OCR、公式识别、语音转写等）。这些属于可选高级功能，其模型体积大且许可证各异（部分权重为非商用许可），本仓库不包含也不默认下载；使用前请自行阅读对应官方仓库的许可证条款。

## 核心 Python 依赖（requirements.txt）

| 依赖 | 用途 | 许可证 | 来源 |
| --- | --- | --- | --- |
| torch 2.11.0 (CUDA 12.8 轮子) | 深度学习运行时 | BSD-3-Clause（本体）；CUDA 轮子含 NVIDIA 组件，随 PyTorch 官方渠道分发 | https://pytorch.org / https://download.pytorch.org/whl/cu128 |
| FlagEmbedding 1.4.0 | BGE-M3 编码器封装 | MIT | https://github.com/FlagOpen/FlagEmbedding |
| faiss-cpu 1.14.3 | 向量索引 | MIT | https://github.com/facebookresearch/faiss |
| numpy 2.4.6 | 数值计算 | BSD-3-Clause | https://numpy.org |
| transformers 5.12.1 | 模型加载 | Apache-2.0 | https://github.com/huggingface/transformers |
| tokenizers 0.22.2 | 分词 | Apache-2.0 | https://github.com/huggingface/tokenizers |
| sentencepiece 0.2.1 | 分词模型 | Apache-2.0 | https://github.com/google/sentencepiece |
| huggingface-hub 1.20.1 | 模型下载 | Apache-2.0 | https://github.com/huggingface/huggingface_hub |
| scikit-learn 1.9.0 | 机器学习工具（FlagEmbedding 依赖） | BSD-3-Clause | https://scikit-learn.org |
| scipy 1.17.1 | 科学计算 | BSD-3-Clause | https://scipy.org |

## 可选工具链依赖

`skills/_shared/model-tools/requirements/` 下定义了若干可选子环境（OCR / 公式识别 / 文档转换 / 语音转写等），涉及 marker-pdf、surya-ocr、PaddleOCR、TexTeller、cnocr/cnstd、faster-whisper、python-docx、lxml、PyMuPDF、opencv、pillow 等第三方包。许可证与官方来源以各自 PyPI 页面与官方仓库为准（其中部分项目的模型权重许可与代码许可不同，个别为 GPL 或非商用条款，再分发前请逐一确认）。本仓库不重新分发这些包及其模型。

## 外部工具

| 工具 | 必需性 | 说明 |
| --- | --- | --- |
| Python 3.10-3.13（推荐 3.11/3.12） | 必需 | https://www.python.org 或 `winget install Python.Python.3.11` |
| NVIDIA GPU 驱动（CUDA 12.x 兼容） | RAG 子系统必需 | https://www.nvidia.com/drivers ；随 torch cu128 轮子使用的 CUDA 运行时由 pip 提供，无需单独安装 CUDA Toolkit |
| Git | 可选（克隆仓库时） | https://git-scm.com |
| Obsidian | 可选（浏览 LLMWiki） | https://obsidian.md |
| LibreOffice / ffmpeg 等 | 可选（仅 skills 导入工具链用到） | 见 `skills/_shared/scripts/libreoffice_runner.py` 等脚本的说明；从各自官网安装 |

> 许可证信息以各项目当前官方发布为准；如与上表有出入，以官方声明为准。
