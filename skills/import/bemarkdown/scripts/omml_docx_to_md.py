"""
omml_docx_to_md.py — DOCX (含 OMML 公式) → Markdown 完整转换

策略：
1. 直接解析 document.xml，遍历段落结构
2. 遇到 OMML 公式时内联插入 LaTeX
3. 遇到图片时提取并引用
4. 同时处理文本格式（粗体/斜体）

这是 mammoth 无法处理 OMML 时的替代方案。
"""
import argparse
import hashlib
import json
import os
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
V = "urn:schemas-microsoft-com:vml"

# Reuse the OMML→LaTeX converter
import sys
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)
from math_conversion_contract import HashBoundCompatibilityOverrides, MathConversionResult, conversion_issue
from omml_to_latex import convert_omml, omml_to_latex
from eq_field_to_latex import EQ_STANDARD, convert_eq_field, parse_eq_field


# Relationship IDs are ASCII tokens.  Restricting the capture prevents the
# old ``\w+`` expression from swallowing subsequent placeholders because
# Chinese characters and the ``__IMG__`` marker itself are Unicode ``\w``.
IMAGE_PLACEHOLDER_RE = re.compile(r"__IMG__([A-Za-z0-9]+)__")


class DocumentMathConversionBlocked(RuntimeError):
    def __init__(self, report_path: Path, summary: dict[str, int]):
        self.report_path = report_path
        self.summary = summary
        super().__init__(
            "structured Word math conversion blocked: "
            f"needs_review={summary.get('needs_review', 0)}, "
            f"unsupported={summary.get('unsupported', 0)}; report={report_path}"
        )


def _atomic_write_text(path: Path, content: str) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


@dataclass
class FormulaConversionContext:
    source_sha256: str
    overrides: HashBoundCompatibilityOverrides
    records: list[dict[str, Any]] = field(default_factory=list)
    indices: Counter = field(default_factory=Counter)

    def _record(self, result: MathConversionResult, source_payload: bytes | str) -> str:
        kind = result.source_kind
        self.indices[kind] += 1
        index = int(self.indices[kind])
        payload_bytes = source_payload if isinstance(source_payload, bytes) else source_payload.encode("utf-8")
        record = {
            "source_kind": kind,
            "formula_index": index,
            "source_sha256": self.source_sha256,
            "formula_source_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            **result.to_dict(),
        }
        self.records.append(record)
        return result.latex or ""

    def convert_omml(self, elem: etree._Element) -> str:
        source = etree.tostring(elem, encoding="utf-8")
        return self._record(convert_omml(elem), source)

    def convert_eq(self, instruction: str) -> str:
        next_index = int(self.indices["eq"]) + 1
        rule = self.overrides.rule_for(next_index, instruction)
        return self._record(convert_eq_field(instruction, compatibility_rule=rule), instruction)

    def record_incomplete_eq(self, instruction: str) -> None:
        result = MathConversionResult(
            status="unsupported",
            source_kind="eq",
            latex=None,
            standard_reference=EQ_STANDARD,
            unsupported_constructs=[
                conversion_issue(
                    "eq_incomplete_field",
                    "Word field begin/separate/end markers are incomplete or unbalanced.",
                    standard_reference=EQ_STANDARD,
                )
            ],
        )
        self._record(result, instruction)

    def report(self, source_name: str) -> dict[str, Any]:
        summary = Counter(record["status"] for record in self.records)
        summary.update({"total": len(self.records)})
        blocked = summary.get("needs_review", 0) + summary.get("unsupported", 0)
        return {
            "schema_version": 1,
            "status": "blocked" if blocked else "converted",
            "source_name": source_name,
            "source_sha256": self.source_sha256,
            "summary": {
                "total": int(summary.get("total", 0)),
                "converted": int(summary.get("converted", 0)),
                "needs_review": int(summary.get("needs_review", 0)),
                "unsupported": int(summary.get("unsupported", 0)),
            },
            "compatibility_overrides": self.overrides.to_dict(),
            "formulas": self.records,
        }


