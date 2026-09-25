# DOCX 图片型题干的 OCR 兜底流程

## 触发信号

在导入 DOC/DOCX 练习卷时，BeMarkdown 完整流程结束后，如果转换出的 `_final.md` 出现以下情况，不能直接进入正则/LLM 切题：

- `qstart_count` 很少或为 0，但图片引用很多；
- 正文主要是 `![图](media/...)`，题干文字几乎没有；
- 答案区也以图片形式存在；
- DOCX 是由截图/图片排版生成，Word 内部没有可提取文本。

典型例子：`“高考”假期作业2——热力学定律综合练习.docx`，BeMarkdown 输出仅约 1KB、20 张图片、0 个题号，但实际 PDF/OCR 后有约 13 题。

## 正确处理

1. **仍先跑 BeMarkdown 完整流程**
   - 保留 OMML/EQ/WMF 与共享 PP-FormulaNet/TexTeller 公式链的转换结果；
   - 该输出作为图片、公式、答案校验的 backup。

2. **将 DOC/DOCX 导出为 PDF**
   ```bash
   "/c/Program Files/LibreOffice/program/soffice.exe" --headless --convert-to pdf --outdir "$PDF_OUT" "$DOCX_IN"/*.docx
   ```

3. **对导出的 PDF 用 marker OCR**
   ```bash
   env -u PYTHONPATH -u PYTHONHOME \
     "%LOCALAPPDATA%/Programs/Python/Python310/Scripts/marker" \
     "$PDF_OUT" --output_dir "$OCR_OUT" --force_ocr
   ```

4. **以 OCR Markdown 为 primary，以 BeMarkdown `_final.md` 为 backup**
   - 子 agent/LLM 切题时读取 `primary_md=docx_pdf_md/.../*.md`；
   - 必要时读取 `backup_md=docx_md/.../*_final.md` 补图片、公式、答案。

5. **提取前做题号统计**
   - 扫描 OCR MD 的题号起点、答案标记、图片数、`$` 数；
   - 若 OCR 后题号数量仍明显低于预期，渲染页面用 VLM 补识别，不要留下“见原件”或“题干缺失”等占位文字。

## 注意

- 不要因为 BeMarkdown 输出没有文字就判定源文件不可导入。
- 不要跳过 BeMarkdown：它仍可能提供更高质量的公式/图片资源。
- OCR 结果中可能有西里尔字母误识（如 `В` 应为 `B`），答案表需要规范化。
