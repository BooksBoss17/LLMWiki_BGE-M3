---
name: BeMarkdown
description: "Convert Word (DOCX) and PDF files to clean Markdown. Use for OMML/EQ, legacy Equation OLE, MathType MTEF v3/v5, WMF preview/OCR, PNG alt, marker-pdf/pymupdf4llm, and combined image-PDF OCR. Enforces MTEF→MathML→KB-LaTeX structure, PP-FormulaNet/TexTeller visual evidence, hash-bound review, resumable audits, and failure blocking."
version: 2.10.0
author: Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [Markdown, DOCX, PDF, Conversion, Documents]
    related_skills: [ocr-and-documents]
---

# BeMarkdown — Word/PDF → Clean Markdown

> 用于把 DOCX/PDF 转为可入库 Markdown。优化原则：不拆散工具决策闭环；保留 DOCX 四类公式、PDF 三类路径、WMF→LaTeX、中文下标修复、marker GPU、VLM 兜底等关键积累；压缩重复实测表和长代码。

## 0. 核心原则

1. **先诊断再转换**：不要凭扩展名盲选工具。先判断 DOCX 公式/图片格式或 PDF 文本质量。
2. **DOCX 首选结构化提取**：`omml_docx_to_md.py` 是主工具，OMML 按 ECMA-376/ISO 29500 Office Math 语义解析，EQ 按 Microsoft Word 官方域语义解析；未知、不可移植或损坏结构必须写入 `formula_conversion_report.json` 并阻断最终 Markdown，禁止静默压平。
3. **旧公式结构优先并失败阻断**：精确关联 `OLEObject`、`Equation Native` 和 WMF 预览，记录 DOCX/OLE/MTEF/WMF 四层 SHA-256。MTEF v3/v5 严格解析为 XML AST→MathML→KB-LaTeX；未知记录、未消费字节、未映射字形或损坏容器一律阻断。只有 MTEF、PP-FormulaNet、TexTeller 三源保守规范化后完全一致，渲染几何无风险且不命中高风险结构，才可 `machine_final`。其余必须 hash 绑定视觉复核；独立 WMF 只能复核为 `latex`、`text` 或 `image`。
4. **PDF 先诊断后转换**：先用 `diagnose_pdf.py` 判定 `text_pdf`、`mixed_pdf`、`image_pdf`；纯文本且公式完整时用 pymupdf4llm。
5. **图片型 PDF 用组合转写**：扫描件、图片型试卷、公式碎片化或混合排版 PDF 默认用 `combine_image_pdf_ocr.py` 收集 marker 和 `chinese-handwriting-formula-transcriber` 的结构化候选；候选可来自 PaddleOCR-VL、PP-OCRv6、PP-FormulaNet、TexTeller、region OCR 和显式 auto transcript。marker 和任何单一模型都只是候选来源，不是最终稿。
6. **VLM 只审疑难块**：agent VLM 只处理 `vlm_review_tasks.jsonl` 中的低置信度公式、图像题、表格、核素符号、题号边界等冲突块；不要逐页全量消耗上下文。
7. **转换后必须质检**：无 data URI、无 WMF 残留、无中文下标乱码、无断链图片、LaTeX 闭合、图片语义正确。

## 1. 工具决策树

```text
DOCX
├─ document.xml 有 <m:oMath>                → omml_docx_to_md.py（OMML→LaTeX）
├─ 有 <w:fldChar>/<w:instrText> 且 eq       → omml_docx_to_md.py（EQ→LaTeX）
├─ 有 Equation OLE + WMF                    → MTEF v3/v5 结构解析 + WMF 三源复核
├─ 只有独立 word/media/*.wmf                → WMF 渲染 + 双 OCR + 强制视觉分类
├─ 无 OMML/EQ/WMF，但 PNG 图片 alt 含公式   → mammoth + replace_images_with_latex.py
└─ 普通文本/表格                            → markitdown 或 omml_docx_to_md.py

PDF
├─ 文本完整、公式可读                       → pymupdf4llm + 后处理
├─ 图片型/扫描/混合文本图片/公式碎片化      → combine_image_pdf_ocr.py（多候选 + 冲突块 + VLM 任务）
└─ 少量风险页单独复核                       → 页面/区域渲染 + local-vlm-ocr-fallback
```

