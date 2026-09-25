---
name: lecture-generation
description: "Use when generating lecture handouts, review sheets, lesson materials, or exam papers from LLMWiki_BGE-M3 using Wiki+RAG retrieval."
license: MIT
metadata:
  hermes:
    tags: [retrieve, llmwiki, physics, education]
    related_skills: ["llmwiki-rag-retrieval", "md-to-docx"]
---

# 讲义/复习讲义生成工作流

> 深度检索模式的应用：当用户（教师）要求生成讲义、复习讲义、组卷等教学材料时使用。
> v2: 2026-06-29 更新 — 内容适配讲义类型、知识框架用表格

## 适用场景

- "帮我生成一份XX的期末复习讲义"
- "出一章的复习课教案"
- "帮我设计一节XX的复习课"

## 工作流（7步）

### Step 1: 确定检索模式 + 内容范围

讲义/复习课 = 系统性备课 → **深度检索模式**（Wiki + dense/sparse k=10）

> **⚠️ 内容适配讲义类型（用户反馈）：**
> 期末复习 ≠ 高考复习。根据讲义类型决定是否包含考情分析：
> - **期末复习/章末复习** → 去掉考情分析、适用教材、复习范围、课型、来源标注等元信息。只留标题+知识点+题目。
> - **高考冲刺复习** → 保留考情分析（分值、难度、命题形式）。

### Step 2: Wiki 导航
1. read `LLMWiki/index.md` → 定位相关教材章节Wiki页
2. read 对应章节Wiki页 → 获取章总结 + 节目录 + raw原文指针
3. 跟随 wikilinks 读取相关知识点枢纽页（如有）

### Step 3: RAG 多查询检索
将复习范围拆成 4-6 个主题查询，每个查 dense + sparse k=10，合并去重：

```python
# 必须通过 terminal 调用 .venv，execute_code 无 faiss
# cd <KB_ROOT>/BGE-M3
# runtime/env/Scripts/python.exe -c "
import sys; sys.path.insert(0, 'scripts')
from rag_pipeline import RAGRetriever
r = RAGRetriever(); r.load()
queries = ['主题1关键词', '主题2关键词', ...]
for q in queries:
    dense = r.search(q, k=10, mode='dense')
    sparse = r.search(q, k=10, mode='sparse')
    # 合并去重，按 score 排序，取 top-6
# "
```

### Step 4: 读取具体题目
从 RAG 结果中识别相关题目 source 路径，read_file 读取 5-8 道代表性题目（含高考真题+模拟题），获取完整题干+答案+详解。

> **默认原题组题与回退门禁（用户反馈 2026-07-15）：**
> 1. 讲义、作业、学案、练习卷和试卷需要带题目时，默认直接采用题库完整原题，不改题干、数值、选项或小问，并复用该题 `assets` 原图。
> 2. 用户明确要求原创时可按要求生成原创题。完成深度检索后题库仍不足以覆盖指定知识点、题型或难度时，必须先向用户列明缺口、拟补题数量和题型并请求确认；只有用户确认后才生成原创补题。原创题和原创图必须清楚标为生成内容，且原创图仍须遵守下方题库参考图规则。
> 3. 每道候选题进入材料前，逐项检查 `## 题目`、选项/小问、`## 答案`、`## 详解`、正文图片引用、frontmatter `assets` 和实际图片语义。不能只确认文件存在或图片可打开。
> 4. 发现题干缺失、选项/小问不完整、明确指图但缺图、原图错配或图像语义错误时，立即暂停当前组题，不得跳过该题、换相似题或由 Role B 直接修改 raw。写入 `tmp/tasks/<task_id>/role_b_question_defect_handoff.json`，至少记录 `question_id`、`source_path`、`defect_type`、`evidence` 和 `return_to=lecture-generation`，路由到 Role A 的 `exercise-bank-import` 修复。
> 5. 只有 Role A 返回题库质量门禁、Wiki/图谱/RAG 同步均通过的修复结果后，才重新检索并继续组题。若复现结果表明缺陷来自本 skill、router、角色边界或验证门禁，停止业务生成并路由 Role C 的 `kb-framework-admin` 更新流程。

