---
name: teaching-ppt-analysis
description: 分析课堂教学 PowerPoint/PPTX 课件，输出有证据支撑的内容正确性、教学逻辑、视觉设计和投影可读性审查。用于物理课件分析、课件改进建议、课堂投影风险检查和基于知识库的教学内容核对。
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [windows, macos, linux]
metadata:
  hermes:
    tags: [powerpoint, teaching, review, classroom, presentation, physics]
    related_skills: [powerpoint, llmwiki-rag-retrieval]
---

# Teaching PPT Analysis — 教学 PPT 分析

当用户要求分析、审查、改进现有课堂 `.pptx` / PPT 课件时使用本 skill，尤其适用于高中物理课堂。

本 skill 补充通用 PowerPoint 能力，只保存本知识库的课堂分析经验和可复用脚本。

## 输出目标

输出面向教师行动的证据化审查，不做泛泛的设计点评。最终回答通常包含：

1. 总体判断。
2. 内容覆盖与教学逻辑。
3. 上课前必须修正的问题。
4. 分页或分模块改进建议。
5. 课堂使用风险：投影可读性、视频、公式、提前显示答案、小图和小字。
6. 如果生成了完整报告或附件，说明保存路径。

## LLMWiki_BGE-M3 存储规则

在 `<KB_ROOT>` 内工作时：

- `.hermes/desktop-attachments/` 只是 Hermes Desktop 的本地附件缓存，只能作为临时交接位置，不能当成知识库层、永久来源或最终输出位置。
- 区分 **基于知识库的分析任务** 和 **知识库导入任务**：
  - 基于知识库的分析任务：课件只是分析/对比/审查输入，任务本身不修改知识库。将工作副本放入 `tmp/renders/ppt-analysis/<课件标题>/input/`，处理后删除 `.hermes` 缓存和临时中间文件。
  - 知识库导入任务：用户明确要求“导入/入库/同步”材料。原始文件应按既有规则永久保存到 `source-library/`，然后继续走 raw → Wiki → RAG 同步。
- 临时提取结果、渲染 PDF/图片、联系表和草稿报告放在 `tmp/renders/ppt-analysis/<课件标题>/`；任务结束后删除临时工作文件，除非用户明确要求保留。
- 不要为了 PPT 审查创建新的根目录、raw/Wiki/RAG 分类或知识库框架路径。框架或分类变更必须由用户明确授权。
- 如果用户要求把 PPT 分析写入知识库，先确认已批准的目标位置；不要静默发明新 schema 路径。
- 最终教学分析不能只留在 `.hermes/`、仓库根目录随机文件或未命名输出目录中。必须报告准确保存路径，并说明临时/缓存文件是否已清理。

## 工作流程

### 1. 提取结构和文字

- 获取幻灯片页数、标题、正文、备注和媒体清单。
- 有可用的通用 PowerPoint skill 时，优先使用其常规提取路径。
- 标准提取不可用时，将 `.pptx` 当作 OOXML zip 检查：
  - 解析 `ppt/slides/slideN.xml` 中的文本框。
  - 解析 slide relationship 文件，识别图片、视频等嵌入媒体。
  - 检查 `ppt/media/` 中的图片、GIF、WMF、视频。
  - 如有备注页，解析 `ppt/notesSlides/`。
- 中间提取结果放入临时工作目录，不要与最终教学报告混淆。

可复用兜底脚本：

```bash
python skills/analyze/teaching-ppt-analysis/scripts/extract_pptx_structure.py --pptx <deck.pptx> --out-dir tmp/renders/ppt-analysis/<deck-title>
```

### 2. 渲染视觉结果

纯文本提取会漏掉很多课堂风险。能渲染时必须渲染：

- 用 LibreOffice 或 PowerPoint 将 `.pptx` 转成 PDF。
- 用 PyMuPDF 等 PDF 渲染器把页面渲染成图片。
- 为全课件生成 contact sheet。
- 单独检查高风险页面：公式页、视频页、密集图像页、习题页、总结页。

PDF 导出后可使用：

```bash
python skills/analyze/teaching-ppt-analysis/scripts/render_pdf_pages.py --pdf <deck.pdf>
```

### 3. 对照教学来源和知识库

如果课件与 LLMWiki 物理知识库相关：

- 用 `llmwiki-rag-retrieval` 检查相关教材、课标、题库或讲义页面。
- 判断课件是否覆盖必要内容。
- 核对物理正确性，不只看视觉效果。

重点核对：

- 实验条件。
- 图像或运动轨迹。
- 公式与适用条件。
- 课堂表述是否会造成误解。

### 4. 分析教学设计

每个主要部分都要问：

- 这一段由什么问题驱动？
- 是否清楚呈现“现象 → 证据 → 推理 → 模型”？
- 学生此时应该读、观察、推理、计算还是回答？
- 后排投影能否看清？
- 答案是否在学生思考前提前出现？

### 5. 写报告

使用清楚的教师语言，优先给出具体页码和可执行修改：

- “第7页：把‘加电场时 qvB=qE’改为‘电场和磁场同时作用且不偏转时 qE=qvB’，再单独讲只加磁场测半径。”
- “第14页：视频黑屏页需加播放提示和静态兜底图。”
- “第19页：答案红色提前显示，若用于课堂练习应设置点击出现。”

## 常见检查项

1. **视频占位风险**
   - 嵌入视频可能在渲染或教室电脑上显示为黑色矩形。
   - 建议添加“点击播放”提示，课前在教室电脑测试，并保留静态兜底图。

2. **公式/字体兼容风险**
   - 旧公式对象可能导出异常字形，例如速度符号、正负号或缺失符号。
   - 核心公式建议用公式编辑器或 OMML 重新录入。

3. **公式缺少物理条件**
   - 公式本身正确，也可能因条件缺失而造成教学错误。
   - 例：`qE=qvB` 需要电场和磁场同时作用且带电粒子不偏转；只加磁场做圆周运动是另一步。

4. **教材搬运式长文本**
   - 将长段文字改为时间线、对比表或“现象→推理→结论”结构。

5. **投影可读性**
   - 放大关键图、标签、科学计数法指数和常数。
   - 不要把核心量只写成很小的上标，例如 `10^-15 m` 和 `10^-10 m`。

6. **答案提前暴露**
   - 讲评课可提前显示红色答案；课堂练习应改成点击出现或下一页呈现。

## References

- `references/ppt-analysis-workflow.md`：物理教学课件分析的简明流程和常见问题清单。
