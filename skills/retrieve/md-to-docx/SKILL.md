---
name: md-to-docx
description: "Markdown 转 Word（.docx）：LaTeX 公式转 OMML 原生格式、双栏 A4 紧凑排版、Word COM 后处理消除空白页、自动验证页数并导出 PDF；可用受控 OfficeCLI 做 OpenXML 校验、视觉校验和输出质量检查。适用于讲义/试卷/笔记等教学文档的格式转换。"
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [docx, word, markdown, omml, latex, pdf, education, officecli, qa]
    related_skills: [powerpoint, ocr-and-documents]
---

# Markdown 转 Word 文档（含 OMML 公式 + 双栏排版）

将 Markdown 文件转换为排版精美的 Word 文档，支持 LaTeX 公式转 OMML、双栏紧凑排版、自动页数控制和 PDF 导出。

## 适用场景

- 讲义/复习资料/试卷的 MD→DOCX 转换
- 需要在 Word 中显示原生数学公式（非 Unicode 近似）
- 需要紧凑排版（A4 双栏 2 页）

## 前置条件

- Windows 主机（需 Word COM 接口）
- Python 3.10+，已安装 `python-docx`、`lxml`、`pywin32`、`PyMuPDF`
- 不需要 pandoc

## 工作流程

### 第 1 步：读取并分析 MD 源文件

```python
with open(md_path, "r", encoding="utf-8") as f:
    md = f.read()
```

确认内容结构：标题层级、表格数量、公式数量（`$...$` 行内 + `$$...$$` 行间）、代码块、列表。

### 第 2 步：编写 LaTeX→OMML 转换器

这是核心组件。**不要用 Unicode 近似**（如 `₁₂₃` 下标），要用 Word 原生 OMML 格式。

关键实现：
- **分词器**：将 LaTeX 拆分为 `cmd`/`op`/`char` 三类 token
- **解析器**：递归下降解析，处理 `\frac{}{}`、`\sqrt{}`、`_{}`、`^{}`、`\text{}` 等
- **OMML 元素映射**：
  - `\frac{a}{b}` → `<m:f><m:num>a</m:num><m:den>b</m:den></m:f>`
  - `x_{sub}` → `<m:sSub><m:e>x</m:e><m:sub>sub</m:sub></m:sSub>`
  - `x^{sup}` → `<m:sSup><m:e>x</m:e><m:sup>sup</m:sup></m:sSup>`
  - `\sqrt{x}` → `<m:rad><m:e>x</m:e></m:rad>`
- **符号映射**：`\Delta→Δ`, `\rho→ρ`, `\times→×`, `\leq→≤` 等（用字面 replace）
- **行内公式**：`$...$` → `<m:oMath>` 插入段落
- **行间公式**：`$$...$$` → `<m:oMathPara>` 居中插入

完整转换器代码见 `references/latex_to_omml.py`。

### 第 3 步：构建 DOCX 文档

```python
from docx import Document
from docx.shared import Pt, Cm, Mm, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.section import WD_ORIENT
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml

doc = Document()
section = doc.sections[0]
section.page_width = Mm(210)   # A4
section.page_height = Mm(297)
section.top_margin = Cm(0.8)
section.bottom_margin = Cm(0.8)
section.left_margin = Cm(1.0)
section.right_margin = Cm(1.0)
# ⚠️ 不要在 python-docx 中设置双栏！会导致空白尾页。
# 双栏在 Step 5 用 Word COM 设置。
```

排版参数有两套：**标准试卷格式**和**讲义压缩格式**。

#### 标准试卷格式（参照用户卷子，见 `references/exam_format_spec.md`）

| 参数 | 值 |
|------|------|
| 中文字体 | **宋体**（SimSun） |
| 西文字体 | **Times New Roman** |
| 正文字号 | 10.5pt（五号） |
| 标题字号 | 14pt 加粗，居中 |
| 二级标题 | 10.5pt 加粗（如"一、选择题"） |
| 题干/选项 | **加粗** |
| 行距 | 1.0 倍（240 twips, auto） |
| 边距 | 2.0cm（四边） |
| 栏数 | 单栏 |
| 选项排版 | Tab 分隔，AB/CD 同行 |

