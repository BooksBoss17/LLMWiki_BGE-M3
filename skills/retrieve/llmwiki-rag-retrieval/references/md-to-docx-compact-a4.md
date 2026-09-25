# MD → DOCX 紧凑 A4 排版技术参考

> 本文档记录将知识库生成的 Markdown 讲义转换为 A4 双栏两页 Word 文档的完整技术方案。
> v2: 2026-06-29 更新 — OMML 公式、双栏空白页修复、Word COM 分栏方案

## 适用场景

- 知识库检索生成的讲义/复习资料需要输出为 Word 格式
- 内容量大（~8000字 + 13表格）需要压缩到 A4 两页
- 用户要求"内容集中在一张A4纸上（两页）实在不行了就A3"

## 工具链

| 工具 | 用途 | 安装状态 |
|------|------|---------|
| `python-docx` | 生成 .docx 文件 | ✅ 已安装 (1.2.0) |
| `win32com.client` | Word COM 接口，验证页数 + 设置分栏 + 转 PDF | ✅ Windows 自带 |
| `fitz` (PyMuPDF) | 读取 PDF 页数和内容 | ✅ 已安装 |
| `lxml` | OMML XML 树构建 | ✅ python-docx 依赖 |

> **注意：** pandoc 未安装，不可用。latex2mathml 未安装。全部用 python-docx 手动构建。

## 核心技术点

### 1. LaTeX → OMML 转换（用户要求，非 Unicode）

> **⚠️ 用户明确要求：** "转成word之后公式使用OMML格式看上去才更美观"
> 不要用 Unicode 近似（₀₁₂³ 等），要用 Word 原生数学格式（OMML）。

python-docx 不原生支持 LaTeX 渲染，但可以通过 `lxml.etree` 直接构建 OMML XML 元素插入段落。

**OMML 命名空间：**
```python
M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
def m_ns(tag): return f'{{{M}}}{tag}'
```

**核心 OMML 元素：**

| LaTeX | OMML 元素 | 结构 |
|-------|-----------|------|
| `\frac{a}{b}` | `m:f` | `m:f > m:num + m:den` |
| `x^2` | `m:sSup` | `m:sSup > m:e + m:sup` |
| `x_1` | `m:sSub` | `m:sSub > m:e + m:sub` |
| `x_1^2` | `m:sSubSup` | `m:sSubSup > m:e + m:sub + m:sup` |
| `\sqrt{x}` | `m:rad` | `m:rad > m:e` |
| 普通字符 | `m:r > m:t` | run 内放文本 |
| `\text{x}` | `m:r > m:t` | 直接放文本 |

**转换器类设计（LatexToOMML）：**
1. `_tokenize(latex)` — 词法分析：cmd/char/op 三类 token
2. `_parse(parent)` — 递归下降解析，将 OMML 元素 append 到 parent
3. `_handle_cmd(parent)` — 处理 `\frac`/`\sqrt`/`\text`/希腊字母等
4. `_wrap_subsup(parent, sub, sup)` — 将前一个子元素包装成 sSub/sSup/sSubSup
5. `convert_inline(latex)` → 返回 `m:oMath` 元素，直接 `p._p.append(omath)` 插入段落
6. `convert_display(latex)` → 返回 `m:oMathPara` 元素（居中显示公式）

**插入段落的方法：**
```python
# 行内公式
omath = converter.convert_inline(latex_str)
p._p.append(omath)  # 直接追加到段落 XML

# 独立公式
omathpara = converter.convert_display(latex_str)
p._p.append(omathpara)
```

**希腊字母替换表（在 _handle_cmd 中处理）：**
```python
SYMBOLS = {
    r'\Delta':'Δ', r'\rho':'ρ', r'\times':'×', r'\cdot':'·',
    r'\leq':'≤', r'\geq':'≥', r'\neq':'≠', r'\approx':'≈',
    r'\pi':'π', r'\theta':'θ', r'\sigma':'σ', r'\lambda':'λ',
    r'\mu':'μ', r'\Rightarrow':'⇒', r'\rightarrow':'→',
    # ... 完整列表见脚本
}
```

> **陷阱：** `\left` 和 `\right` 需要跳过（不生成元素），其后的括号字符作为普通字符处理。

### 2. 双栏排版 — ⚠️ 两种方案

#### 方案 A: python-docx XML（有空白页陷阱）

```python
from docx.oxml.ns import nsdecls
from docx.oxml import parse_xml

sectPr = section._sectPr
cols = parse_xml(f'<w:cols {nsdecls("w")} w:num="2" w:space="160" w:equalWidth="1"/>')
sectPr.append(cols)
```

> **⚠️ 陷阱：python-docx 双栏导致尾随空白页**
> python-docx 设置 `w:cols` 后，body 末尾的 sectPr 会触发 Word 渲染一个额外的空白页。
> 尝试过的修复（均无效）：
> - 删除尾部空段落
> - 将 body sectPr 移入最后一段的 pPr + type=continuous
> - 设置空段落 spacing line=20 exact
> - 添加新的单栏 body sectPr
> **不要再用这个方案。用方案 B。**

