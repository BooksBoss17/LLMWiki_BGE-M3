"""Post-process: fix unambiguous plain-text superscripts in MD files.

Word documents sometimes have manually-typed superscripts as plain text (not OMML,
not EQ field codes, not images). These appear as e.g. "ω2" (should be ω²),
"103" (should be 10³), "m3" (should be m³).

This script only fixes patterns that are UNAMBIGUOUSLY superscripts in physics:
1. ×10+digits → ×$10^{digits}$  (scientific notation, the × prefix is unambiguous)
2. kg/m3 → kg/m³  (unit, always superscript)
3. ω2 → $\omega^2$  (angular velocity squared, always superscript in physics)
4. *R*3 after ρ· or π → $R^3$  (volume formula context)

Does NOT fix ambiguous cases like r2 (could be r² or r₂), v2 (v² or v₂),
m1 (m₁ subscript), T0 (T₀ subscript) — these need context judgment beyond regex.
"""
import re


def fix_plain_text_superscripts(text):
    """Fix unambiguous plain-text superscripts in MD content.
    
    Returns the text with superscripts converted to LaTeX $...$ notation.
    """
    # 1. Scientific notation: ×10+digits → ×$10^{digits}$
    # The × prefix makes this unambiguous (e.g. 5.5×103 → 5.5×$10^3$)
    text = re.sub(r'×10(\d+)(?!\d)', r'×$10^{\1}$', text)
    
    # 2. Unit: kg/m3 → kg/m³  (always superscript for cubic meters)
    text = re.sub(r'kg/m3\b', r'kg/m³', text)
    
    # 3. ω² : *ω*2, ω*2, or plain ω2 → $\omega^2$
    # In physics, ω (angular velocity) followed by 2 is always squared, never subscripted
    text = re.sub(r'\*?ω\*?2(?!\d)', r'$\\omega^2$', text)
    
    # 4. R³ in volume formula context: *R*3 followed by 联立 (volume formula indicator)
    text = re.sub(r'\*R\*3(?=，联立)', r'$R^3$', text)
    text = re.sub(r'\*R\*3(?=，可得)', r'$R^3$', text)
    
    return text


if __name__ == '__main__':
    import sys
    import os
    
    if len(sys.argv) < 2:
        print("Usage: python fix_plain_superscripts.py <md_file> [output_file]")
        sys.exit(1)
    
    md_path = sys.argv[1]
    out_path = sys.argv[2] if len(sys.argv) > 2 else md_path.replace('.md', '_fixed.md')
    
    with open(md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Split into $-segments and non-$-segments to avoid modifying LaTeX
    parts = re.split(r'(\$[^$]+\$)', content)
    fixed_parts = []
    fix_count = 0
    
    for part in parts:
        if part.startswith('$') and part.endswith('$'):
            # LaTeX segment — don't touch
            fixed_parts.append(part)
        else:
            # Plain text — apply fixes
            fixed = fix_plain_text_superscripts(part)
            if fixed != part:
                fix_count += 1
            fixed_parts.append(fixed)
    
    result = ''.join(fixed_parts)
    
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(result)
    
    print(f"Fixed {fix_count} text segments with superscript issues")
    print(f"Output: {out_path}")