> **新题版/避重规则（用户反馈 2026-07-02）：**
> 当用户要求“题目不能采用我们出过的/最近写过的”时，必须先建立排除集，再选题：
> 1. 排除用户附加或指定文件夹中的所有来源题（按 `source_title`、`source_path`、题目 ID 三重排除）。
> 2. 排除之前试导出讲义中已使用的题目 ID。
> 3. 对热学题特别排除已知错图题：`MA0001332`、`MC0001336`、`C0000517`，除非维护 agent 已修复并通过图片复核。
> 4. 旧题库中部分题虽然来源可用，但 OCR/公式可能串错；必须抽读题干、答案、详解，发现明显乱码/公式错配（如图片公式混进答案、物理量乱码）则避用或人工重写答案。
> 5. 用户明确要求原创时可生成“原创新题/自绘图题”；某个必备考点在可用题库中缺少可靠题目时，先说明覆盖缺口并取得用户确认，再生成约定数量的原创补题。生成说明必须标注原创题。
> 6. 最终生成说明必须列明排除来源、采用题源、错图/乱码避用情况。

> **题图溯源与复用规则（用户反馈 2026-07-15）：**
> 1. 每道带图题在选题时都要保留题库题目 ID 与 `assets` 路径。题干直接采用题库原题且该题有原图时，将原图字节直接复制到 `output/assets_<name>/source/<题目ID>/` 后引用；不得为美观或统一风格重新绘制、重标注或以相似图替代。
> 2. 改编题只有在原图的研究对象、接触/连接关系、已知量和箭头语义仍全部适配时才可复用原图；任一项不适配即视为原创/改编图题，必须另绘示意图，并在生成记录中说明不复用的原因。
> 3. 原创/改编图题在调用绘图工具前，必须读取并查看题库中直接相关的同模型原图；若同模型有多张可靠原图，至少对照两张。以物体相对位置、斜面或轨道方向、接触/连接关系、受力或运动箭头和标注位置为准，禁止只按文字描述臆画。
> 4. 最终检查时逐题核对“原题复用原图”或“新图已列出题库参考图”的溯源记录；对外练习文件不显示内部溯源记录。
> 5. 原创/改编图不得在本 skill 内调用备用生图模型。将已确认题干、结构化图形描述、输出资产目录和 ASCII task ID 交给下一阶段 `learn/physics-diagram-toolkit/SKILL.md`；只有其 `completion.json`、参考图 review 和 semantic review 全部通过后，才把发布后的图片加入讲义或作业。

> **题目图/解析图分离门禁：**
> 1. 每张新图在 `tmp/tasks/<task_id>/diagram_assets.json` 中登记为 `usage=question_image|solution_image`，并记录题号、item ID、发布后 asset/completion 相对路径及 asset SHA-256。
> 2. `question_image` 必须来自 request `purpose=question`、spec v3 `exam-monochrome`，且 vectors/dimensions/annotations 中没有 `semantic_role=analysis`。题干明确给出的量必须绑定 `diagram_intent` fact。
> 3. `solution_image` 默认来自 `purpose=solution`、`solution-color`；仅当答案复用完全无分析层的题目图时，设置 `reuse_question_image=true`。
> 4. 练习 Markdown 只能引用登记的 question image；答案 Markdown 只能引用登记的 solution image 或显式复用的 clean question image。生成 Word/PDF 前运行：
>
> ```powershell
> python skills/retrieve/lecture-generation/scripts/validate_diagram_asset_separation.py --manifest tmp/tasks/<task_id>/diagram_assets.json --json
> ```
>
> 5. geometry report、completion、asset hash 或用途检查任一失败即停止导出；不得通过复制、改名或人工批准把解析图混入练习文件。

### Step 5: 读取现有讲义/考情分析（仅高考复习时）
如果 raw/lectures/ 下有对应主题的冲刺讲义，且讲义类型为高考复习，读取其考情分析部分作为讲义的考情参考。期末复习跳过此步。

