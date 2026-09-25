"""Post-process OCR results to fix garbled Chinese subscripts.

OCR engines can misrecognize Chinese characters
in subscripts (e.g. F_{向}, t_{上}). The garbled output patterns are not stable
(same char → different garbled LaTeX), so we use context-based inference: 
match the formula structure to infer the correct Chinese subscript.

Common Chinese subscripts in physics:
  F_{向} (向心力)    F_{合} (合力)     F_{摩} (摩擦力)
  F_{支} (支持力)    F_{重} (重力)     F_{拉} (拉力)
  W_{合} (合功)      E_{k} (动能)      E_{p} (势能)
  t_{上} (上升时间)  t_{下} (下落时间)  H_{上} (上升高度)

Garbled patterns from marker-pdf surya:
  \\odot (圆圈运算符, 通常是末尾多余的"。"句号被误识别)
  \\perp (⊥垂直符号, 通常是"上"被误识别)
  \\mp (∓, 通常是"下"被误识别)
  \\boxplus, \\oplus, \\otimes (通常是中文下标被误识别)
  \\text{①} (脚注标记, 非乱码, 不处理)
"""
import re

# Patterns that indicate a garbled Chinese subscript
GARBLED_INDICATORS = [
    # marker-pdf surya patterns
    '\\boxplus', '\\oplus', '\\otimes',
]

# Surya-specific garbled patterns that need context check
# (because \perp and \mp can be real math symbols)
SURYA_GARBLED = ['\\perp', '\\mp', '\\odot']


def is_marker_engine(engine):
    normalized = str(engine or "").lower()
    return any(value in normalized for value in ("marker", "surya"))


def is_garbled_subscript(sub_content, engine=None):
    """Check if a subscript content looks like garbled Chinese."""
    if any(p in sub_content for p in GARBLED_INDICATORS):
        return True
    # Surya patterns: garbled only when the ENTIRE subscript is just the symbol
    # (e.g. _{\perp} or _{\mp} — real \perp between variables like v \perp B is not in a subscript)
    for pat in SURYA_GARBLED:
        if is_marker_engine(engine) and pat in sub_content:
            # Check if the subscript is ONLY the garbled symbol (possibly with \! spacing)
            cleaned = re.sub(r'\\!+', '', sub_content).strip()
            if cleaned == pat or cleaned == pat.replace('\\', ''):
                return True
    return False


def is_trailing_garbled(latex, sub_content, engine=None):
    """Check if \odot appears as a trailing subscript at end of formula (likely 。句号误识)."""
    # Pattern: unit_{\odot} at end of formula (e.g. "0.10 \text{ s}_{\odot}")
    # Real subscripts on units are rare; \odot as subscript is almost always garbled
    if is_marker_engine(engine) and '\\odot' in sub_content:
        cleaned = re.sub(r'\\!+', '', sub_content).strip()
        if cleaned == '\\odot':
            return True
    return False