#### 讲义压缩格式（内容多时使用）

| 参数 | 值 |
|------|------|
| 中文字体 | **宋体** |
| 西文字体 | **Times New Roman** |
| 正文字号 | 7pt（压缩，标准10.5pt太占空间） |
| 标题字号 | H1=11pt / H2=8.5pt / H3=7.5pt / H4=7pt |
| 标题颜色 | **黑色加粗**（不用蓝色，与试卷一致） |
| 行距 | 0.92 倍 |
| 边距 | 0.8cm |
| 栏数 | 双栏（Word COM 设置） |
| 表格字号 | 6.5pt，表头浅蓝底加粗 |
| 行间公式 | OMML，居中 |

⚠️ **绝对不要用微软雅黑**。用户卷子统一使用宋体+TNR，讲义也必须一致。

### 第 4 步：解析 MD 并填充内容

逐行解析 Markdown，处理：
- `#`/`##`/`###`/`####` 标题 → `add_heading()`
- `$$...$$` 行间公式 → `add_display_math()`（OMML）
- `$...$` 行内公式 → 在段落中插入 `<m:oMath>`
- `|...|` 表格 → `add_table()`（单元格内也处理 OMML）
- `- ` / `1.` 列表 → `add_list()`
- `> ` 引用 → `add_quote()`（左边框 + 浅蓝底色）
- ` ``` ` 代码块 → 等宽字体 + 灰底
- `**bold**` / `*italic*` / `` `code` `` → 对应 run 格式

**内容过滤**：可在解析阶段跳过不需要的章节（如"考情分析""来源标注"等）。

### 第 5 步：Word COM 后处理（双栏 + 页数控制）

```python
import win32com.client, time

word = win32com.client.Dispatch('Word.Application')
word.Visible = False
word.DisplayAlerts = False
doc = word.Documents.Open(docx_path)
time.sleep(1)

# 设置双栏（在 Word 中设置，不是 python-docx）
doc.Sections(1).PageSetup.TextColumns.SetCount(2)
doc.Sections(1).PageSetup.TextColumns.Spacing = 6  # pt

doc.Repaginate()
pages = doc.ComputeStatistics(2)  # wdStatisticPages=2
print(f"页数: {pages}")

# 如果超出目标页数，用 TypeBackspace 删除尾部空段落
if pages > target_pages:
    for _ in range(3):
        sel = word.Selection
        sel.EndKey(6)  # wdStory
        sel.TypeBackspace()
    doc.Repaginate()

doc.Save()
```

### 第 6 步：导出 PDF

```python
# ⚠️ 如果目标路径的 PDF 已被占用（阅读器锁定），导出会失败
# 解决方案：先导出到临时路径，再移动；或用不同的文件名

pdf_path = docx_path.replace('.docx', '.pdf')
try:
    doc.ExportAsFixedFormat(pdf_path, ExportFormat=17, OpenAfterExport=False)
except:
    # 回退：用 _v2 后缀
    pdf_path = docx_path.replace('.docx', '_v2.pdf')
    doc.ExportAsFixedFormat(pdf_path, ExportFormat=17, OpenAfterExport=False)

doc.Close(False)
word.Quit()
```

### 第 7 步：验证

```python
import fitz
pdf_doc = fitz.open(pdf_path)
print(f"PDF 页数: {len(pdf_doc)}")
for i, page in enumerate(pdf_doc):
    text = page.get_text()
    lines = [l for l in text.split('\n') if l.strip()]
    print(f"  Page {i+1}: {len(lines)} lines, {len(text)} chars")
    # 末页 0 lines = 空白页