### Step 6: 生成讲义
讲义结构模板（期末复习版，去掉考情分析）：

> **⚠️ 标准讲义结构（用户反馈 2026-07-01）：**
> 不要把讲义做成“习题集/练习卷”样式，也不要每道题用表格框住、左侧单独写“知识点栏”；这样浪费空间且不像学校讲义。讲义应采用 Wiki 中留存的标准讲义范式（如 `讲义16-热学和原子物理` / `设计-讲义16-热学和原子物理`）：
> - 结构是“**题型一：知识点凝练 → 典例 → 变式1-1 → 变式1-2；题型二：知识点凝练 → 典例 → 变式……；最后习题集/课堂练习**”。
> - 每个题型先给真正有用的干货知识：公式、适用条件、解题步骤、易错提醒；不要在开头放一张泛泛的知识框架表。
> - 题干和选项直接排版，不套框；题干/例题标题可加粗，选项按 A/B 或 A/B/C/D 分行或用 Tab 排版。
> - 热学讲义必须包含图像分析题（`p-V`、`p-T`、`V-T`、`p-1/V`、实验图像等），优先从最近期末复习卷和市质检卷的题库图片题中选。
> - 生成讲义前必须先从 Wiki/Raw 读取同主题标准讲义或讲义设计作为结构参考，而不是只参考练习卷格式。

推荐结构：
1. **题型一：分子动理论与固液基础** — 知识点凝练 + 典例 + 变式
2. **题型二：气体实验定律与装置建模** — 知识点凝练 + 液柱/活塞/传感器典例 + 变式
3. **题型三：气体图像与热力学第一定律综合** — 知识点凝练 + 图像典例 + 变式
4. **题型四：热学实验** — 等温变化实验 + 油膜法 + 图像/误差分析
5. **习题集/课堂练习** — 不套框，保留图像题；答案、解析和方法点拨全部进入独立答案文件

**练习与答案成对交付（用户反馈 2026-07-16）：**

1. 讲义、作业和练习卷默认生成 `<名称>.md` 与 `<名称>答案.md` 两份源文件，不再生成或使用“学生版/教师版”命名。
2. `<名称>.md` 是练习文件。讲义知识点凝练可正常保留，但每道典例、变式、课堂练习或作业题下方不得附答案、解析、方法点拨或提示性结论。
3. 每道题的本地题号必须写在题干之前，并与题干处于同一个 Markdown 段落；推荐写法为 `**7.** 汽车以 $54\,\mathrm{km/h}$ 的速度行驶。`。不得使用独立的 `### 7.` 或其他只含题号的标题行，避免在题干前浪费一个空白行。
4. 普通作业和课后练习只呈现题目内容。**每一道解答题**都在最后一个小问后默认保留 3 个自然空白段落：源 Markdown 紧跟该题使用 4 个连续空行（其中 1 个是结构分隔，另外 3 个由 `md-to-docx` 转为空白段落）；此规则也适用于文件末题。不得添加“答题区”“答：”、连续下划线、横线、边框、表格或其他可见占位符。只有用户明确要求可直接在卷面完整作答的测试卷时，才按其要求增加空白段落。
5. `<名称>答案.md` 按练习文件中的题型、题号、选项和小问顺序一一对应，集中给出答案、必要解析和方法点拨。不得遗漏图片题或只给最终结果而缺少用户要求的过程。
6. 内容调整后同步检查两份文件：练习文件不得泄露答案，答案文件不得错号、漏题或引用不同版本题干。

**内部溯源与对外净版：**

- 选题时把练习本地题号、题库 ID、原题号、`source_title`、`source_path` 和 `assets` 写入任务内 `tmp/tasks/<task_id>/question_source_map.json`，用于答案配对、原图复用和质量复核。
- 对外交付的练习文件与答案文件只使用本套材料的本地题号，不显示 `[题库:...]`、`[教材:...]`、`原题XX`、题库 ID、源路径、内部标签或其他溯源记录。
- 教师讲义正文只有在用户明确要求显示参考来源时，才使用面向教师的可读来源说明；内部题库 ID 仍不进入题目标题或题干。