# Context rules: (regex matching full formula, replacement LaTeX subscript, description)
# Ordered by specificity (most specific first)
CONTEXT_RULES = [
    # F_{向} = m*v^2/r  →  向心力公式
    (r'F_\{[^}]*\}.*=.*m.*\\frac.*v.*\^{?2}?.*\{.*r.*\}', '\\text{向}', '向心力'),
    # F^2 = F_{?}^2 + (mg)^2  →  力的合成, 勾股定理
    (r'F\^{?2}??.*=.*F_\{[^}]*\}.*\^.*\+.*m\s*g.*\^', '\\text{向}', '勾股定力合成'),
    # F_{合} = ma  →  牛顿第二定律 (合力 = 质量×加速度)
    (r'F_\{[^}]*\}\s*=\s*m\s*a\b', '\\text{合}', '牛顿第二定律'),
    # F_{摩} = μmg or f = μmg  →  摩擦力
    (r'[Ff]_\{[^}]*\}.*=.*\\mu.*m\s*g', '\\text{摩}', '摩擦力'),
    # F_{支} = mg  →  支持力 (mg without μ)
    (r'F_\{[^}]*\}.*=.*m\s*g\b(?!.*\\mu)', '\\text{支}', '支持力'),
    # W_{合} = ΔE  →  合力做功
    (r'W_\{[^}]*\}.*=.*\\Delta', '\\text{合}', '合功'),
    # F_{重} = mg  →  重力 (context: just mg, often with 下落/重力)
    (r'F_\{[^}]*\}\s*=\s*m\s*g\b', '\\text{重}', '重力'),
    
    # --- 竖直上抛运动 (marker surya: \perp→上, \mp→下) ---
    # t = t_{?} + t_{?}  →  上升时间 + 下落时间
    (r't\s*=\s*t_\{[^}]*\}\s*\+\s*t_\{[^}]*\}', '\\text{上}', '上升+下落时间', '\\text{下}'),
    # v_t = v_0 - a*t_{?}  →  上升过程 (减速)
    (r'v_0\s*-\s*a\s*t_\{[^}]*\}', '\\text{上}', '上升时间'),
    # v_t^2 - v_0^2 = -2a*H_{?}  →  上升高度
    (r'-2\s*a\s*H_\{[^}]*\}', '\\text{上}', '上升高度'),
    # H_{?} = v_0^2 / 2a  →  上升高度
    (r'H_\{[^}]*\}\s*=\s*\\frac\{.*v_0.*\^{?2}?.*\}\{.*2\s*a.*\}', '\\text{上}', '上升高度'),
    # t_{?} = v_0 / a  →  上升时间
    (r't_\{[^}]*\}\s*=\s*\\frac\{.*v_0.*\}\{.*a.*\}', '\\text{上}', '上升时间'),
    # H = H_{?} + h  →  总高度 = 上升高度 + 下落高度
    (r'H\s*=\s*H_\{[^}]*\}\s*\+\s*h', '\\text{上}', '总高度=上升+下落'),
    
    # --- N_{T} = G (法向力, marker surya: \perp→N误识) ---
    # \perp\!\!\!\perp T = G or similar → N_T = G (正压力)
    (r'\\perp.*T\s*=\s*G', '\\text{N}', '正压力N'),
]


def find_subscripts(s):
    """Find all _{...} subscripts, handling nested braces. Returns (start, end, content) tuples."""
    results = []
    i = 0
    while i < len(s):
        idx = s.find('_{', i)
        if idx == -1:
            break
        depth = 1
        j = idx + 2
        while j < len(s) and depth > 0:
            if s[j] == '{':
                depth += 1
            elif s[j] == '}':
                depth -= 1
            j += 1
        if depth == 0:
            sub_content = s[idx+2:j-1]
            results.append((idx, j, sub_content))
        i = j
    return results


def fix_garbled_subscripts(latex, engine=None):
    """Fix garbled Chinese subscripts using context-based inference.
    
    Args:
        latex: LaTeX string from an OCR candidate
    
    Returns:
        (fixed_latex, was_fixed, replacement) where:
        - fixed_latex: the (possibly) fixed LaTeX string
        - was_fixed: True if a garbled subscript was found and fixed
        - replacement: the replacement subscript (e.g. '\\text{向}'), or None
    
    Note: For rules with two replacements (e.g. t_上 + t_下), the second
    replacement is applied to the second matching subscript.
    Replaces from RIGHT to LEFT to preserve string positions.
    """
    subs = find_subscripts(latex)
    
    for start, end, sub_content in subs:
        # Skip \text{①} footnote markers (not garbled)
        if '\\text{①}' in sub_content or re.match(r'\\text\{[①②③④⑤⑥⑦⑧⑨⑩]\}', sub_content):
            continue
        
        # Check for trailing \odot (likely 。句号误识, just remove it)
        if is_trailing_garbled(latex, sub_content, engine=engine):
            fixed = latex[:start] + latex[end:]  # Remove the subscript entirely
            return fixed, True, '(removed trailing \\odot)'
        
        if is_garbled_subscript(sub_content, engine=engine):
            for rule in CONTEXT_RULES:
                # Rules with 4 elements have a second replacement (e.g. 上/下 pair)
                if len(rule) == 4:
                    pattern, replacement1, desc, replacement2 = rule
                    if re.search(pattern, latex, re.DOTALL):
                        # Find all garbled subscripts, replace from RIGHT to LEFT
                        garbled_subs = [(s, e, c) for s, e, c in subs if is_garbled_subscript(c, engine=engine)]
                        garbled_subs.reverse()  # right to left
                        
                        fixed = latex
                        replaced = []
                        for idx, (s_start, s_end, s_content) in enumerate(garbled_subs):
                            if idx == len(garbled_subs) - 1:
                                # First in original → replacement1
                                new_sub = f'_{{{replacement1}}}'
                                replaced.insert(0, replacement1)
                            else:
                                new_sub = f'_{{{replacement2}}}'
                                replaced.insert(0, replacement2)
                            fixed = fixed[:s_start] + new_sub + fixed[s_end:]
                        if replaced:
                            return fixed, True, '/'.join(replaced)
                else:
                    pattern, replacement, desc = rule
                    if re.search(pattern, latex, re.DOTALL):
                        new_sub = f'_{{{replacement}}}'
                        fixed = latex[:start] + new_sub + latex[end:]
                        return fixed, True, replacement
    
    return latex, False, None


