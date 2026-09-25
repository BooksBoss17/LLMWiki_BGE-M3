# OMML / EQ → 可移植 LaTeX 转换契约

## 权威来源

- OMML：ECMA-376 Part 1 §22.1、ISO/IEC 29500-1 Office Math。
  - <https://ecma-international.org/publications-and-standards/standards/ecma-376/>
  - <https://www.iso.org/standard/71691.html>
- Microsoft 实现说明：<https://learn.microsoft.com/en-us/openspecs/office_standards/ms-oi29500/94590ec4-e4c1-4f7a-b967-abe6bf8658b2>
- Word EQ 域语义：<https://support.microsoft.com/en-us/word/field-codes-eq-equation-field>

ECMA/ISO 定义 OMML 源结构，不定义唯一的 LaTeX 输出。自动转换只使用 Wiki/MathJax 与现有 Word 输出链共同支持的可移植子集。

## 结果与门禁

每个公式必须返回：

- `converted`：可生成最终 LaTeX。
- `needs_review`：源结构已识别，但可移植 LaTeX 无法无损表达。
- `unsupported`：结构未知、字段损坏或必需子节点缺失。

`needs_review` 和 `unsupported` 的 `latex` 必须为 `null`。DOCX 中任一公式未转换时，仅写 `formula_conversion_report.json`，不写新的最终 Markdown。

报告绑定 DOCX SHA-256；每条公式另记 `source_kind`、同类型 1 起始序号、公式源 SHA-256、标准引用、警告、问题和已用兼容规则。

## OMML 映射边界

自动转换：分式、根式、前后置上下标、定界符及分隔符、上划线、常见重音、常见 n 元运算符、函数和极限。

必须复核：`phant`、`eqArr`、复杂框线/box、矩阵、分组字符、下划线以及目标链不稳定的重音或运算符。

未知 OMML 节点禁止递归拼接子文本；必须报告节点名和 XML 路径。

## EQ 映射边界

- `\f(a,b)` → `\frac{a}{b}`。
- `\r(x)` → `\sqrt{x}`；`\r(n,x)` → `\sqrt[n]{x}`。
- `\s\upN(x)` / `\s\doN(x)` → 上/下标；位移点数记为布局警告。
- `\x\to(x)` 是上边框，标准输出为 `\overline{x}`，不是矢量箭头。
- `\o()` 是叠印；不得自动解释为核素上下标。
- 单列单项 `\a()` 可保留内容；多列或多项数组进入复核。
- 无开关或复杂边框的 `\x()` 进入复核。

未知指令、参数数量错误、括号不闭合或 Word field begin/separate/end 不平衡均为 `unsupported`。

## 历史兼容覆盖

仅允许两个规则：

- `eq-x-top-as-vector`
- `eq-overstrike-as-nuclear-scripts`

覆盖 JSON 必须包含 `schema_version: 1`、DOCX `source_sha256`，并为每条 EQ 公式提供：

- `source_kind: eq`
- `formula_index`
- `instruction_sha256`
- `rule_id`
- 非空 `visual_evidence`

任一 hash 或序号不匹配时覆盖无效，转换继续按标准语义执行。
