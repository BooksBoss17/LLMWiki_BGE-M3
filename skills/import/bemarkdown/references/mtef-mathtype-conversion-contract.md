# MTEF / MathType 结构转换与复核合同

## 目录

1. [目标与零错误定义](#1-目标与零错误定义)
2. [关系与哈希链](#2-关系与哈希链)
3. [运行库与许可边界](#3-运行库与许可边界)
4. [解析和映射合同](#4-解析和映射合同)
5. [三源机器门禁](#5-三源机器门禁)
6. [视觉复核合同](#6-视觉复核合同)
7. [状态与长任务保障](#7-状态与长任务保障)
8. [WMF 渲染合同](#8-wmf-渲染合同)
9. [CLI 与验收](#9-cli-与验收)

## 1. 目标与零错误定义

旧 Word 公式固定采用：

```text
Equation OLE → Equation Native → MTEF v3/v5 → XML AST → MathML → KB-LaTeX
                                      ↘ WMF 全幅渲染 → PP-FormulaNet + TexTeller
```

“零错误”不是声称 OCR 永远正确，而是：

- 三个独立来源完全一致且没有风险标志时才机器自动通过。
- 其他项目必须由 Agent 查看全分辨率证据并提交 hash 绑定结论。
- 仍不可辨认时保持 unresolved 并请求教师确认。
- unresolved、blocked、旧 completion 或部分成功都不得生成可入库最终 Markdown。

## 2. 关系与哈希链

从 `word/document.xml` 的同一个 `w:object` 读取：

- `o:OLEObject/@r:id` → `word/embeddings/*.bin`。
- `v:imagedata/@r:id` → `word/media/*.wmf`。
- `ProgID` 只作为来源证据，不替代 relationship。

每个出现位置必须记录：

- DOCX SHA-256。
- OLE 容器 SHA-256。
- 完整 `Equation Native` 流 SHA-256。
- 去除 28 字节头后的 MTEF SHA-256。
- WMF 预览 SHA-256；渲染后另记 PNG SHA-256。

关系目标必须位于 DOCX ZIP 内部；外部关系、缺目标、缺 `Equation Native`、头长度不是 28、版本不是 3/5 都阻断。重复出现按 MTEF hash 复用结构和复核结果，但每个 DOCX/对象/预览位置仍保留审计行。同一 MTEF 对应多个 WMF 时，所有预览必须通过或被同一次复核覆盖。

## 3. 运行库与许可边界

组件 ID 与固定版本：

- `temurin-jre-21.0.11+10`。
- `jruby-complete-9.3.8.0`。
- `transpect-mathtype-0.0.7.5`，固定 revision `c1f788c7857802193220d370894ae52a2ce40d6c`。
- `apache-batik-1.19`。
- `olefile-0.47`。

Transpect 仅携带 Ruby `mathtype` parser core 和必要依赖：

- mathtype：MIT。
- BinData：BSD-2-Clause 可选许可。
- Nokogiri Java：MIT。
- ruby-ole：MIT。

禁止携带 Transpect 顶层许可未在本项目逐项确认的 Java、XProc、XSLT、测试和构建胶水。详细上游 URL、文件 hash 和再分发条件以 `skills/_shared/model-tools/registry.yaml`、`THIRD_PARTY_LICENSES.md` 和便携包 manifest 为准。

运行时调用固定通过：

```bash
python skills/_shared/model-tools/scripts/model_runtime.py exec --id jruby-complete-9.3.8.0 -- ...
python skills/_shared/model-tools/scripts/model_runtime.py exec --id apache-batik-1.19 -- ...
```

不得回退到全局 Java、JRuby、`npx` 或 Agent 私有缓存。

## 4. 解析和映射合同

### 4.1 MTEF parser

- 只接受 v3/v5。
- 每个分片最多 512 个唯一 MTEF；一个 JVM 处理完整分片。
- parser 必须从首字节开始并完整消费载荷。
- 未知记录、未知 record choice、截断字段、无闭合 END 或尾随非标准数据都阻断。
- 多段或疑似重复载荷不得拼接猜测；保留全部 bytes 和预览证据，交视觉复核。

### 4.2 XML AST → MathML

仓库自有映射层只处理显式支持的 record/template/variation：

- 字符、slot、分式、根式、上下标、常见 fence。
- sum/product/integral 及上下限。
- pile、matrix、常见 accent/bar/prime。
- 标准 non-marking MathType 间距码只映射为 MathML `mspace`，不产生语义字符。

未知模板、未知 embellishment、私用区非间距字形、缺 slot 或矩阵维度不一致必须返回 `blocked`。禁止递归拼接可见字符冒充结构解析。

### 4.3 MathML → KB-LaTeX

目标是 Wiki/MathJax 与现有 Word 输出链共同支持的保守子集，不追求任意 TeX：

- `\frac`、`\sqrt`、`_`、`^`。
- `\left...\right`、常见 Greek/operator。
- `\sum`、`\prod`、`\int` 及上下限。
- `matrix`、常见 over/under accent。

不进行代数化简、单位推断、字符猜测或“看起来等价”的激进规范化。

## 5. 三源机器门禁

状态：

- `structure_candidate`：MTEF 严格解析和映射通过，仅是结构候选。
- `machine_final`：三源完全一致且通过全部风险门禁。
- `needs_vlm`：结构/视觉有候选但不足以自动通过。
- `reviewed_final`：Agent/教师提交的 hash 绑定最终结论。
- `blocked`：容器、parser、映射、渲染或必需证据失败。

`machine_final` 同时要求：

1. MTEF parser 完整消费并输出合法 MathML/KB-LaTeX。
2. WMF→PNG 非空、非小图、无裁切、比例误差不超过合同阈值。
3. MTEF、PP-FormulaNet、TexTeller 的保守规范化结果完全一致。
4. 三个 engine 都真实返回，不允许用重复模型结果补位。
5. 不含单字符、中文公式文本、矢量、核素/多重上下标、复杂分式/根式、矩阵/多重积分等风险标志。

规范化只移除外围数学定界符、`\left/\right` 和空白；不得交换项、改符号、改上下标或做语义等价替换。

## 6. 视觉复核合同

MTEF 复核记录必须包含：

- `review_key == mtef_sha256`。
- 当前 `runtime_fingerprint`。
- 当前 MTEF SHA-256。
- 该 MTEF 对应的完整 `previews` 列表，每项含 WMF/PNG SHA-256。
- `status=resolved|unresolved`。
- resolved 时的 `final_kind=latex|empty`、非空 `visual_evidence`、0–1 `confidence`；`latex` 必须给出 `final_latex`。
- `empty` 仅允许用于结构映射明确为空、且该 MTEF 的全部 hash 绑定预览均为 `blank_render` 的空 Equation 对象；最终转换必须移除对象，不得写入占位公式。

独立 WMF 复核记录必须包含：

- `review_key=wmf:<wmf_sha256>`。
- WMF/PNG SHA-256 和 runtime fingerprint。
- `final_kind=latex|text|image`。
- `final_value`、视觉证据和置信度。

任一 hash、预览集合或 runtime fingerprint 漂移时整条复核失效。复核结果写为 `reviewed_final`，不得伪装成 `machine_final`。

## 7. 状态与长任务保障

状态目录：`tmp/state/bemarkdown/<task-id>/`。核心数据库：`formula_audit.sqlite3`。

- SQLite 固定 WAL、`synchronous=FULL`、外键开启。
- task lock 阻止同一 task-id 并发写入；进程消失后才允许清理 stale lock。
- 文档、公式出现位置、唯一 MTEF、预览、模型证据、复核和 attempt 分表保存。
- JRuby 分片写 `.partial` JSONL 并逐条 flush/fsync；完整后原子替换。
- parser fingerprint 绑定 JRE/JRuby/parser/worker；映射合同更新只重算已有 XML，不重复解析。
- runtime fingerprint 绑定 parser、映射、渲染、模型和合同；变化自动使机器结论与复核失效。
- 同一 runtime fingerprint 内，PP-FormulaNet 与 TexTeller 只允许按完整 PNG 文件的 SHA-256 去重推理；结果须重新绑定到每个 WMF/PNG 位置。感知哈希、文件名、MTEF hash 或近似图片都不得代替 PNG SHA-256；相同 PNG hash 的已完成证据若不一致必须阻断。
- 两个视觉模型在同一 GPU 上顺序运行，避免模型常驻显存之和超过设备容量；每个引擎仍只创建一个长期进程。模型总超时到达时必须清理 Windows 完整进程树，已 fsync 的逐项 JSONL 作为下次续跑边界。
- `--resume` 为默认；`--rebuild` 才强制重算。

只有 unresolved=0 且 SQLite `integrity_check=ok` 才原子写 `completion.json`。失败或未完成时必须删除旧 completion。

## 8. WMF 渲染合同

渲染顺序：

1. 解析 placeable WMF 原生 bounds，并逐记录校验完整性。
2. 在隔离子进程中移除非绘图 `MFCOMMENT`，修复 METAHEADER 大小，再由 Pillow 的 Windows `drawwmf` 直接按原生 DPI 播放；逐项输出可续跑。
3. 原生结果失败或几何异常时，使用 Batik WMF→SVG→PNG。
4. Batik 几何异常时，允许 LibreOffice 输出诊断 SVG；只有其语义 `BoundingBox` 与源 bounds 比例严格一致时，才移除 A4 外壳并交 bundled Batik 栅格化。A4、比例不符、空白、边缘裁切或内容占比异常均不能自动通过。
5. 上述路径仍失败时，预编译经典 GDI 适配器作为最终隔离回退；超时按分片二分，不得把失败渲染标成成功。

输出必须保持原始比例，长边至少 300 px，不裁切、不拉伸、不做后置 PIL 缩放。批量失败时按小分片重试并二分定位；超时必须清理进程树和未完成临时文件。

## 9. CLI 与验收

兼容入口：

```bash
python scripts/batch_convert_all.py INPUT OUTPUT
```

新增参数：

```text
--mtef-mode shadow|strict|off
--audit-only
--task-id ASCII_ID
--resume
--rebuild
--json
```

全库首次运行：

```bash
python scripts/batch_convert_all.py <source> <output> --mtef-mode shadow --audit-only --task-id <id> --json
```

验收后生产转换使用 `strict`。`off` 仅用于明确的历史诊断，不得把双 OCR 结果当成新的 MTEF 最终结论。

全库验收至少检查：

- 所有 Equation 出现位置都有 DOCX/OLE/MTEF/WMF hash 链。
- v3/v5 零未知、零损坏、零未消费；例外必须有明确 hash 绑定复核，不得改成“忽略尾部”。
- 所有 WMF 预览关系完整且渲染几何通过。
- `unresolved_mtef=0`、`unresolved_independent_wmf=0`。
- 中断 JRuby、Batik/GDI、FormulaNet、TexTeller 后续跑无漏项、无重复提交。
- 第二次运行只读取有效缓存。
- 中文和空格路径、屏蔽全局 Java/JRuby/LibreOffice 后烟测通过。