## 2. 环境与已安装工具

- 共享模型运行库：`skills/_shared/model-tools/`；默认通过 `model_runtime.py resolve --id ...` 或 `scripts/run_bemarkdown_with_shared_runtime.bat` 获取解释器和模型，允许显式覆盖 `LLMWIKI_MODEL_RUNTIME_ROOT`。
- 文档环境：共享 `document-stt-py310` 提供 `marker-pdf` 与 `surya-ocr`；公式环境默认提供 `PP-FormulaNet_plus-L`、TexTeller。CnOCR/CnSTD 保留但不在默认候选链中；模型 ID 与版本以 `registry.yaml` 为准。
- MTEF 运行库：Temurin JRE、JRuby Complete、许可安全的 Transpect parser 子集、olefile 与 Apache Batik 全部通过 `model_runtime.py exec/resolve` 调用；禁止全局 Java、JRuby 或 XProc/XSLT 胶水。
- WMF 渲染：MathType MFCOMMENT 文件优先经 Batik WMF→SVG→PNG；其他 placeable WMF 可用预编译 GDI 记录枚举适配器，失败再走 Batik。LibreOffice 仅作诊断回退，A4 画布或比例不符不得通过。
- 本机 GPU 结果与 CPU 降级必须读取 `skills/_shared/model-tools/benchmark-summary.json`；不得把历史速度或上游宣传指标当成本轮实测。

marker CUDA 必查：pip 可能装 CPU torch，需替换为 CUDA 版并验证；`convert_pdf_with_marker.py` 会自动拒绝 CPU marker：

```bash
python skills/import/bemarkdown/scripts/convert_pdf_with_marker.py --check-only --json
```

## 3. DOCX 完整流程

### Step 1 — 快速诊断 DOCX

检查 `word/document.xml` 和 `word/media/`：统计 OMML、EQ、OLE/WMF、PNG。结论写入转换日志。

关键判断：
- `<m:oMath>`：Word 原生公式 OMML。
- `<w:fldChar>` + `<w:instrText>`：旧版 EQ 域代码。
- `<w:object>` 或 `.wmf`：Equation Editor/MathType OLE 公式。
- PNG + alt LaTeX：图片公式。

完成标准：明确本 DOCX 属于哪种格式组合。

### Step 2 — 运行主转换

推荐批量：

```bash
python scripts/batch_convert_all.py <input_dir> <output_dir> --mtef-mode shadow --audit-only --task-id <ascii-task-id> --json
```

脚本递归查找 DOCX，自动检测 WMF，并按批次初始化双引擎 ensemble，对每个文件执行：

```text
DOCX relationship/OLE/MTEF/WMF 哈希链 → JRuby 分片解析 → MathML/KB-LaTeX → WMF 全幅渲染 → 三源共识/视觉复核 → 原子完成门禁
```

单文件可调用：

```python
from scripts.omml_docx_to_md import convert_one_docx
convert_one_docx(docx_path, out_dir)  # 公式 ensemble 按共享模型 ID 在隔离 runtime 中初始化
```

默认使用 `--resume`；仅在显式要求重算时用 `--rebuild`。全库 shadow 验收后才把生产调用改为 `--mtef-mode strict`。状态固定写入 `tmp/state/bemarkdown/<task-id>/formula_audit.sqlite3`，SQLite WAL、分片 JSONL 和组件/合同 hash 共同保证续跑。视觉模型只对当前 runtime fingerprint 下字节完全相同的 `png_sha256` 去重推理，并把同一证据重新绑定到各 WMF 位置；同 hash 若出现冲突模型结果必须阻断。PP-FormulaNet 与 TexTeller 在同一 GPU 上顺序运行，各阶段只保留一个长期进程，`--model-timeout` 是两者合计预算；超时会清理完整进程树并在下次只续跑未完成 hash。`shadow` 或任一未解决项只能生成 `_draft.md`；只有全部公式为 `machine_final`/`reviewed_final` 才写 `completion.json` 和 `_final.md`。

