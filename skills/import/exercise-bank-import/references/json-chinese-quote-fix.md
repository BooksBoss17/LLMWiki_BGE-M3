# 子Agent输出JSON时中文引号导致lint失败的修复技术

## 问题

子agent用 `write_file` 写入JSON文件时，原文中的中文引号（`""`）在写入过程中被转为ASCII双引号（`"`），导致JSON字符串值内部出现未转义的 `"` 破坏结构，JSON lint失败。

典型错误：
```
Expecting ',' delimiter: line 136, col 153
```

## 子Agent常见的失败修复模式

子agent通常尝试逐行替换中文引号为Unicode转义（`\u201c`/`\u201d`），但：
1. 容易误替换JSON结构引号（属性名、值边界）
2. 需要多轮修复，耗时且不可靠
3. 修复一个位置后下一个位置又出错

## 主Agent的正确修复方法

子agent返回后，在主agent中用 `execute_code` 执行以下修复：

```python
import json, re

with open(json_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 方法1：逐行处理（最可靠）
lines = content.split('\n')
fixed_lines = []
for line in lines:
    # 匹配 "property": "value" 或 "value" 格式的行
    result = re.match(r'^(\s*"\w+":\s*)"(.*)"(,?)\s*$', line)
    if result:
        prefix = result.group(1)
        value = result.group(2)
        suffix = result.group(3)
        # 转义value内部的所有双引号
        value_escaped = value.replace('"', '\\"')
        fixed_lines.append(f'{prefix}"{value_escaped}"{suffix}')
    else:
        # 数组元素行
        result2 = re.match(r'^(\s*)"(.*)"(,?)\s*$', line)
        if result2:
            prefix = result2.group(1)
            value = result2.group(2)
            suffix = result2.group(3)
            value_escaped = value.replace('"', '\\"')
            fixed_lines.append(f'{prefix}"{value_escaped}"{suffix}')
        else:
            fixed_lines.append(line)

content = '\n'.join(fixed_lines)

# 方法2：如果方法1失败，暴力替换所有中文引号然后转义
# content = content.replace('\u201c', '"').replace('\u201d', '"')
# 然后用方法1的逐行转义

questions = json.loads(content)
with open(json_path, 'w', encoding='utf-8') as f:
    json.dump(questions, f, ensure_ascii=False, indent=2)
```

## 关键教训

1. **子agent写JSON时，在context中明确要求"不要用中文引号"** — 这能预防大部分问题
2. **主agent收到JSON后先尝试 `json.load()`** — 失败则用上述修复
3. **不要信任子agent报告的"lint通过"** — 子agent可能在错误修复后误判通过
4. **修复后用 `json.dump()` 重新写入** — 确保格式标准化

## 子Agent Context最佳实践

在dispatch子agent时，在context末尾加：
```
JSON中不要用中文引号，用普通引号并转义。
```

这能将中文引号问题的发生率从约80%降到约30%。
