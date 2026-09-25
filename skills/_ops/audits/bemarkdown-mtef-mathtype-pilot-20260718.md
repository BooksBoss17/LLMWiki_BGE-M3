# BeMarkdown MTEF/MathType 脱敏试点审计（2026-07-18）

## 结论

- 状态：`pilot_complete`，不是全库三源验收完成。
- 用户将视觉模型范围收缩为统计试点；不再续跑全量 TexTeller，也不生成 `completion.json`。
- 结构审计覆盖全库；视觉统计只使用当前 runtime fingerprint 下的精确 PNG SHA-256 证据。
- 本轮未修改 `raw/`、`LLMWiki/`、图谱或 RAG。

## 审计范围

| 项目 | 结果 |
|---|---:|
| DOCX | 180 |
| Equation OLE 出现位置 | 32,793 |
| 唯一 MTEF | 16,763 |
| MTEF v3 / v5 出现位置 | 29 / 32,764 |
| Equation WMF 预览 | 19,646 |
| 独立 WMF | 27 |
| 唯一渲染 PNG SHA-256 | 16,115 |
| runtime fingerprint | `b77b086e4a6898c349fe79bfec5168de9137090bdbf0a82cdc5552267e2147c1` |

32,793 个 Equation 位置的 DOCX、OLE、MTEF、WMF 四层 hash 缺失均为 0；SQLite `integrity_check=ok`。17 个空白预览由 3 条 hash 绑定复核恢复为 `reviewed_empty/reviewed_final`。

## 结构与渲染结果

- parser：16,762 个 `parsed`，1 个严格解析阻断。
- 最终结构状态：`machine_final=5`、`reviewed_final=3`、`needs_vlm=16,738`、`blocked=17`。
- 17 个阻断项均保持失败阻断：1 个非标准版本/尾部记录，16 个未映射私用字形或替换字符；未静默压平或猜测。
- 预览：19,656 个 `rendered`，17 个 `reviewed_empty`；原 MathType WMF 挂起 fixture 已由记录过滤适配器修复。

## 缩减后的视觉统计

- PP-FormulaNet 已在范围纠正前完成 16,115 个唯一 PNG；证据只保存在忽略的本地状态库，不作为全库完成声明。
- TexTeller 保留 480 个确定性 PNG hash 样本（约 3%），不再续跑其余 15,635 个。
- 双模型保守规范化完全一致：20 / 480。
- 进入 MTEF + PP-FormulaNet + TexTeller 三源比较的预览对：1,011；字符串完全一致：15；通过几何和风险门禁后形成 5 个 `machine_final` MTEF。
- 样本覆盖 561 个唯一 v5 MTEF，未覆盖 v3；v3 由全库结构解析和专用 fixture 测试覆盖，视觉统计不得外推为 v3 质量结论。

这些结果说明扩大 OCR 扫描量不能替代视觉复核：三源不一致或高风险项必须保持 `needs_vlm/blocked`。27 个独立 WMF 仍需 `latex|text|image` 人工/Agent 分类，不能由双 OCR 自动定稿。

## 性能与运行库证据

- PP-FormulaNet 与 TexTeller 改为同 GPU 顺序长期进程；并发基线曾占用约 11.9 / 12.3 GiB 且 GPU 利用率仅约 2%，已禁止该模式。
- PP-FormulaNet 使用 batch 16；TexTeller 使用 batch 32；精确 PNG SHA-256 去重，禁止感知哈希或文件名复用。
- Windows 超时会清理完整进程树；1 秒真实超时烟测后无遗留 Python 子进程，显存回落到约 0.6 GiB。
- doctor：31 个组件，`ok=true`、`warnings_present=false`；PP-FormulaNet=`gpu:0`，TexTeller=`cuda:0`，cuDNN build/runtime 均为 9.9.0。

## 门禁状态

- `formula_audit_summary.json.ok=false`。
- `unresolved_mtef=16,755`，`unresolved_independent_wmf=27`。
- `completion.json` 不存在；任何最终 Markdown 仍被阻断。
- 当前结论只证明工具链、结构审计、断点续跑和缩减视觉统计有效，不证明全库公式已达到三源零未解决验收。
