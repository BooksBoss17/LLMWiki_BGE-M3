# WMF → LaTeX 批量重新转换

> 当 BeMarkdown 转换后仍残留 `.wmf` 公式引用时，必须从原始 DOCX 重新走共享公式链。

## 当前流程

`batch_convert_all.py` 的 `convert_one_docx(docx_path, out_dir, latex_ocr=None)` 仍保留第三个参数以兼容旧调用，但该参数已忽略。WMF 的实际流程为：

```text
原始 DOCX → 共享 GDI+/LibreOffice 高分辨率 PNG 渲染
          → PP-FormulaNet_plus-L + TexTeller
          → 双引擎一致才自动写入 LaTeX；否则保留图像并生成视觉复核任务
```

不要使用 PIL 直接转换 WMF：其默认 DPI 过低，公式图像不可作为识别证据。

## 单文件重新转换

在仓库根目录执行：

```powershell
python skills/import/bemarkdown/scripts/batch_convert_all.py <原始DOCX目录> <临时输出目录>
```

脚本会解析共享模型 ID 并调用 PP-FormulaNet_plus-L 与 TexTeller。

## 批量处理原则

1. 每个 DOCX 先输出到 `tmp/` 的独立任务目录，保留原始 DOCX 和 SHA-256。
2. 仅当 `formula_review_tasks.jsonl` 没有 unresolved 项时，才将 Markdown 与 media 提升到 `raw/`。
3. 未达成双引擎共识的公式必须保留原图并交视觉复核，不能以置信度或旧速度记录替代。
4. 验证通过后再替换目标 Markdown 和 media；不要覆盖原件库中的源文件。

## 验证

- Markdown 中无已解决的 `.wmf` 残留引用。
- 每个写入 LaTeX 的 WMF 都有 hash 绑定的两引擎候选或复核记录。
- 渲染 PNG 保持完整原始边界，尺寸不是 PIL 的低 DPI 缩略图。
- 所有未解决公式仍保留图像引用，未被猜测性替换。