pdf_doc.close()
```

### 第 8 步：OfficeCLI 输出 QA（可选增强）

OfficeCLI 只作为导出后的质量检查器，不替换本 skill 的 Markdown→DOCX/PDF 生成链路。必须通过项目受控脚本调用，不运行官方 `install.ps1`，不写全局 PATH，不安装 OfficeCLI 自带 agent skill/MCP。

```bash
python skills/_shared/scripts/officecli_doc_qa.py check --json
python skills/_shared/scripts/officecli_doc_qa.py install-local --version v1.0.135 --json
python skills/_shared/scripts/officecli_doc_qa.py qa --docx output/<name>.docx --pdf output/<name>.pdf --out-dir output/qa_<name> --json
```

导出脚本支持：

```bash
python skills/retrieve/md-to-docx/scripts/md2docx.py input.md output/name.docx --officecli-qa auto
python skills/retrieve/md-to-docx/scripts/md2docx.py input.md output/validation.docx \
  --layout validation --officecli-qa required
```

- `auto`：检测到 OfficeCLI 才生成 `output/qa_<name>/`，缺失不阻断。
- `off`：完全跳过 OfficeCLI QA。
- `required`：OfficeCLI 缺失、OpenXML 校验失败或截图失败都视为交付失败。
- `--layout compact` 保留双栏紧凑版；`--layout validation` 使用 A4 单栏、10.5pt、宋体 + Times New Roman、上下 1.6 cm/左右 2.0 cm 页边距，并强制标题与下一段同页，适合公式和题图终检且避免页底孤立标题。
- Markdown 图片会以内嵌原始字节写入 DOCX，按 150 DPI 计算 Word 显示尺寸并只在超出版心时等比缩小；不裁切、不重采样、不改变宽高比。引用图片缺失时转换失败关闭。
- `--no-pdf` 只用于单元测试或中间草稿；正式交付仍必须导出 PDF 并完成 Office CLI 与逐页视觉复核。
- 公式统一调用 `references/latex_to_omml.py`，避免脚本内重复解析器在嵌套分式/根式中重置状态。验证版 DOCX 导出 PDF 时不二次保存 Word 文件，防止 Word COM 删除 schema 必需的空根指数节点。

## 常见陷阱

### 1. ❌ python-docx 设置双栏导致空白尾页

**问题**：用 `parse_xml('<w:cols w:num="2"/>')` 在 python-docx 中设置双栏后，Word 渲染时会在末尾产生一个空白页（body sectPr 触发新页面）。

**解决**：不在 python-docx 中设置双栏。改用 Word COM 的 `PageSetup.TextColumns.SetCount(2)` 在打开后设置。

### 2. ❌ LaTeX 公式用 Unicode 近似导致排版混乱

**问题**：用 `₁₂₃` 等 Unicode 下标/上标替代 OMML，在 Word 中字体不统一、对齐错乱。

**解决**：自写 LaTeX→OMML 转换器，直接生成 `<m:oMath>` XML 元素插入段落。参考 `references/latex_to_omml.py`。

### 3. ❌ PDF 文件被锁定，导出失败

**问题**：`ExportAsFixedFormat` 报 `(-2147352567, '发生意外')` 错误，因为旧 PDF 被阅读器占用。

**解决**：
- 用 `taskkill //IM WINWORD.EXE //F` 清理残留进程
- 导出到不同文件名（如 `_v2.pdf`）
- 或导出到 `%TEMP%` 再 `shutil.move`

### 4. ❌ Word COM 的 SaveAs 报错

**问题**：`doc.SaveAs(pdf_path, FileFormat=17)` 在某些 Word 版本上报错。

**解决**：改用 `doc.ExportAsFixedFormat(path, ExportFormat=17, OpenAfterExport=False)`。

### 5. ❌ OMML 转换器正则问题

**问题**：`str.maketrans` 参数长度不匹配；`\left(` 中的括号被正则误解析。

**解决**：
- 用 dict 映射代替 `maketrans`（避免长度不等）
- 字面 replace 和 regex sub 分开处理
- `\left`/`\right` 直接跳过（不生成 OMML 元素）

### 6. ⚠️ 行间公式 `$$...$$` 跨行处理

MD 中的 `$$` 可能在单独一行（`$$\nformula\n$$`），需要收集多行直到遇到闭合 `$$`。

