# 批量讲义导入模式（实践版）

> 2026-06-29 导入 16 个三轮冲刺讲义 + 4 个题型专练 + 4 个押题卷时总结的标准模式。

## 适用场景

用户有一个目录下 N 个讲义/试卷 DOCX（每个含原卷版+解析版），需要逐个导入题库。

## 标准流程（每个讲义/试卷）

```
1. 原件存档 → shutil.copy2 到 source-library/ 目录
2. BeMarkdown 转换 → batch_convert_all.convert_one_docx() 转原卷+解析
3. delegate_task 配对 → 子agent读两份MD，输出 paired_questions.json
4. 生成题目MD → 主agent读JSON，生成标准格式MD到 raw/exercises/<知识点>/
5. 保留讲义MD → copy _final.md 到 raw/lectures/讲义NN-标题/（讲义才需要，试卷跳过）
6. 清理临时 → shutil.rmtree(_tmp_ 目录)
7. 质量检查3圈 → 只检查本次新增的题目（按ID范围过滤）
```

## 关键代码模式

### 批量转换 + delegate_task 配对

```python
import os, shutil, sys
sys.path.insert(0, os.path.join(os.environ.get('LOCALAPPDATA', ''), 'hermes', 'skills', 'productivity', 'bemarkdown', 'scripts'))
sys.path.insert(0, r"%USERPROFILE%\Desktop\<外部暂存区，路径不进仓库>")
from batch_convert_all import convert_one_docx

# 原件存档
for f in os.listdir(src_dir):
    shutil.copy2(os.path.join(src_dir, f), os.path.join(原件_dir, f))

# BeMarkdown 转换
for label in ["原卷版", "解析版"]:
    for f in os.listdir(原件_dir):
        if NN in f and 关键词 in f and label in f:
            convert_one_docx(os.path.join(原件_dir, f), os.path.join(tmp_dir, label))
```

### delegate_task 配对模板

```
context: 原卷MD路径 + 解析MD路径 + JSON输出路径
goal: 读取原卷和解析MD，逐题配对输出JSON数组，保存到指定路径。
注意：知识点统一用"<分类名>"，多选→MA，计算→C，找不到答案填"无"
```

### 生成题目MD的标准模板

```python
md_content = f"""---
id: {qid}
question_type: {q_type}
knowledge_points:
  - {kp}
difficulty: 0.50
difficulty_reason: "LLM初判，待人工确认"
source_type: exercise_sheet
source_title: "高考物理终极冲刺{NN} {标题}"
source_path: "../source-library/{文件名}"
source_question_no: "{q_no}"
assets:
{chr(10).join(f'  - "{m}"' for m in media_refs) if media_refs else '  - ""'}
review_status: auto
classification_confidence: 0.80
classification_notes: "题型和知识点由LLM初判"
---

# {qid}

## 题目

{q_text}

## 答案

{a_text}

## 详解

{e_text}
"""
```

### 质量检查（只检查新文件）

```python
def check_new():
    issues = []
    for fn in sorted(os.listdir(ex_dir)):
        if not fn.endswith('.md'): continue
        id_num = fn.replace('.md','').replace('MC','').replace('MA','').replace('B','').replace('C','').replace('E','')
        try:
            if int(id_num) < max_id+1: continue  # 跳过旧文件
        except: continue
        # 检查格式+内容+对应关系
    return issues
```

## 常见问题及解决

| 问题 | 解决方案 |
|------|---------|
| 子agent JSON含中文引号导致解析失败 | 主agent兜底：替换\u201c/\u201d为"，逐行正则转义内部" |
| 子agent因429返回completed但没写文件 | 检查JSON文件存在且非空，缺失则重新dispatch |
| 子agent生成临时.py脚本 | shutil.rmtree(_tmp_)时一并清理 |
| 批量导入时全量检查产生历史已知问题告警 | 只检查本次新增（按ID过滤），全量检查留到批次结束 |
| BV号格式错误中断批量下载 | 每个视频try/except包裹，失败跳过继续 |

## 实际导入统计（2026-06-29）

- 16个讲义：400题（ID 711~1110）
- 题型专练01单选：40题（ID 1111~1150）
- 题型专练02多选：35题（ID 1151~1185）
- 题型专练03实验：31题（ID 1186~1216）
- 题型专练04计算：待导入
- 押题卷×4：待导入
- 单个讲义从存档到完成约 5-15 分钟（取决于子agent配对速度）