def _load_compatibility_overrides(
    value: str | os.PathLike[str] | dict[str, Any] | HashBoundCompatibilityOverrides | None,
    *,
    source_sha256: str,
) -> HashBoundCompatibilityOverrides:
    if value is None:
        return HashBoundCompatibilityOverrides.empty(source_sha256)
    if isinstance(value, HashBoundCompatibilityOverrides):
        if value.source_sha256 != source_sha256:
            raise ValueError("compatibility overrides are bound to a different DOCX")
        return value
    if isinstance(value, dict):
        payload = value
    else:
        payload = json.loads(Path(value).read_text(encoding="utf-8"))
    return HashBoundCompatibilityOverrides.from_payload(payload, source_sha256=source_sha256)


def replace_image_placeholders(text, rid_to_file):
    """Replace every image placeholder without consuming later markers."""
    def replace_img_ref(match):
        fname = rid_to_file.get(match.group(1), "")
        return f"![图](media/{fname})" if fname else ""

    return IMAGE_PLACEHOLDER_RE.sub(replace_img_ref, text)


def get_text_from_run(run, formula_context=None):
    """Extract text from a w:r element, preserving formatting."""
    texts = []
    is_bold = False
    is_italic = False
    
    rPr = run.find(f'{{{W}}}rPr')
    if rPr is not None:
        b = rPr.find(f'{{{W}}}b')
        if b is not None and b.get(f'{{{W}}}val', 'true') != 'false':
            is_bold = True
        i = rPr.find(f'{{{W}}}i')
        if i is not None and i.get(f'{{{W}}}val', 'true') != 'false':
            is_italic = True
    
    for child in run:
        tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        
        if tag == 't':
            text = child.text or ''
            # Do not wrap punctuation-only runs. Word commonly stores a
            # minus sign or decimal point as a separate italic run; emitting
            # ``*-*0*.*8`` corrupts the Markdown and the numeric value.
            has_semantic_text = any(ch.isalnum() or "\u3400" <= ch <= "\u9fff" for ch in text)
            if not has_semantic_text:
                texts.append(text)
            elif is_bold and is_italic:
                texts.append(f'***{text}***')
            elif is_bold:
                texts.append(f'**{text}**')
            elif is_italic:
                texts.append(f'*{text}*')
            else:
                texts.append(text)
        
        elif tag == 'tab':
            texts.append('\t')
        
        elif tag == 'br':
            texts.append('  \n')
        
        # Check for OMML math inside run (inline math)
        elif tag == 'oMath':
            latex = formula_context.convert_omml(child) if formula_context else omml_to_latex(child)
            if latex:
                texts.append(f' ${latex}$ ')
        
        # Check for drawing/image inside run (DrawingML: w:drawing > a:blip)
        elif tag == 'drawing':
            blip = child.find(f'.//{{{A}}}blip')
            if blip is not None:
                embed = blip.get(f'{{{R}}}embed', '')
                if embed:
                    texts.append(f'__IMG__{embed}__')
        
        # Check for VML imagedata inside w:object (old-style OLE images)
        elif tag == 'object':
            # Find v:imagedata inside v:shape
            imagedata = child.find(f'.//{{{V}}}imagedata')
            if imagedata is not None:
                rid = imagedata.get(f'{{{R}}}id', '')
                if rid:
                    texts.append(f'__IMG__{rid}__')
    
    return ''.join(texts)