#### 方案 B: Word COM TextColumns（推荐，无空白页）

```python
import win32com.client

word = win32com.client.Dispatch('Word.Application')
word.Visible = False
word.DisplayAlerts = False
doc = word.Documents.Open(docx_path)

# 在 Word 中设置双栏 — 不会产生空白页
doc.Sections(1).PageSetup.TextColumns.SetCount(2)
doc.Sections(1).PageSetup.TextColumns.Spacing = 6  # points

doc.Repaginate()
pages = doc.ComputeStatistics(2)  # wdStatisticPages
print(f'Page count: {pages}')
doc.Save()
```

**关键：** python-docx 生成单栏 DOCX，然后用 Word COM 设置双栏。Word 自己管理分节符，不会产生尾随空白页。

### 3. 紧凑排版参数（A4 两页适配）

| 参数 | 值 | 说明 |
|------|------|------|
| 纸张 | A4 (210×297mm) | 肖像方向 |
| 上下边距 | 0.8cm | python-docx 设置 |
| 左右边距 | 1.0cm | python-docx 设置 |
| 栏数 | 2 | Word COM 设置，间距 6pt |
| 正文字号 | 7pt | 微软雅黑 |
| 行间距 | 9pt | 固定值 |
| 段前/段后 | 0pt / 0.5pt | 几乎无间距 |
| H1 标题 | 11pt 加粗 | 居中，深蓝色 |
| H2 标题 | 8.5pt 加粗 | 蓝色+下边框 |
| H3 标题 | 7.5pt 加粗 | 深灰 |
| 表格字号 | 6.5pt | 表头蓝底加粗，单元格间距最小 |
| 公式 | OMML | 居中，Cambria Math |

### 4. Markdown 解析 + 内容过滤

解析器按行处理。**根据讲义类型过滤内容：**

```python
# 期末复习（非高考复习）→ 去掉考情分析
SKIP_SECTIONS = {'考情分析', '讲义说明'}

# 跳过头部元信息
if line.startswith('> **') and any(k in line for k in ['适用教材','复习范围','课型','来源标注']):
    skip  # 不输出

# 考情分析整节跳过
if '## 一、考情分析' in line:
    skip_mode = True
if skip_mode and line.startswith('## '):
    skip_mode = False  # 下一节开始
```

**ASCII 树状图 → 表格转换：**
> **⚠️ 用户反馈：** "你把知识框架画得很乱"
> ASCII 树状图（`├──` `│` `└──`）在 Word 中因字体不等宽会错位。
> **必须替换为表格**：3列（章/节/核心知识点），合并章节单元格。

```python
def add_knowledge_framework_table():
    data = [
        ['章', '节', '核心知识点'],
        ['第1章\n分子动理论...', '分子动理论基本观点', '分子组成、热运动、分子力'],
        # ...
    ]
    # 创建表格后合并第1列的章节单元格
    table.cell(start, 0).merge(table.cell(end, 0))
```

### 5. 页数验证 + PDF 导出

```python
# Word COM 验证页数
doc.Repaginate()
pages = doc.ComputeStatistics(2)

# 导出 PDF — ⚠️ 注意文件锁问题
# ExportAsFixedFormat 比 SaveAs(FileFormat=17) 更可靠
doc.ExportAsFixedFormat(pdf_path, ExportFormat=17, OpenAfterExport=False)
```

> **⚠️ 陷阱：PDF 文件锁**
> 如果旧 PDF 被阅读器打开，`ExportAsFixedFormat` 和 `SaveAs` 都会失败（`com_error -2147352567`）。
> 解决方案：
> 1. 先 `taskkill //IM WINWORD.EXE //F` 关闭所有 Word 实例
> 2. 使用不同的文件名输出（如 `_v2.pdf`）
> 3. 或导出到临时目录后 `shutil.move`

### 6. 迭代压缩流程

1. 生成 DOCX（单栏）→ Word COM 设置双栏 → 验证页数
2. 超过2页 → 减小字号/行间距/边距
3. 只差1-2行 → 将页脚说明文字缩为5pt单行
4. 重新生成 → 验证 → 重复直到达标
5. **A3 fallback：** 如果 A4 双栏仍超2页，切换到 A3（297×420mm），但优先尝试 A4

## 排版效果参考

| 内容量 | 推荐参数 | 预期页数 |
|--------|---------|---------|
| ~3000字 + 5表格 | 7.5pt, 9.5pt行距, 单栏 | 1-2页 |
| ~5000字 + 8表格 | 7pt, 9pt行距, 双栏 | 2页 |
| ~8000字 + 13表格 | 7pt, 9pt行距, 双栏, 0.8cm边距 | 2页 |
| >10000字 | 考虑A3或拆分为多份 | 2-4页 |

## 完整脚本

转换脚本保存在知识库 `skills/retrieve/md-to-docx/scripts/md2docx.py`，包含：
- LatexToOMML 转换器类（~150行）
- Markdown 解析器（~200行，含内容过滤）
- Word COM 后处理（分栏+验证+PDF导出）
- 可复用，修改参数适配不同内容量
