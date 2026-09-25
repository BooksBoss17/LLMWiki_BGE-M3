# OMML/EQ 标准化影响审计（2026-07-17）

本摘要不含原件文件名、公式正文或运行路径。完整逐公式报告仅保存在 `tmp/logs/`，不进入 Git 或整合包。

## 扫描范围

- DOCX：180
- OMML：2,979
- EQ：677
- 结构化公式总数：3,656
- 无法读取的 DOCX：0

## 标准转换结果

- OMML 自动转换：2,965
- OMML 需复核：14
- EQ 自动转换：585
- EQ 需复核：92
- 未知或损坏结构：0
- 受影响原件：21

需复核原因计数：

- OMML `phant`：17 次，分布在14个公式内。
- OMML `eqArr`：3 次。
- EQ overstrike：78 次。
- EQ box/复杂边框：14 次。

历史兼容候选仅作审计，不自动启用：

- `eq-overstrike-as-nuclear-scripts`：72
- `eq-x-top-as-vector`：6

## 决定

- 默认按 ECMA-376/ISO 29500 OMML 与 Microsoft EQ 官方语义转换。
- 任何 `needs_review` 或 `unsupported` 阻断最终 Markdown。
- 历史兼容必须由源 DOCX、公式序号和指令 SHA-256 绑定的视觉证据显式启用。
- 本轮不重转换 raw/Wiki，不触发图谱或 RAG 重建。