def convert_paragraph(para, rels, formula_context=None):
    """Convert a w:p element to markdown text, handling OMML, images, and EQ field codes."""
    parts = []
    field_stack = []
    
    for child in para:
        tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
        
        if tag == 'r':
            # Check if this run contains fldChar or instrText (EQ field code)
            has_fld = child.find(f'{{{W}}}fldChar') is not None
            has_instr = child.find(f'{{{W}}}instrText') is not None
            
            if has_fld:
                fld = child.find(f'{{{W}}}fldChar')
                ftype = fld.get(f'{{{W}}}fldCharType', '')
                if ftype == 'begin':
                    field_stack.append({"instruction": [], "separated": False})
                elif ftype == 'separate' and field_stack:
                    field_stack[-1]["separated"] = True
                elif ftype == 'end':
                    if not field_stack:
                        continue
                    field = field_stack.pop()
                    if field["instruction"]:
                        instr = ''.join(field["instruction"]).strip()
                        if re.match(r"(?i)^eq\b", instr):
                            latex = formula_context.convert_eq(instr) if formula_context else parse_eq_field(instr)
                            if latex:
                                parts.append(f' ${latex}$ ')
                continue
            
            if has_instr and field_stack and not field_stack[-1]["separated"]:
                instr_elem = child.find(f'{{{W}}}instrText')
                if instr_elem is not None and instr_elem.text:
                    field_stack[-1]["instruction"].append(instr_elem.text)
                continue
            
            if field_stack:
                # Skip display result runs inside field (usually empty or placeholder)
                continue
            
            # Regular run (may contain text, tabs, breaks, inline oMath)
            text = get_text_from_run(child, formula_context)
            parts.append(text)
        
        elif tag == 'oMath':
            # Inline OMML formula
            latex = formula_context.convert_omml(child) if formula_context else omml_to_latex(child)
            if latex:
                parts.append(f' ${latex}$ ')
        
        elif tag == 'oMathPara':
            # Block OMML formula (display math)
            om = child.find(f'{{{M}}}oMath')
            if om is not None:
                latex = formula_context.convert_omml(om) if formula_context else omml_to_latex(om)
                if latex:
                    parts.append(f'\n$${latex}$$\n')
        
        elif tag == 'hyperlink':
            # Hyperlink - just get the text
            for r in child.findall(f'{{{W}}}r'):
                parts.append(get_text_from_run(r, formula_context))
        
        elif tag == 'object':
            # VML image inside w:object (OLE embedded image)
            imagedata = child.find(f'.//{{{V}}}imagedata')
            if imagedata is not None:
                rid = imagedata.get(f'{{{R}}}id', '')
                if rid:
                    parts.append(f'__IMG__{rid}__')
        
        elif tag == 'pPr':
            # Paragraph properties - check style
            pass  # Skip for now

    if field_stack:
        for field in field_stack:
            instruction = ''.join(field["instruction"]).strip() or "<missing instruction>"
            if formula_context and re.match(r"(?i)^eq\b", instruction):
                formula_context.record_incomplete_eq(instruction)
            elif not formula_context and re.match(r"(?i)^eq\b", instruction):
                raise ValueError(f"incomplete Word field: {instruction}")
    
    return ''.join(parts)