复核回灌：对 MTEF 审计使用 `apply_formula_reviews.py --audit-db ... --reviews ... --require-all`。MTEF 结果必须同时绑定 MTEF hash、全部预览/PNG hash、runtime fingerprint、`final_latex`、视觉证据和置信度；独立 WMF 必须绑定 WMF/PNG hash 并指定 `final_kind=latex|text|image`。旧版单文档复核接口继续兼容，但复核状态统一为 `reviewed_final`。

### Step 3 — 四种公式格式处理规则

| 格式 | XML/文件特征 | 处理 | 关键坑 |
|---|---|---|---|
| OMML | `<m:oMath>` | 按 ECMA-376/ISO 29500 解析 fraction/scripts/radical/delimiter/bar/nary 等 | mammoth 会丢弃；未知节点必须阻断，不能递归拼接文本 |
| EQ 域代码 | `fldChar + instrText eq` | 按 Microsoft EQ 域语义递归解析 `\f`、`\r`、`\s`、`\b`、`\a`、`\o`、`\x` | `\x\to` 是上边框、`\o` 是叠印；不得默认猜成矢量或核素上下标 |
| MTEF/OLE + WMF | `<w:object>`、`Equation Native`、`.wmf` | MTEF XML AST→MathML→KB-LaTeX，同时 WMF→PP-FormulaNet/TexTeller；三源一致或复核 | 未消费字节、未知模板/字形、A4 画布、PIL、单/双模型直写均禁止 |
| 独立 WMF | `.wmf` 无 OLE | 全幅渲染→双 OCR 候选→`latex/text/image` 强制复核 | 不得仅凭双 OCR 自动定稿 |
| PNG alt | DrawingML 图片 + alt | mammoth 提取后 alt→LaTeX | alt 可能是文件路径/ID，需过滤 |

EQ 常见标准映射：`\f(num,den)`→`\frac{num}{den}`，`\r(x)`→`\sqrt{x}`，`\r(n,x)`→`\sqrt[n]{x}`，`\x\to(v)`→`\overline{v}`。历史 `\x\to→\vec`、`\o\al→核素上下标` 只能经源 DOCX 与指令 hash 绑定的视觉复核覆盖启用；详见 `references/omml-eq-conversion-contract.md`。

中文下标乱码：OCR 模型常把 `向/出/入/总/副/原/上/下` 识别成 `\odot/\text{iii}/\text{ii}/\perp/\mp` 等。调用 `fix_chinese_subscripts.py` 时必须传入候选引擎；marker/surya 专属模式不得套用到 PP-FormulaNet 或 TexTeller。任何会改变物理语义的修订都要对照公式图，不能只凭正则回填。

### Step 4 — PNG alt / mammoth 兜底

仅当 DOCX 没有 OMML/EQ/WMF，且图片 alt 明显含 LaTeX 时使用。`replace_images_with_latex.py` 规则：
- alt 是真实 LaTeX → `$...$` 或 `$$...$$`
- 小空白图 → 删除
- 无 alt 或 alt 是 Windows 路径、FounderCES ID、纯标签编号 → 保留为图片
- 无 alt 的物理示意图不要凭透明底/大小误判为公式，应 VLM 抽查。

完成标准：无 base64 data URI；公式转文本，示意图保留。

## 4. PDF 完整流程

### 4.0 PDF 诊断入口

任何 PDF 转换前先运行诊断，尤其是试卷、答案、扫描件、图片型题干和公式密集材料：

```bash
python skills/import/bemarkdown/scripts/diagnose_pdf.py <file.pdf> --json
```

- `text_pdf` 且公式可读：用 pymupdf4llm。
- `image_pdf`：整页图片/扫描件，默认用组合 OCR。
- `mixed_pdf` 或 `summary.formula_risk=true`：文本层可能存在但公式/题图风险高，默认用组合 OCR。

详细规则见 `references/image-pdf-ocr-workflow.md`。

### 4.1 pymupdf4llm（文本完整时）

适合文本型 PDF 且公式可读。输出后去除误加粗体、清理 `\xa0`。

