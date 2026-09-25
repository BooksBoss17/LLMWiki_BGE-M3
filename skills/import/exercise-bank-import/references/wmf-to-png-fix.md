# WMF 公式引用检查与修复

## 问题

BeMarkdown 转换 DOCX 后，部分 OMML 公式仍以 `.wmf` 格式引用：
```
![图](media/image_0053.wmf)
```

WMF 文件无法被 VLM/RAG/Markdown 渲染器正确处理。题干长度检查通过（wmf引用占字符数），图片断链检查也通过（wmf文件存在），但实际可读内容几乎为零。

## 检测方法

```python
import re
# 扫描题干和详解中的图片引用
for img_ref in re.findall(r'!\[.*?\]\((media/[^)]+)\)', content):
    if img_ref.endswith('.wmf'):
        issues.append(f"{fn}: WMF引用需转PNG {img_ref}")
```

## 修复流程

### Step 1: 批量转 WMF → PNG

```python
from PIL import Image
import os

def convert_wmf_to_png(media_dir):
    """将 media 目录下所有 wmf 文件转为 png"""
    all_files = set(os.listdir(media_dir))
    converted = 0
    for f in sorted(all_files):
        if f.endswith('.wmf'):
            png_name = f.replace('.wmf', '.png')
            if png_name not in all_files:  # 避免重复转换
                try:
                    img = Image.open(os.path.join(media_dir, f))
                    if img.mode != 'RGB':
                        img = img.convert('RGB')
                    img.save(os.path.join(media_dir, png_name))
                    converted += 1
                except Exception as e:
                    print(f"  ❌ {f}: {e}")
    return converted
```

### Step 2: 更新 MD 文件中的引用

```python
def update_wmf_refs(md_path):
    """将 MD 文件中的 .wmf) 替换为 .png)"""
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    if '.wmf' in content:
        new_content = content.replace('.wmf)', '.png)')
        with open(md_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return True
    return False
```

### Step 3: 验证

```python
# 重新扫描，确认 0 个 wmf 引用
for fn in os.listdir(cat_dir):
    if not fn.endswith('.md'): continue
    with open(os.path.join(cat_dir, fn), 'r', encoding='utf-8') as f:
        content = f.read()
    if '.wmf' in content:
        print(f"  ❌ {fn}: 仍有wmf引用")
```

## 统计

- 力与物体的平衡: 332wmf + 64png → 396png
- 动量定理: 416wmf + 72png → 488png
- 图像问题: 447wmf + 106png → 553png
- 牛顿运动定律: 389wmf + 348png → 737png
- 磁场: 415wmf + 91png → 506png
- 万有引力与航天: 247wmf + 33png → 280png
- 综合: 567wmf + 282png → 849png

总计: 1965 个 wmf 转为 png，174 个 MD 文件更新引用。
