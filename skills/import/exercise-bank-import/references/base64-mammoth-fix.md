# mammoth base64 data URI 陷阱

## 问题

mammoth `convert_to_markdown()` 会把 DOCX 中的图片内联为 base64 data URI：
```
![latex公式](data:image/png;base64,iVBORw0KGgoAAAANSUhEU...)
```

而 `replace_images_with_latex.py` 只处理文件路径格式（`![alt](media/image.png)`），不处理 base64 data URI。

## 判断方法

转换后检查 MD 中是否有 `data:image/png;base64` 字符串。有则需要 base64 提取；无则 `replace_images_with_latex.py` 可直接使用。

## 修复代码

```python
import base64, hashlib, re

# Step 1: base64 → PNG 文件
def replace_base64(match):
    alt = match.group(1)
    b64 = match.group(2)
    h = hashlib.md5(b64[:100].encode()).hexdigest()[:8]
    img_name = f"img_{h}.png"
    img_path = os.path.join(out_dir, "media", img_name)
    if not os.path.exists(img_path):
        with open(img_path, 'wb') as f:
            f.write(base64.b64decode(b64))
    return f"![{alt}](media/{img_name})"

content = re.sub(r'!\[([^\]]*)\]\(data:image/png;base64,([A-Za-z0-9+/=]+)\)', replace_base64, content)

# Step 2: 从 alt 提取 LaTeX（只处理非空、非"图"的 alt）
def replace_latex(match):
    alt = match.group(1)
    if alt and alt.strip() and alt.strip() not in ['', 'image', '图']:
        latex = alt.strip()
        if re.search(r'[a-zA-Z0-9\\{}^_\[\]]', latex):
            return f"${latex}$"
    return match.group(0)

content = re.sub(r'!\[([^\]]*)\]\((media/img_[^)]+)\)', replace_latex, content)
```

## 验证

- 转换后 MD 中不再有 `data:image/png;base64` 字符串
- `$` 符号数量合理（每个公式对应一对 `$`）
- PNG 文件已保存到 `media/` 目录