```python
import pymupdf4llm, re
md = pymupdf4llm.to_markdown(pdf_path)
md = re.sub(r'\*\*(.+?)\*\*', r'\1', md).replace('\xa0',' ')
```

### 4.2 组合 OCR（图片型 PDF 默认推荐）

图片型试卷、扫描件、文本层不可信的答案 PDF，默认用组合模式：

```bash
python skills/import/bemarkdown/scripts/combine_image_pdf_ocr.py <file.pdf> <output_dir> --json
```

烟测或长文档试跑时可限制页码和本地模型页数：

```bash
python skills/import/bemarkdown/scripts/combine_image_pdf_ocr.py <file.pdf> <output_dir> --page-range 0-1 --run-local-transcriber --local-page-limit 1 --json
```

组合模式输出 `candidate_manifest.json`、`merged_draft.md`、`conflict_blocks.jsonl`、`vlm_review_tasks.jsonl`、`final_quality_report.json`。`candidate_manifest.json` 必须包含 `model_candidate_matrix` 和 `engine_status`，用于判断每页哪些模型成功、哪些候选低置信、哪些仍需 VLM。只有 `final_quality_report.json.ok_for_import=true`、`issue_count=0`、`unresolved_conflict_count=0` 且 `model_candidate_matrix_present=true`，才能进入题库配对或 raw 入库。

多 wave 试卷/答案必须在 VLM 复核回灌后重新汇总最终状态，不得沿用初跑 summary：

```bash
python skills/import/bemarkdown/scripts/summarize_combined_ocr_waves.py <combined_wave_root> --out ../ocr_combined_wave_summary_after_vlm.json --json
```

汇总脚本只读取每个 wave 当前的 `final_quality_report.json`。主控验收以这个最终汇总为准；若任一 wave `ok_for_import=false`、`issue_count>0` 或 `unresolved_conflict_count>0`，不得进入题库配对/入库。单页答案末页等短页若触发 `too_few_nonblank_lines`，必须先由 VLM 复核并回灌最终报告，再重建汇总。

详细规则见 `references/combined-image-pdf-ocr.md`。

### 4.3 marker-pdf（候选来源/快速检查）

marker 适合作为 OCR 候选、版面候选和快速检查。不要直接手写 marker 参数，优先用 wrapper 适配当前 marker CLI：

```bash
python skills/import/bemarkdown/scripts/convert_pdf_with_marker.py <file.pdf> <output_dir> --json
```

wrapper 会检查 `marker.exe`、Python310 marker import、CUDA、当前 force-OCR 选项（如 `--PdfProvider_force_ocr`），并把源 PDF 复制成 ASCII staging 名称再转换。CPU 模式极慢且不允许用于生产导入。图片型考试卷不得只凭 marker 输出入库。

部署预检不需要准备 PDF 或输出目录：`python skills/import/bemarkdown/scripts/convert_pdf_with_marker.py --check-only --json`。若同时传入 PDF，自检结果还会包含诊断，但仍不会执行转换。

### 4.4 页面渲染 + 本地 OCR/VLM（局部复核）

当 PDF 文本提取碎片化严重、marker 质量不足或少量页面需要精确校对时，先渲染风险页/区域，再进入本地 OCR/VLM 兜底。该分支详细规则见 `references/local-vlm-ocr-fallback.md`；BeMarkdown 不复制 PaddleOCR-VL 等模型，只调用 `chinese-handwriting-formula-transcriber` 的 wrapper 和任务卡。

```python
import pymupdf
doc = pymupdf.open(pdf_path)
for i,page in enumerate(doc):
    pix = page.get_pixmap(matrix=pymupdf.Matrix(3,3))
    pix.save(f'page_{i+1}.png')
```

优先让本地 PaddleOCR-VL/PP-OCRv6/PP-FormulaNet/TexTeller 协同输出结构化候选；BeMarkdown 读取 JSON 输出作为正式接口，`.md/.txt` 只作兼容兜底。`machine_final` 可作为强证据，`machine_candidate` 必须人工/视觉复核，`machine_abstain` 必须保持 unresolved，不能入库。只有本地模型仍无法解决的少量页，才用通用 VLM 要求完整逐行转录文字和公式，公式用 LaTeX，示意图位置保留图片引用。

