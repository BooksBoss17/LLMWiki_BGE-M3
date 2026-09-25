# Source Library

`source-library/` 是知识库的二进制原件库，永久保存 PDF、DOCX、PPT、图片、音视频和导入时收到的其他原始材料。

## 规则

- 原件只增不减；完成 raw、Wiki 或 RAG 导入后也不得删除。
- Git 只跟踪本说明文件，不跟踪目录中的二进制原件。
- 教学检索不直接索引本目录；清洗后的 Markdown 进入 `raw/`。
- 脚本通过 `PROJECT_LAYOUT.yaml` 的 `library.source` 路径 ID 定位本目录，不得硬编码本机绝对路径。