def scan_for_garbled(md_content, engine=None):
    """Scan a full Markdown document for garbled subscripts.
    
    Returns a list of (line_number, formula, suggestion) tuples for AI review.
    This is for post-conversion quality check — AI should review all flagged items.
    """
    lines = md_content.split('\n')
    issues = []
    
    # Patterns to search for in the whole document
    all_garbled = GARBLED_INDICATORS + SURYA_GARBLED + ['\\boxplus', '\\oplus', '\\otimes']
    
    for i, line in enumerate(lines):
        # Extract all $...$ formulas from the line
        formulas = re.findall(r'\$[^$]+\$', line)
        for formula in formulas:
            subs = find_subscripts(formula)
            for start, end, sub_content in subs:
                # Skip footnote markers
                if re.match(r'\\text\{[①②③④⑤⑥⑦⑧⑨⑩]\}', sub_content):
                    continue
                
                if is_garbled_subscript(sub_content, engine=engine) or is_trailing_garbled(formula, sub_content, engine=engine):
                    # Try to fix
                    fixed, was_fixed, replacement = fix_garbled_subscripts(formula, engine=engine)
                    if was_fixed:
                        issues.append((i+1, formula, fixed, replacement))
                    else:
                        # Flagged but no rule matched — needs AI manual review
                        issues.append((i+1, formula, None, '需AI人工确认'))
    
    return issues


if __name__ == '__main__':
    import sys
    
    # Test cases
    tests = [
        # marker surya patterns
        (r't_{\perp} = \frac{v_0}{a} = 0.4\text{ s}', '上'),  # 上升时间
        (r'H_{\perp} = \frac{v_0^2}{2a} = 0.8\text{ m}', '上'),  # 上升高度
        (r't = t_{\perp} + t_{\mp} = 1.8\text{ s}', '上/下'),  # 上升+下落
        (r'v_t = v_0 - at_{\perp}', '上'),  # 上升过程
        (r'0.10\text{ s}_{\odot}', '(removed)'),  # trailing odot
        (r'v \perp B', None),  # real perpendicular, not in subscript
        (r's^{\text{①}}', None),  # footnote marker, not garbled
    ]
    
    print("Test results:")
    for latex, expected in tests:
        engine = 'marker'
        fixed, was_fixed, replacement = fix_garbled_subscripts(latex, engine=engine)
        if expected:
            if expected == '(removed)':
                status = "✅" if was_fixed and '\\odot' not in fixed else "❌"
            elif expected == '上/下':
                status = "✅" if was_fixed and '\\text{上}' in fixed and '\\text{下}' in fixed else "❌"
            else:
                status = "✅" if was_fixed and '\\text{' + expected in fixed else "❌"
        else:
            status = "✅" if not was_fixed else "❌ false positive"
        print(f"  {status} ${latex[:60]}")
        if was_fixed:
            print(f"       → ${fixed[:60]}")
            print(f"       replacement: {replacement}")
