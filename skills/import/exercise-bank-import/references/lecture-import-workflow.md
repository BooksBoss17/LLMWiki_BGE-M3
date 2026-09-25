# 讲义导入工作流（BeMarkdown + delegate_task LLM配对）

## 适用场景

讲义类 DOCX（含知识点讲解 + 典例 + 变式），不是纯试卷。需要：
- 保留完整 MD 到 `raw/lectures/讲义XX-<标题>/`
- 提取题目到 `raw/exercises/<知识点>/`
- 讲义原位置留指针

## 完整步骤

### Step 1: 原件存档
复制原卷版+解析版 DOCX 到 `source-library/` 目录。

### Step 2: BeMarkdown 转换
```python
from batch_convert_all import convert_one_docx
# 分别转换原卷版和解析版
for label in ["原卷版", "解析版"]:
    convert_one_docx(docx_path, out_dir)
```
- 输出：`<标题>_final.md` + `media/` 目录
- 检查是否有 base64 data URI（如有需先提取）
- 检查是否有 OMML/WMF 公式（convert_one_docx 自动处理）

### Step 3: delegate_task LLM 配对
```python
delegate_task(
    context="原卷MD路径 + 解析MD路径 + 配对规则",
    goal="读取原卷和解析MD，逐题配对输出JSON数组",
    toolsets=["file"]
)
```

配对规则要点：
- 按内容匹配不按题号（讲义题号可能混乱）
- 讲义标记【典例】【变式】需先 `re.sub(r'\*+', '', text)` 去星号
- 多选题标注(多选)→MA
- 找不到答案填"无"
- 知识点分类统一用讲义对应的知识点

输出 JSON 格式：
```json
[
  {
    "question_no": "1",
    "question_type": "MC",
    "knowledge_points": ["力与物体的平衡"],
    "question_text": "题目完整正文",
    "answer": "B",
    "explanation": "详解完整正文"
  }
]
```

### Step 4: 验证配对结果
- 检查 JSON 文件是否存在且非空（429 静默失败！）
- 检查每题 question_text 非空
- 检查答案字段存在
- 检查详解字段存在且 > 20 字符
- 统计题型分布

### Step 5: 生成题目 MD 文件
- 分配全局递增 ID（从当前 max_id + 1 开始）
- 复制 media 到 `raw/exercises/<知识点>/media/`
- 生成标准 Question MD（frontmatter + 题目 + 答案 + 详解）

### Step 6: 保留讲义完整 MD
- 复制解析版 `_final.md` 到 `raw/lectures/讲义XX-<标题>/讲义XX-<标题>-解析.md`
- 复制原卷版 `_final.md` 到 `raw/lectures/讲义XX-<标题>/讲义XX-<标题>-原卷.md`
- 复制 media 到讲义目录

### Step 7: 质量检查 3 圈
- 格式层：frontmatter、$闭合、图片断链、ID唯一
- 内容层：题干 > 50c、详解 > 20c、无占位符
- 对应关系层：答案 = 故选X

### Step 8: 清理临时文件
删除 `_tmp_exer_lecXX` 临时目录。

## 实际案例

### 讲义01：力与物体的平衡
- 24题（MC×13 MA×6 E×2 C×3），ID 711~734
- 0 OMML/WMF 公式（公式为 PNG 图片）
- 133 + 361 = 494 张图片
- 配对成功率 100%（0 答案=无，0 详解短）
- 连续 3 圈 0 issues

## 注意事项

- `convert_one_docx` 在 `batch_convert_all.py` 中，不是独立模块
- 讲义的 media 图片比试卷多很多（含知识点讲解的配图）
- BeMarkdown 转换 0 OMML 不代表没有公式，可能是 PNG 图片格式
- delegate_task 配对耗时约 5-6 分钟（24题级别）