完成标准：公式不碎片化；表格/图片保留；转录结果与原页抽查一致。

## 5. 后处理与质检

### 必做后处理

- 去 mammoth 转义：`\.`→`.`，`\(`/`\)`→括号。
- 去 pymupdf4llm 误粗体：`**text**`→`text`。
- 纯文本上标只修无歧义项：`×103→×10^3`、`kg/m3→kg/m³`、`ω2→ω²`；`r2/v2/m1/T0` 等有歧义不盲修。
- 检查中文下标乱码，按来源引擎运行 `fix_chinese_subscripts.py` 或 `scan_for_garbled(..., engine=...)`。

### 转换质量门禁

- [ ] `formula_conversion_report.json.status=converted`，OMML/EQ 的 `needs_review=0`、`unsupported=0`。
- [ ] `formula_audit_summary.json` 中 MTEF/预览关系完整、`unresolved_mtef=0`、`unresolved_independent_wmf=0`。
- [ ] `completion.json` 的 runtime/contract/summary hash 与当前状态一致；SQLite `integrity_check=ok`。
- [ ] 无 `data:image/...base64`。
- [ ] `formula_review_tasks.jsonl` 无 unresolved；最终稿无 `.wmf` 图片引用残留。
- [ ] LaTeX `$` 闭合。
- [ ] 图片引用 0 断链。
- [ ] 公式不碎片化；无 `picture intentionally omitted` 残留。
- [ ] 中文下标无明显乱码。
- [ ] 无 alt 路径/ID 被误转为公式。
- [ ] 无示意图被误删/误转。
- [ ] 输出 MD、media、summary 文件存在。

## 6. 常见事故与硬门禁

1. **不要用 mammoth 处理 OMML**：会丢公式。
2. **不要用 PIL 转 WMF**：低分辨率、字体缺失，公式不可读。
3. **公式后处理不要用危险 regex 替换反斜杠**：`re.sub(..., r'\frac', ...)` 易触发转义问题；用 `.replace()` 或 `lambda`。
4. **不要在 Skill 内固定用户缓存或独立 ONNX runtime**：版本、provider 和 CPU/GPU 状态由共享 requirements、doctor 与 benchmark 统一记录。
5. **marker 输入是目录**：单文件路径会报错。
6. **marker CPU 慢 50 倍**：必须验证 CUDA。
7. **文本型 PDF 也可能公式碎片化**：先抽查文本质量，碎片化就用 marker 或 VLM。
8. **alt 文本不一定是 LaTeX**：Windows 路径、FounderCES、图片编号必须保留为图片。
9. **透明底不等于公式**：Word 裁切的实验装置图也可能透明。
10. **优先找 DOCX 原件**：有 DOCX 时不要从 PDF 视觉恢复公式，DOCX 结构信息更可靠。

## 7. 实测经验摘要

- 2026-07 全库 shadow 基线：180 份 DOCX、32,793 个 Equation OLE、16,763 个唯一 MTEF、19,646 个 Equation WMF 预览，另有 27 个独立 WMF；MTEF v3=29、v5=32,764。
- 重复公式按 MTEF hash 继承结论；同一 MTEF 可对应多个预览，所有预览证据必须一致或经过同一 hash 绑定复核。
- 任何历史速度或置信度都不是当前验收依据；以本次 `formula_audit_summary.json`、runtime fingerprint 和模型原始证据为准。

## 8. Verification Checklist