### 7. ⚠️ 表格单元格内的公式

表格单元格中也可能有 `$...$` 行内公式，需要同样走 OMML 转换流程。

### 8. ❌ 使用微软雅黑而非宋体

**问题**：默认用微软雅黑生成 DOCX，但用户卷子统一使用宋体+Times New Roman，格式不一致显得不专业。

**解决**：所有中文字体用**宋体**（SimSun），所有西文字体/数字用 **Times New Roman**。在 `set_run_font()` 中同时设置 `w:ascii`、`w:hAnsi` 和 `w:eastAsia` 三个属性。标题不加颜色（黑色加粗即可，不用蓝色）。

### 8b. ❌ 把“复习练习卷”做成压缩讲义

**问题**：用户给了标准练习卷作参考时，如果仍使用 A4 双栏、7pt、0.8cm 边距的压缩讲义样式，会与本校卷子风格不一致。

**解决**：当用户说“参照这两份练习/过去制作的卷子”时，优先使用标准试卷格式：A4 单栏、2cm 边距、宋体+Times New Roman、10.5pt 正文、14pt 居中标题、1.0 行距。若内容太多，先拆成多份练习，而不是继续压字号。若用户要求“多一个知识点栏”，每道题用 2 列表格：左侧窄栏写知识点，右侧放题干和选项。详见 `references/thermal-review-worksheet-format.md`。

### 9. ❌ 用 Unicode 下标/上标代替 OMML 被用户指出"公式不美观"

**问题**：用户明确要求公式使用 OMML 格式，Unicode 近似（如 `p₁V₁`）在 Word 中视觉效果差。

**解决**：所有 `$...$` 和 `$$...$$` 必须走 `LatexToOMML` 转换器，生成 `<m:oMath>` 元素。不要用 `str.maketrans` 做 Unicode 下标/上标替换。

## 页数估算参考

| 排版 | 每页字符数（中文） | 适用 |
|------|-------------------|------|
| 单栏 A4, 10.5pt | ~2200 | 标准试卷 |
| 单栏 A4, 9pt | ~3000 | 讲义（内容少） |
| 单栏 A4, 7.5pt | ~4000 | 讲义（内容中等） |
| 单栏 A4, 7pt | ~4500 | 讲义（内容多） |
| 双栏 A4, 7pt | ~3500×2栏=7000 | 紧凑讲义（2页） |
| 单栏 A3, 10.5pt | ~4400 | 大幅面试卷 |

> ⚠️ 以上数据为实测值（2026-06-29）。单栏7pt双栏 = 2页≈7000字符。如果内容超过7000字符需要进一步压缩字号或精简内容。

## 验证清单

- [ ] DOCX 能用 Word 正常打开
- [ ] 公式显示为 OMML 原生格式（双击可编辑）
- [ ] 表格内容完整、表头有底色
- [ ] 双栏排版正确（无空白尾页）
- [ ] 页数符合目标（A4 双栏 2 页 或 A3 1 页）
- [ ] PDF 导出成功且页数与 DOCX 一致
- [ ] 中文字体为**宋体**、西文字体为 **Times New Roman**、公式字体为 Cambria Math
- [ ] 如启用 OfficeCLI QA：`validate` 通过，`output/qa_<name>/contact_sheet.png` 已视觉检查，`issues.json` 无交付阻断问题

## 文件依赖

- `references/latex_to_omml.py` — 完整的 LaTeX→OMML 转换器（可直接 import）
- `references/exam_format_spec.md` — 用户标准试卷格式规范（字体/字号/边距/选项排版，基于3份实际卷子分析）
- `references/thermal-review-worksheet-format.md` — 热学/物理复习练习卷设计规范：按本校近期复习卷重难点拆分两份练习，并在标准试卷格式上增加“知识点栏”
- `templates/md2docx_template.py` — 完整转换脚本模板（宋体+TNR，修改路径即可用）
- `../../_shared/scripts/officecli_doc_qa.py` — 受控 OfficeCLI 检测、本地装载和导出后 QA 包装脚本
