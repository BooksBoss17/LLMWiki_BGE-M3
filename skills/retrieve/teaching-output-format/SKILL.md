---
name: teaching-output-format
description: "Use when formatting teaching outputs, exam sheets, review worksheets, or Word/PDF documents according to the user's physics handout/exam style, including final DOCX/PDF delivery QA expectations."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [windows]
metadata:
  hermes:
    tags: [retrieve, llmwiki, physics, education]
    related_skills: ["md-to-docx"]
---

# 用户标准试卷格式规范

> 基于 3 份实际卷子分析（2026-06-29）
> 分析样本：期中复习练习2.docx / 高二物理第5周周练.docx / 高二物理半期复习练习4(1).docx

## 字体规范

| 用途 | 中文字体 | 西文字体 | 字号 |
|------|---------|---------|------|
| 大标题 | 宋体 | Times New Roman | 14pt（有时10.5pt） |
| 编制人信息 | 宋体 | Times New Roman | 10.5pt |
| 章节标题（一、二、） | 宋体 | Times New Roman | 10.5pt **加粗** |
| 题干（（  ）1．...） | 宋体 | Times New Roman | 10.5pt **加粗** |
| 选项（A．B．C．D．） | 宋体 | Times New Roman | 10.5pt **加粗** |
| 正文/解析 | 宋体 | Times New Roman | 10.5pt |
| 答案表格 | 宋体 | Times New Roman | 10.5pt |

**关键规则：**
- 所有 run 的 `w:ascii` = `Times New Roman`，`w:eastAsia` = `宋体`
- 题干和选项**全部加粗**
- 标题黑色加粗，不用蓝色/彩色

## 段落格式

| 参数 | 值 |
|------|------|
| 行距 | 1.0 倍（`line=240, lineRule=auto`） |
| 段前间距 | 0 |
| 段后间距 | 0（有时标题段后有小间距） |
| 对齐 | 标题居中，正文左对齐 |

## 页面格式

| 参数 | 值 |
|------|------|
| 纸张 | A4 (210×297mm) |
| 边距 | 上下左右各 2.0cm |
| 栏数 | 单栏（`w:num=1`），或部分卷子双栏 |
| 答案表 | 2行×N列，无表头标题，直接填题号和答案 |

## 选项排版

选项使用 Tab 分隔，AB 在一行，CD 在一行：
```
A．选项内容	B．选项内容
C．选项内容	D．选项内容
```

## 讲义与试卷的区别

讲义格式与试卷基本字体/字号一致，但结构不是普通练习卷：
- 使用**题型化结构**：`【题型一】知识点凝练 → 典例 → 变式1-1 → 变式1-2`，最后再放课堂练习/习题集。
- 每个题型前的“知识点凝练”必须是干货：公式、适用条件、解题流程、易错点；不要只在开头放泛泛知识框架。
- **不要每道题套表格框**，也不要在每题左侧单独加“知识点栏”；这会浪费空间且不像学校标准讲义。
- 题干、选项直接按参考讲义/试卷排版：例题标题加粗，选项可按行或 Tab 排版。
- 热学讲义必须包含图像分析题，图片题按原题插入图片，图片尺寸要压缩到适合页面。
- 可以省略“编制人/班级/座号/姓名”行；期末复习省略“考情分析”。

### 热学讲义导出版式细则（2026-07-02 更新）

- 生成前优先读取 `output/1.热学讲义试导出/人工调整格式/` 中人工微调过的 docx，提取标题、题型标题、正文、点拨、答案区的实际字号和边距。
- 当前人工微调版参考：A4，四边距 2cm；主标题 14pt 居中；副标题 10.5pt；题型标题 12pt 加粗；正文 10.5pt；教师点拨 9pt 斜体。
- 学生版默认不放参考答案；教师版保留方法点拨和答案。
- 教师版答案不要三列强挤长公式；推荐无边框两列，长公式改短文本或单独占行。
- 如果学生版某实验题需要书写空间，可主动分页让题目/图像/小问整体在一页；但不能让上一题选项 D 或方法点拨单独成为页首孤行。
- 选项较长时可用 AB/CD 两行 Tab 排版，避免单个选项跨页。

## 作业卷/试卷答题区规则

- 如果用户要求“作业卷子/练习卷/测试卷”，默认学生版应能直接在卷面作答，不只是题目清单。
- 选择题、填空题可只留括号/横线；实验题若是填空式，题内横线即可。
- **计算题必须留书写空间**：每道含 2-3 个小问的计算题至少留 10-12 行横线或等量空白；题干明确“写出必要文字说明、公式和演算步骤”时，不能只留 4-5 行。
- 教师版可以不留答题线，但参考答案应逐题列出；长答案不要横向挤在同一行，避免第9/10题答案被截断。
- 若为了学生作答空间导致学生版页数增加，应优先保证可书写性；教师版可压缩点拨/解析字号以控制页数。

## DOCX/PDF 交付 QA 规则

所有讲义、试卷、练习、知识清单、学生报告等 Word/PDF 交付物都走统一链路：

`生成 Markdown 和图片资产到 output/` -> `md-to-docx 生成 DOCX` -> `Word COM 导出 PDF` -> `PyMuPDF 检查页数/空白页` -> `OfficeCLI QA 检查 DOCX 和截图`

- 生成类 skill 只负责内容和资产；DOCX/PDF 导出统一交给 `retrieve/md-to-docx/SKILL.md`。
- 普通草稿使用 `--officecli-qa auto`；OfficeCLI 可用则生成 `output/qa_<name>/`，不可用不阻断。
- 正式交付使用 `--officecli-qa required`；OfficeCLI 缺失、OpenXML 校验失败或截图生成失败都不能交付最终版。
- 如果生成了 `contact_sheet.png`，交付前必须视觉检查，确认无空白页、缺图、严重重叠、明显截断或版面溢出。
- 若 `issues.json` 提示占位符、图片缺失、异常空段落、明显溢出风险，先修复源 Markdown/图片/导出脚本后重跑 QA。
- OfficeCLI 只做导出后质量检查，不替代物理内容审校、题目答案校对或 `md-to-docx` 生成链路。

## python-docx 实现要点

```python
# 正确设置中英文字体
rpr = run._element.get_or_add_rPr()
rfonts = rpr.get_or_add_rFonts()
rfonts.set(qn('w:ascii'), 'Times New Roman')
rfonts.set(qn('w:hAnsi'), 'Times New Roman')
rfonts.set(qn('w:eastAsia'), '宋体')
```

## 页数估算实测

| 排版 | 每页字符数 | 实测页数（~8000字讲义） |
|------|-----------|----------------------|
| 单栏 A4, 10.5pt | ~2200 | 6页 |
| 单栏 A4, 9pt | ~3000 | 5页 |
| 单栏 A4, 7.5pt | ~4000 | 4页 |
| 单栏 A4, 7pt | ~4500 | 4页 |
| 双栏 A4, 7pt | ~3500×2 | 2页 ✅ |
| 双栏 A4, 7.5pt | ~3200×2 | 3页 |