**公式格式：** 使用 LaTeX 闭合（`$...$` 或 `$$...$$`），转 Word 时用 OMML 格式（见 `references/md-to-docx-compact-a4.md`）。

### Step 7: 保存成对文件并导出
```
LLMWiki_BGE-M3/output/<讲义名>.md
LLMWiki_BGE-M3/output/<讲义名>答案.md
```
**不要保存到 raw/ 子目录** — raw/ 是原始资料区，output/ 是生成物区。

必须把两份完成版 Markdown 分别交给 `retrieve/md-to-docx/SKILL.md`，不要在本 skill 内重写 DOCX/PDF 生成器：

```bash
python skills/retrieve/md-to-docx/scripts/md2docx.py output/<讲义名>.md output/<讲义名>.docx --layout validation --officecli-qa required
python skills/retrieve/md-to-docx/scripts/md2docx.py output/<讲义名>答案.md output/<讲义名>答案.docx --layout validation --officecli-qa required
```

- 过程草稿可用 `--officecli-qa auto`；最终成对交付时两份文件都必须用 `--officecli-qa required`。
- OpenXML 校验失败、截图失败或发现缺图/占位符/明显溢出风险时，先修复后分别重跑。
- 两份文件各自生成的 `contact_sheet.png` 都必须视觉检查；无法完成视觉检查时在最终说明中明确写出。
- 讲义、练习卷、试卷的图片和示意图资产放在 `output/assets_<讲义名>/`，不要写入 raw/Wiki/RAG。

## 验证
- [ ] 讲义覆盖用户指定的全部章节
- [ ] 每个知识点有教材原文支撑
- [ ] 候选例题在题库中具有完整题干、答案、详解和方法点拨，可供成对文件使用
- [ ] 练习文件中的每道题下方均无答案、解析、方法点拨或提示性结论
- [ ] 每道题均为“题号在前、题号与题干同一段落”，未使用独立题号标题行
- [ ] 普通作业/课后练习的每一道解答题（包括文件末题）后都有且只有 3 个自然空白段落，无“答题区”“答：”、下划线、横线、边框或表格；用户明确要求卷面作答空间时才扩展
- [ ] 答案文件与练习文件的题型、题号、选项、小问和图片题一一对应
- [ ] 公式在 Markdown 中用 LaTeX 闭合（`$...$` 或 `$$...$$`），DOCX 中转为 OMML，PDF 中未出现字面量 `\mathrm`、`\sim` 等 LaTeX 命令
- [ ] `question_source_map.json` 的本地题号、题库 ID、原题号、源路径和图片资产映射完整
- [ ] 对外练习与答案只显示本地题号，未泄露题库 ID、原题号、源路径或内部来源标签
- [ ] 非用户指定原创题均来自题库原题；题库覆盖不足时已先取得用户确认，原创补题已单独标注
- [ ] 每道候选题的题干、选项/小问、答案/详解、图片引用、`assets` 和图像语义均已检查
- [ ] 发现题干缺失/错图时已完成 Role A 修复闭环；skill/router 问题已转 Role C，未在 Role B 静默绕过
- [ ] 原创/改编图已通过 `physics-diagram-toolkit` 的视觉参考、确定性渲染和 hash 绑定语义复核；未调用备用生图模型
- [ ] `diagram_assets.json` 中 question/solution 图片用途、completion 和 SHA-256 完整，`validate_diagram_asset_separation.py` 通过
- [ ] 练习文件未引用 solution image；答案文件只引用 solution image 或显式复用的 clean question image
- [ ] 文件保存到 output/ 目录
- [ ] 知识框架用表格而非 ASCII 树状图
- [ ] 根据讲义类型（期末 vs 高考）决定是否包含考情分析
- [ ] 练习与答案均已输出同名 Markdown、DOCX、PDF，不使用学生版/教师版命名
- [ ] 两套 Word/PDF 均经 `md-to-docx` 导出并使用 `--officecli-qa required`
- [ ] 两套 OfficeCLI QA 均通过 `validate`，且各自 `contact_sheet.png` 已完成视觉检查