def convert_docx_to_md(docx_path, output_dir, *, compatibility_overrides=None):
    """Convert DOCX to Markdown and block unresolved structured math."""
    source_path = Path(docx_path)
    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
    overrides = _load_compatibility_overrides(
        compatibility_overrides,
        source_sha256=source_sha256,
    )
    formula_context = FormulaConversionContext(source_sha256, overrides)
    os.makedirs(output_dir, exist_ok=True)
    media_dir = os.path.join(output_dir, "media")
    os.makedirs(media_dir, exist_ok=True)
    
    with zipfile.ZipFile(docx_path, 'r') as z:
        # Read document.xml
        with z.open('word/document.xml') as f:
            doc_xml = f.read()
        
        # Read relationships
        rels_xml = z.read('word/_rels/document.xml.rels')
        rels_tree = etree.fromstring(rels_xml)
        rel_map = {}
        for rel in rels_tree:
            rid = rel.get('Id', '')
            target = rel.get('Target', '')
            rtype = rel.get('Type', '')
            rel_map[rid] = {'target': target, 'type': rtype}
        
        # Extract all media files
        img_map = {}  # original filename → saved filename
        img_counter = [0]
        for name in z.namelist():
            if name.startswith('word/media/'):
                ext = os.path.splitext(name)[1] or '.png'
                img_counter[0] += 1
                # Normalize extension (e.g. .x-wmf → .wmf)
                if 'wmf' in ext.lower():
                    ext = '.wmf'
                elif 'emf' in ext.lower():
                    ext = '.emf'
                fname = f"image_{img_counter[0]:04d}{ext}"
                with z.open(name) as f:
                    data = f.read()
                with open(os.path.join(media_dir, fname), 'wb') as f:
                    f.write(data)
                # Map the original filename to our filename
                orig_name = name.replace('word/media/', '')
                img_map[orig_name] = fname
        
        # Also need to map rId → media file
        # Read rels to get rId → target mapping
        rid_to_file = {}
        for rid, info in rel_map.items():
            if 'image' in info['type'] or 'image' in info['target']:
                target = info['target']
                # Target might be "media/image1.png" or "../media/image1.png"
                target = target.replace('../', '').replace('media/', '')
                if target in img_map:
                    rid_to_file[rid] = img_map[target]
    
    # Parse document.xml
    tree = etree.fromstring(doc_xml)
    body = tree.find(f'{{{W}}}body')
    
    md_lines = []
    image_count = 0
    
    for elem in body:
        tag = elem.tag.split('}')[-1] if '}' in elem.tag else elem.tag
        
        if tag == 'p':
            # Paragraph
            text = convert_paragraph(elem, rel_map, formula_context)
            # Replace image placeholders.  Keep the count in this loop while
            # using the non-greedy/ASCII-safe helper above.
            placeholders = IMAGE_PLACEHOLDER_RE.findall(text)
            text = replace_image_placeholders(text, rid_to_file)
            image_count += sum(1 for rid in placeholders if rid_to_file.get(rid))
            
            if text.strip():
                md_lines.append(text)
        
        elif tag == 'tbl':
            # Table
            md_lines.append('')  # blank line before table
            for row in elem.findall(f'{{{W}}}tr'):
                cells = []
                for cell in row.findall(f'{{{W}}}tc'):
                    cell_text = ''
                    for p in cell.findall(f'{{{W}}}p'):
                        cell_text += convert_paragraph(p, rel_map, formula_context)
                    cells.append(cell_text.strip())
                md_lines.append('| ' + ' | '.join(cells) + ' |')
            # Add separator after first row
            if md_lines and md_lines[-1].startswith('|'):
                num_cols = md_lines[-1].count('|') - 1
                md_lines.insert(-1, '| ' + ' | '.join(['---'] * num_cols) + ' |')
            md_lines.append('')
    
    # Join and clean up
    md = '\n\n'.join(md_lines)
    md = md.replace('\xa0', ' ')
    md = re.sub(r'\n{3,}', '\n\n', md)
    
    # Fix mammoth-style escapes (just in case)
    md = md.replace('\\.', '.')
    
    output_root = Path(output_dir)
    report = formula_context.report(source_path.name)
    report_path = output_root / "formula_conversion_report.json"
    if report["status"] != "converted":
        _atomic_write_json(report_path, report)
        raise DocumentMathConversionBlocked(report_path, report["summary"])

    md_path = output_root / f"{source_path.stem}.md"
    _atomic_write_text(md_path, md)
    _atomic_write_json(report_path, report)
    return str(md_path), len(formula_context.records), image_count


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Convert DOCX with strict OMML/EQ math gates")
    parser.add_argument("docx_path")
    parser.add_argument("output_dir", nargs="?", default="./output")
    parser.add_argument("--compat-overrides", help="Hash-bound reviewed EQ compatibility overrides JSON")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        md_path, fcount, icount = convert_docx_to_md(
            args.docx_path,
            args.output_dir,
            compatibility_overrides=args.compat_overrides,
        )
        payload = {"status": "converted", "markdown": md_path, "formula_count": fcount, "image_count": icount}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(f"OK {md_path}")
            print(f"   formulas: {fcount}, images: {icount}")
    except DocumentMathConversionBlocked as exc:
        payload = {"status": "blocked", "report": str(exc.report_path), "summary": exc.summary}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False))
        else:
            print(str(exc))
        raise SystemExit(2)
