# marker-pdf 模型与配置参考

## 模型文件清单（共 ~3.3GB）

下载位置: `C:\Users\<用户名>\AppData\Local\datalab\datalab\Cache\models\`（或用户通过 `SURYA_MODEL_CACHE_DIR` 环境变量指定的目录）

| 模型 | Checkpoint | 用途 | 大小 |
|------|-----------|------|------|
| layout | 2025_09_23 | 布局分析（检测标题/段落/图片/表格区域） | 1.4GB |
| text_recognition | 2025_09_23 | 文字识别 (OCR) | 1.4GB |
| text_detection | 2025_05_07 | 文字检测（定位文字位置） | 74MB |
| table_recognition | 2025_02_18 | 表格识别（还原 GFM 表格） | 202MB |
| ocr_error_detection | 2025_02_18 | OCR 纠错（检测识别错误） | 262MB |

模型从 `models.datalab.to` (S3) 下载，首次运行 `marker` 命令时自动触发。每个任务只有一个最新 checkpoint 版本，**没有可选的其他模型**。

## 关键配置

```
TORCH_DEVICE_MODEL = cuda    # 必须是 cuda，不是 cpu
MODEL_DTYPE = torch.float16
S3_BASE_URL = https://models.datalab.to
```

## 当前 CLI 注意

- 不要在工作流里硬写旧式 `--force_ocr`。
- 当前 marker 帮助中 force OCR 通常暴露为布尔 flag `--PdfProvider_force_ocr`；若版本变化，以 `marker --help` 为准。
- 优先通过 `scripts/convert_pdf_with_marker.py` 调用 marker。该脚本会探测 `--PdfProvider_force_ocr` / `--DocumentProvider_force_ocr`，并在 CUDA 不可用时失败退出。

## LLM 增强（可选）

marker 支持 `--use_llm` 启用 LLM 修正 OCR 结果。支持 Gemini / Claude / OpenAI / Ollama / Azure 作为 LLM service。

关键选项:
- `--use_llm` 或 `--PdfConverter_use_llm`：启用 LLM 增强
- `--llm_service`：指定 LLM 服务（如 `marker.services.gemini.GoogleGeminiService`）
- `--LLMEquationProcessor_use_llm`：专门用 LLM 修正公式识别
- `--LLMPageCorrectionProcessor_use_llm`：用 LLM 修正文字识别

## 性能对比

| 模式 | 8 页 PDF | 161 页教材 | GPU 利用率 |
|------|---------|-----------|----------|
| CPU (torch+cpu) | >1 小时（未完成） | 不可行 | 0% |
| GPU (cuda) | 2分41秒 | 7分20秒 | 85% |

CPU 模式不可用，必须配置 CUDA。

## 已知 OCR 局限

1. **中文下标乱码**: $R_{总}$→`\odot`、$R_{副}$→`\text{iii}`、$R_{原}$→`\text{ii}`、$t_{上}$→`\perp`、$t_{下}$→`\mp`
2. **西里尔字母**: 答案表 C/B 可能识别为 С/В
3. **中文文字偶发乱码**: 如"故选"→"执法"
4. **公式末尾多余下标**: 如 `= 0.10 s_{\odot}`（末尾不应有下标）

## 教材 PDF 后处理问题 (v2.3+ 实测，161页鲁教版物理教材)

marker 转换教材类 PDF 后存在系统性后处理问题，需要批量清理。详见
`multi-vault-wiki-management` skill 的 `references/textbook-import-workflow.md`
中的 "Post-Conversion OCR Cleanup" 章节。

### 问题分类

1. **装饰性大字乱码 (H1 级)**：章节标题页装饰字被识别为 H1（`# 猪论`→绪论、
   `# 半频速频度槽`→牛顿运动定律等）。扫描所有 H1，非章标题/书名的直接删除。
2. **标题层级不一致**：相同结构的标题被分配不同层级。需按统一标准重映射
   （H1=书名, H2=章, H3=节, H4=小标题, H5=栏目）。
3. **重复文本乱码**：短语重复 50-100+ 次。检测：`(.{5,20})\1{3,}`，替换为
   HTML 注释占位。
4. **OCR 字符替换**：`相互作開→相互作用`、`総略/瓮略/亲略/祭略→策略`、
   `示警全安→安全警示`（2字词反转）、`笋5寸→第5节`、`招重与生重→超重与失重`、
   `┏ҕ法点拨→方法点拨`、`例 题→例题`（单字间多空格）。
5. **标题装饰符号残留**：`▶▶`、`●●●`、`①`、`₽`、`[]`、`**` 需清除。
6. **目录页表格破碎**：marker 将目录识别为 GFM 表格但格式乱。直接重写为
   嵌套 markdown 列表 + 页码。
7. **重复章标题**：每节开头重复 `## 第N章 章名`。保留首次，删除后续。
8. **"解" 标题层级不统一**：出现在 H3/H4/H5 各级。统一为 `##### 解`。
9. **章标题页格式**：`## 第N章` 和章名分两行→合并；`第4章`缺`#`→补`##`；
   `导 入`→`导入`；`第 4 节`→`第4节`。

### 验证方法

所有修复完成后运行最终扫描确认 0 残留：检查非书名 H1、标题中的 `**`、
OCR 错误字符串、重复文本、已知空格词、装饰符号、"解"标题层级。