- [ ] 已诊断 DOCX/PDF 类型并选择正确路径。
- [ ] DOCX：OMML/EQ/WMF/PNG alt 处理策略正确。
- [ ] PDF：pymupdf4llm/marker/VLM 选择正确，marker GPU 可用。
- [ ] Equation OLE 走 MTEF→MathML→KB-LaTeX，并与全幅 WMF 的 PP-FormulaNet/TexTeller 三源复核；独立 WMF 未自动定稿。
- [ ] 源 DOCX 用 `officecli_doc_qa.py qa --policy source-audit` 记录 relationship/缺图/截图；最终验证 DOCX 用 `--policy final`，validate、issues、stats、整页截图任一失败都阻断。
- [ ] 输出 MD 和 media 存在，图片引用 0 断链。
- [ ] 组合 OCR 的 `candidate_manifest.json` 含 `model_candidate_matrix` 和 `engine_status`；`final_quality_report.json.model_candidate_matrix_present=true`。
- [ ] 组合 OCR 多 wave 已用 `summarize_combined_ocr_waves.py` 从最终 `final_quality_report.json` 重建汇总，所有 wave `ok_for_import=true`、`issue_count=0`、`unresolved_conflict_count=0`。
- [ ] 无 data URI、无 WMF 残留、无中文下标乱码、无公式碎片化。
- [ ] LaTeX 闭合，表格/图片/标题基本结构可用。
- [ ] conversion_summary 或转换日志记录文件数、公式数、图片数、耗时。

## 9. Scripts / References

Scripts:
- `scripts/batch_convert_all.py` — DOCX 批量完整流程。
- `scripts/mtef_formula_audit.py`、`mtef_worker.rb`、`mtef_mathml.py` — 四层 hash、JRuby 分片、SQLite 续跑、MTEF AST/MathML/KB-LaTeX 与原子完成门禁。
- `scripts/omml_docx_to_md.py`、`omml_to_latex.py`、`eq_field_to_latex.py` — 标准优先的 DOCX 结构化公式解析、报告与阻断门禁。
- `scripts/audit_math_conversion.py` — 只读审计 DOCX 的 OMML/EQ 覆盖、风险与历史兼容候选。
- `../../_shared/scripts/libreoffice_runner.py`、`../../_shared/scripts/render_wmf_native.ps1`、`scripts/wmf_to_png.py` — MFCOMMENT 检测、Batik/GDI 全幅批量渲染、LibreOffice 诊断回退、源哈希和几何门禁。
- `scripts/formula_ensemble.py`、`batch_ocr_wmf.py`、`formula_candidate_utils.py`、`fix_chinese_subscripts.py` — PP-FormulaNet/TexTeller 实际调用、共识、VLM 任务与按引擎中文下标检查。
- `scripts/replace_images_with_latex.py` — PNG alt→LaTeX。
- `scripts/fix_plain_superscripts.py` — 无歧义纯文本上标修复。
- `scripts/diagnose_pdf.py` — PDF 文本层/图片层/整页图片比例诊断。
- `scripts/combine_image_pdf_ocr.py` — 图片型/扫描/公式风险 PDF 的组合转写编排，读取 marker 与 transcriber 结构化候选，生成候选矩阵、冲突块、VLM 任务和质量报告。
- `scripts/summarize_combined_ocr_waves.py` — 从各 wave 的最终 `final_quality_report.json` 重建组合 OCR 汇总，防止初跑旧状态残留。
- `scripts/convert_pdf_with_marker.py` — marker OCR 运行器，检查 Python310、CUDA 和 force-OCR 参数。
- `scripts/pdf_text_to_md.py` — PDF 页面渲染/VLM 辅助。

References:
- `references/omml-eq-conversion-contract.md` — ECMA/ISO OMML、Microsoft EQ 依据、可移植 LaTeX 边界和 hash 绑定兼容契约。
- `references/mtef-mathtype-conversion-contract.md` — MTEF/OLE/WMF 关系、状态机、三源门禁、SQLite 续跑、许可和复核 schema；处理旧 Equation/MathType 时必须读取。
- `references/docx-formula-formats.md` — DOCX 四种公式格式细节。
- `references/image-classification.md` — 图片/公式/示意图区分。
- `references/marker-pdf.md` — marker 安装、CUDA、LLM 增强和问题处理。
- `references/image-pdf-ocr-workflow.md` — 图片型/扫描/混合 PDF 的诊断、marker 和兜底入口流程。
- `references/combined-image-pdf-ocr.md` — 图片型 PDF 多引擎候选、块级融合、VLM 冲突复核和入库门禁。
- `references/local-vlm-ocr-fallback.md` — marker 质量不足时调用 PaddleOCR-VL/PP-OCRv6/PP-FormulaNet/TexTeller 本地 OCR/VLM 协同兜底。
