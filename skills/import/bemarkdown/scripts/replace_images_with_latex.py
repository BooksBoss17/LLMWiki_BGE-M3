#!/usr/bin/env python3
"""Replace image references in mammoth-generated Markdown with LaTeX text from alt attributes.

Strategy:
1. Images with alt text containing LaTeX → replace ![](alt) with $alt$ (inline) or $$alt$$ (block)
2. Small blank images (<1KB) → remove the reference entirely
3. Large diagram images (>5KB, no alt) → keep ![](media/xxx.png) as-is

⚠️ Pitfall: transparency (alpha channel) is NOT a reliable indicator of formula vs diagram.
   Some transparent images are physical apparatus diagrams cropped from Word.
   VLM-verify borderline images before batch processing.

Usage:
    python replace_images_with_latex.py input.md output.md
    python replace_images_with_latex.py input.md output.md --media-dir /path/to/media
"""
import re
import os
import sys
import argparse

BLANK_SIZE_THRESHOLD = 1000   # < 1KB = blank placeholder
DIAGRAM_SIZE_THRESHOLD = 5000 # > 5KB = diagram, keep as image


def is_inline_latex(alt_text):
    """True = inline $...$, False = block $$...$$.
    
    Block indicators: fractions, roots, sums, integrals, large expressions.
    """
    block_indicators = [
        r'\dfrac', r'\frac', r'\sqrt', r'\sum', r'\int',
        r'\lim', r'\over', r'\left', r'\begin',
    ]
    for ind in block_indicators:
        if ind in alt_text:
            return False
    return len(alt_text) <= 30


def replace_images_with_latex(md_path, output_path, media_dir=None):
    """Replace image refs in mammoth MD with LaTeX from alt text.
    
    Args:
        md_path: Path to input mammoth-generated Markdown file.
        output_path: Path to write the processed Markdown.
        media_dir: Directory containing extracted images. Defaults to
                   <md_dir>/media/.
    
    Returns:
        dict: Statistics {inline, block, removed, kept, total}
    """
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    if media_dir is None:
        media_dir = os.path.join(os.path.dirname(md_path), 'media')
    
    pattern = r'!\[([^\]]*)\]\((media/[^)]+)\)'
    stats = {'inline': 0, 'block': 0, 'removed': 0, 'kept': 0, 'total': 0}

    def replace_img(match):
        alt = match.group(1).strip()
        path = match.group(2)  # e.g. "media/image_0004.png"
        img_name = path.replace('media/', '')
        full_path = os.path.join(media_dir, img_name)
        size = os.path.getsize(full_path) if os.path.exists(full_path) else 0
        stats['total'] += 1

        # Case 1: Small blank image → remove
        if size < BLANK_SIZE_THRESHOLD and (not alt or alt == 'image'):
            stats['removed'] += 1
            return ''

        # Case 2: Has alt text with LaTeX → replace with $...$ or $$...$$
        if alt and alt != 'image':
            latex = alt.strip()
            if is_inline_latex(latex):
                stats['inline'] += 1
                return f'${latex}$'
            else:
                stats['block'] += 1
                return f'$${latex}$$'

        # Case 3: Large diagram without alt → keep as image reference
        stats['kept'] += 1
        return f'![图]({path})'

    new_content = re.sub(pattern, replace_img, content)
    # Clean up: collapse 3+ consecutive newlines to 2
    new_content = re.sub(r'\n{3,}', '\n\n', new_content)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(new_content)

    return stats


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Replace image refs with LaTeX from alt text')
    parser.add_argument('input_md', help='Input mammoth Markdown file')
    parser.add_argument('output_md', help='Output Markdown file')
    parser.add_argument('--media-dir', help='Media directory (default: <input_dir>/media)')
    args = parser.parse_args()

    stats = replace_images_with_latex(args.input_md, args.output_md, args.media_dir)
    print(f"Total images: {stats['total']}")
    print(f"  Inline formulas ($...$):  {stats['inline']}")
    print(f"  Block formulas ($$...$$): {stats['block']}")
    print(f"  Removed (blank):          {stats['removed']}")
    print(f"  Kept as images:           {stats['kept']}")
