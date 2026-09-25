# Per-Section Question Extraction Pattern

When a test paper has independent numbering per section (e.g., Section 1 has Q1-3, Section 2 restarts at Q1), flat `^\d+\.` matching across the whole document causes mismatches. Use this composite-key extraction approach.

## Algorithm

1. **Find section boundaries** — scan for `^[一二三四五六]` headers, record `(line_pos, section_title, question_type)`.
2. **Extract questions per section** — within each section's line range, match question numbers independently. Use `(section_index, q_num)` as composite key.
3. **Match between exam and answer** — match by `(section_index, q_num)`, NOT by `q_num` alone.

## Image-Prefix Question Numbers

Some questions start with an image reference before the number:
```
![图](media/image_0006.png)1．扫地机器人...
```

Use this regex to handle both cases:
```python
re.match(r'^(?:!\[.*?\]\([^)]+\)\s*)?(\d+)[\.．]\s*', line)
```

## Multi-Choice Detection

Within a section, check for `(多选)` marker to override section-level type:
```python
if '(多选)' in q_text:
    qtype = 'MA'  # override
```

## Answer Extraction

Answer letter often appears right after the number in the answer MD:
```
1．D 解析：...
2．BC 解析：...
```

Pattern: `re.match(r'^\d+[\.．]\s*([A-Z]+)', a_text)`

For fill-in/calculation: search `故答案为[：:]\s*(.+)` or `故选\s*([A-Z]+)`.

## Full Code Example

```python
def extract_by_section(lines, sections):
    all_q = []
    for si, (sec_pos, sec_title, sec_type) in enumerate(sections):
        sec_end = sections[si+1][0] if si+1 < len(sections) else len(lines)
        sec_lines = lines[sec_pos:sec_end]
        
        q_in_sec = []
        for j, line in enumerate(sec_lines):
            s = line.strip()
            m = re.match(r'^(?:!\[.*?\]\([^)]+\)\s*)?(\d+)[\.．]\s*', s)
            if m:
                q_in_sec.append((j, int(m.group(1))))
        
        for idx, (q_pos, q_num) in enumerate(q_in_sec):
            q_end = q_in_sec[idx+1][0] if idx+1 < len(q_in_sec) else len(sec_lines)
            q_text = '\n'.join(sec_lines[q_pos:q_end]).strip()
            qtype = 'MA' if '(多选)' in q_text else sec_type
            all_q.append((si, q_num, qtype, q_text))
    
    return all_q

# Match exam ↔ answer by (section_index, q_num)
matched = []
for e_si, e_qnum, e_qt, e_text in exam_qs:
    for a_si, a_qnum, a_qt, a_text in ans_qs:
        if e_si == a_si and e_qnum == a_qnum:
            matched.append((e_si, e_qnum, e_qt, e_text, a_text))
            break
```

## `convert_one_docx` None Score

When DOCX has OMML but no WMF, `convert_one_docx` returns `wmf_score: None`.
Safe formatting: `result.get('wmf_score') or 0`
