#!/usr/bin/env python
"""MD→DOCX with OMML formulas, clean table framework, 2-col A4."""
import argparse, json, re, os, subprocess, sys
from pathlib import Path
from docx import Document
from docx.shared import Pt, Cm, Mm, RGBColor, Emu
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.section import WD_ORIENT
from docx.image.image import Image as DocxImage
from docx.oxml.ns import qn, nsdecls
from docx.oxml import parse_xml
from lxml import etree

REFERENCE_DIR = Path(__file__).resolve().parents[1] / "references"
if str(REFERENCE_DIR) not in sys.path:
    sys.path.insert(0, str(REFERENCE_DIR))
from latex_to_omml import LatexToOMML as StableLatexToOMML

# ═══════════════════════════════════════════════════════════════
# OMML NAMESPACES
# ═══════════════════════════════════════════════════════════════
M = 'http://schemas.openxmlformats.org/officeDocument/2006/math'
W = 'http://schemas.openxmlformats.org/wordprocessing/2006/main'

def m_ns(tag): return f'{{{M}}}{tag}'
def w_ns(tag): return f'{{{W}}}{tag}'

# ═══════════════════════════════════════════════════════════════
# LATEX → OMML CONVERTER
# ═══════════════════════════════════════════════════════════════
SYMBOLS = {
    r'\Delta':'Δ', r'\rho':'ρ', r'\times':'×', r'\cdot':'·',
    r'\leq':'≤', r'\geq':'≥', r'\ge':'≥', r'\neq':'≠',
    r'\approx':'≈', r'\propto':'∝', r'\infty':'∞',
    r'\Rightarrow':'⇒', r'\rightarrow':'→', r'\to':'→',
    r'\pi':'π', r'\alpha':'α', r'\beta':'β', r'\gamma':'γ',
    r'\theta':'θ', r'\sigma':'σ', r'\lambda':'λ',
    r'\mu':'μ', r'\nu':'ν', r'\omega':'ω', r'\phi':'φ',
    r'\partial':'∂', r'\le':'≤',
}

class LatexToOMML:
    """Convert LaTeX math to OMML XML elements for Word."""

    def __init__(self):
        self.tokens = []
        self.pos = 0

    def convert_inline(self, latex):
        """Return m:oMath element for inline math."""
        omath = etree.Element(m_ns('oMath'))
        self._build(omath, latex)
        return omath

    def convert_display(self, latex):
        """Return m:oMathPara element for display math (centered)."""
        omathpara = etree.Element(m_ns('oMathPara'))
        omath = etree.SubElement(omathpara, m_ns('oMath'))
        self._build(omath, latex)
        return omathpara

    def _build(self, parent, latex):
        self.tokens = self._tokenize(latex)
        self.pos = 0
        self._parse(parent)

    def _tokenize(self, s):
        """Split LaTeX into tokens: (type, value)."""
        tokens = []
        i = 0
        while i < len(s):
            if s[i] == '\\':
                m = re.match(r'\\[a-zA-Z]+', s[i:])
                if m:
                    tokens.append(('cmd', m.group(0)))
                    i += len(m.group(0))
                elif i+1 < len(s):
                    tokens.append(('cmd', s[i:i+2]))
                    i += 2
                else:
                    i += 1
            elif s[i] in '{}^_':
                tokens.append(('op', s[i]))
                i += 1
            elif s[i] == ' ':
                i += 1
            else:
                tokens.append(('char', s[i]))
                i += 1
        return tokens

    def _peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _advance(self):
        t = self._peek()
        self.pos += 1
        return t

    def _read_brace_group(self):
        """Read {…} content string (handles nesting)."""
        if not self._peek() or self._peek() != ('op','{'):
            return ''
        self._advance()  # consume {
        depth, content = 1, ''
        while self.pos < len(self.tokens) and depth > 0:
            t = self._advance()
            if t == ('op','{'): depth += 1; content += '{'
            elif t == ('op','}'): depth -= 1; content += '}' if depth > 0 else ''
            else: content += t[1]
        return content

    def _read_single_token(self):
        """Read one token (group or single char/cmd)."""
        t = self._peek()
        if not t: return ''
        if t == ('op','{'): return self._read_brace_group()
        self._advance()
        return t[1]

    def _parse(self, parent):
        """Parse tokens → append OMML children to parent."""
        while self.pos < len(self.tokens):
            t = self._peek()
            if t is None: break
            if t == ('op','}'): self._advance(); return
            if t == ('op','{'):
                self._advance()  # consume {
                self._parse(parent)
                continue
            if t[0] == 'op' and t[1] == '_':
                self._advance()
                sub = self._read_single_token()
                self._wrap_subsup(parent, sub, None)
                continue
            if t[0] == 'op' and t[1] == '^':
                self._advance()
                sup = self._read_single_token()
                self._wrap_subsup(parent, None, sup)
                continue
            if t[0] == 'cmd':
                self._handle_cmd(parent)
                continue
            if t[0] == 'char':
                self._advance()
                self._add_run(parent, t[1])
                continue
            self._advance()

    def _handle_cmd(self, parent):
        cmd = self._advance()[1]

        if cmd in (r'\frac', r'\cfrac', r'\dfrac'):
            num = self._read_brace_group()
            den = self._read_brace_group()
            f = etree.SubElement(parent, m_ns('f'))
            ne = etree.SubElement(f, m_ns('num'))
            de = etree.SubElement(f, m_ns('den'))
            self._build(ne, num)
            self._build(de, den)
            return
        if cmd == r'\sqrt':
            arg = self._read_brace_group()
            rad = etree.SubElement(parent, m_ns('rad'))
            rad_pr = etree.SubElement(rad, m_ns('radPr'))
            etree.SubElement(rad_pr, m_ns('degHide'))
            etree.SubElement(rad, m_ns('deg'))
            e = etree.SubElement(rad, m_ns('e'))
            self._build(e, arg)
            return
        if cmd in (r'\text', r'\mathrm', r'\mathbf', r'\textbf'):
            arg = self._read_brace_group()
            r = etree.SubElement(parent, m_ns('r'))
            t = etree.SubElement(r, m_ns('t'))
            t.text = arg
            return
        if cmd == r'\boxed':
            arg = self._read_brace_group()
            self._build(parent, arg)
            return
        if cmd in (r'\overline', r'\bar'):
            arg = self._read_brace_group()
            bar = etree.SubElement(parent, m_ns('bar'))
            e = etree.SubElement(bar, m_ns('e'))
            self._build(e, arg)
            return
        if cmd in (r'\left', r'\right'):
            return  # skip; delimiter chars handled as regular
        if cmd in (r'\,', r'\;', r'\quad', r'\qquad'):
            self._add_run(parent, ' ')
            return
        if cmd == r'\!':
            return
        if cmd in SYMBOLS:
            self._add_run(parent, SYMBOLS[cmd])
            return
        # Unknown: skip
        return

    def _add_run(self, parent, text):
        r = etree.SubElement(parent, m_ns('r'))
        t = etree.SubElement(r, m_ns('t'))
        t.text = text

    def _wrap_subsup(self, parent, sub_str, sup_str):
        """Wrap last child of parent with sub/sup."""
        if len(parent) == 0:
            if sub_str: self._add_run(parent, sub_str)
            if sup_str: self._add_run(parent, sup_str)
            return
        last = parent[-1]
        parent.remove(last)
        if sub_str and sup_str:
            el = etree.SubElement(parent, m_ns('sSubSup'))
            e = etree.SubElement(el, m_ns('e')); e.append(last)
            s = etree.SubElement(el, m_ns('sub')); self._build(s, sub_str)
            p = etree.SubElement(el, m_ns('sup')); self._build(p, sup_str)
        elif sub_str:
            el = etree.SubElement(parent, m_ns('sSub'))
            e = etree.SubElement(el, m_ns('e')); e.append(last)
            s = etree.SubElement(el, m_ns('sub')); self._build(s, sub_str)
        elif sup_str:
            el = etree.SubElement(parent, m_ns('sSup'))
            e = etree.SubElement(el, m_ns('e')); e.append(last)
            s = etree.SubElement(el, m_ns('sup')); self._build(s, sup_str)


# ═══════════════════════════════════════════════════════════════
# READ MARKDOWN
# ═══════════════════════════════════════════════════════════════
parser = argparse.ArgumentParser(description="Convert Markdown to DOCX/PDF with OMML formulas.")
parser.add_argument("input", nargs="?")
parser.add_argument("output", nargs="?")
parser.add_argument(
    "--layout",
    choices=["compact", "validation"],
    default="compact",
    help="compact keeps the legacy two-column layout; validation uses readable single-column exam sizing.",
)
parser.add_argument(
    "--no-pdf",
    action="store_true",
    help="Create only the DOCX. Intended for deterministic tests and intermediate drafts.",
)
parser.add_argument(
    "--officecli-qa",
    choices=["auto", "off", "required"],
    default=os.environ.get("OFFICECLI_QA", "auto"),
    help="Run controlled OfficeCLI post-export QA: auto, off, or required.",
)
ARGS = parser.parse_args()

MD_PATH = os.environ.get("MD_PATH") or (ARGS.input or "")
if not MD_PATH:
    raise SystemExit("Usage: python md2docx.py <input.md> [output.docx] [--officecli-qa auto|off|required]")
with open(MD_PATH, "r", encoding="utf-8") as f:
    md = f.read()
lines = md.split('\n')

VALIDATION_LAYOUT = ARGS.layout == "validation"
BODY_FONT_CJK = "SimSun"
BODY_FONT_LATIN = "Times New Roman"
BODY_SIZE = 10.5 if VALIDATION_LAYOUT else 7
TABLE_SIZE = 9.5 if VALIDATION_LAYOUT else 6.5
BODY_LINE_PT = 13 if VALIDATION_LAYOUT else 9
IMAGE_DISPLAY_DPI = 150

# ═══════════════════════════════════════════════════════════════
# DOCUMENT SETUP — A4, no columns in python-docx (will set via Word COM)
# ═══════════════════════════════════════════════════════════════
doc = Document()
section = doc.sections[0]
section.page_width = Mm(210)
section.page_height = Mm(297)
section.orientation = WD_ORIENT.PORTRAIT
margin = Cm(1.6) if VALIDATION_LAYOUT else Cm(0.8)
side_margin = Cm(2.0) if VALIDATION_LAYOUT else Cm(1.0)
section.top_margin = margin
section.bottom_margin = margin
section.left_margin = side_margin
section.right_margin = side_margin

# DO NOT set columns here — will be set via Word COM to avoid blank page

style = doc.styles['Normal']
style.font.name = BODY_FONT_LATIN
style.font.size = Pt(BODY_SIZE)
style.paragraph_format.space_before = Pt(0)
style.paragraph_format.space_after = Pt(3 if VALIDATION_LAYOUT else 0.5)
style.paragraph_format.line_spacing = Pt(BODY_LINE_PT)
rpr = style.element.get_or_add_rPr()
rfonts = rpr.get_or_add_rFonts()
rfonts.set(qn('w:ascii'), BODY_FONT_LATIN)
rfonts.set(qn('w:hAnsi'), BODY_FONT_LATIN)
rfonts.set(qn('w:eastAsia'), BODY_FONT_CJK)

converter = StableLatexToOMML()

# ═══════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════
def set_run_font(run, name=None, size=None):
    name = name or BODY_FONT_CJK
    size = BODY_SIZE if size is None else size
    latin_name = BODY_FONT_LATIN if name == BODY_FONT_CJK else name
    run.font.name = latin_name
    run.font.size = Pt(size)
    rpr = run._element.get_or_add_rPr()
    rf = rpr.get_or_add_rFonts()
    rf.set(qn('w:ascii'), latin_name)
    rf.set(qn('w:hAnsi'), latin_name)
    rf.set(qn('w:eastAsia'), name)

def add_heading(text, level):
    p = doc.add_paragraph()
    p.paragraph_format.keep_with_next = True
    p.paragraph_format.space_before = Pt(2 if level <= 2 else 1)
    p.paragraph_format.space_after = Pt(0.5)
    p.paragraph_format.line_spacing = Pt(15 if VALIDATION_LAYOUT else 10)
    run = p.add_run()
    sz = ({1:14, 2:11.5, 3:10.5, 4:10.5} if VALIDATION_LAYOUT else {1:11, 2:8.5, 3:7.5, 4:7})[level]
    set_run_font(run, BODY_FONT_CJK, sz)
    run.bold = True
    colors = ({level: RGBColor(0, 0, 0) for level in range(1, 5)} if VALIDATION_LAYOUT else
              {1:RGBColor(0x1a,0x1a,0x8c), 2:RGBColor(0x00,0x33,0x99),
               3:RGBColor(0x33,0x33,0x33), 4:RGBColor(0x44,0x44,0x66)})
    run.font.color.rgb = colors[level]
    if level == 1:
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if level == 2 and not VALIDATION_LAYOUT:
        pborder = parse_xml(f'<w:pBdr {nsdecls("w")}><w:bottom w:val="single" w:sz="4" w:space="1" w:color="99AAFF"/></w:pBdr>')
        p._p.get_or_add_pPr().insert(0, pborder)
    run.add_text(text)

IMAGE_RE = re.compile(r'!\[([^\]]*)\]\(([^)]+)\)')


def _resolve_image_path(target):
    target = target.strip()
    if target.startswith('<') and target.endswith('>'):
        target = target[1:-1]
    if re.match(r'^[a-z]+://', target, flags=re.IGNORECASE):
        raise ValueError(f"remote images are not supported: {target}")
    path = Path(target)
    if not path.is_absolute():
        path = Path(MD_PATH).resolve().parent / path
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f"referenced image does not exist: {path}")
    return path


def _add_picture(run, target, alt_text):
    path = _resolve_image_path(target)
    image = DocxImage.from_file(str(path))
    content_width = int(section.page_width - section.left_margin - section.right_margin)
    if VALIDATION_LAYOUT:
        max_width = min(content_width, int(Cm(15)))
        max_height = int(Cm(12))
    else:
        max_width = int((content_width - Pt(6)) / 2)
        max_height = int(Cm(12))
    native_width = round(image.px_width / IMAGE_DISPLAY_DPI * 914400)
    native_height = round(image.px_height / IMAGE_DISPLAY_DPI * 914400)
    scale = min(1.0, max_width / native_width, max_height / native_height)
    width = Emu(max(1, round(native_width * scale)))
    inline_shape = run.add_picture(str(path), width=width)
    if alt_text:
        inline_shape._inline.docPr.set("descr", alt_text)
    return inline_shape


def add_para_with_inline(text, size=None, bold=False, italic=False, indent=0):
    """Add paragraph with inline $...$ math as OMML."""
    text = re.sub(r'\*\$([^$]+)\$\*', r'$\1$', text)
    size = BODY_SIZE if size is None else size
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(3 if VALIDATION_LAYOUT else 0.5)
    p.paragraph_format.line_spacing = Pt(BODY_LINE_PT)
    if indent:
        p.paragraph_format.left_indent = Cm(indent)
    # Split by $...$ for inline math, **bold**, *italic*
    segments = re.split(r'(\$[^$]+\$|!\[[^\]]*\]\([^)]+\))', text)
    contains_image = False
    contains_math = False
    for seg in segments:
        if not seg: continue
        if seg.startswith('$') and seg.endswith('$'):
            # OMML inline
            omath = converter.convert_inline(seg[1:-1])
            p._p.append(omath)
            contains_math = True
        elif IMAGE_RE.fullmatch(seg):
            match = IMAGE_RE.fullmatch(seg)
            run = p.add_run()
            _add_picture(run, match.group(2), match.group(1))
            contains_image = True
        else:
            # Process **bold** and *italic* within
            sub_segs = re.split(r'(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)', seg)
            for ss in sub_segs:
                if not ss: continue
                if ss.startswith('**') and ss.endswith('**'):
                    r = p.add_run(); set_run_font(r, BODY_FONT_CJK, size)
                    r.bold = True; r.add_text(ss[2:-2])
                elif ss.startswith('*') and ss.endswith('*') and not ss.startswith('**'):
                    r = p.add_run(); set_run_font(r, BODY_FONT_CJK, size)
                    r.italic = True; r.add_text(ss[1:-1])
                elif ss.startswith('`') and ss.endswith('`'):
                    r = p.add_run(); set_run_font(r, 'Consolas', size-1)
                    r.add_text(ss[1:-1])
                else:
                    r = p.add_run(); set_run_font(r, BODY_FONT_CJK, size)
                    if bold: r.bold = True
                    if italic: r.italic = True
                    r.add_text(ss)
    if IMAGE_RE.fullmatch(text.strip()):
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    if contains_image or contains_math:
        p.paragraph_format.line_spacing = 1.0
    if contains_image:
        p.paragraph_format.keep_together = True
    return p

def add_display_math(latex):
    """Add centered display equation using OMML."""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(1)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.0
    omathpara = converter.convert_display(latex)
    p._p.append(omathpara)

def add_list(text, num=None, level=0):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0)
    p.paragraph_format.space_after = Pt(0)
    p.paragraph_format.line_spacing = Pt(BODY_LINE_PT)
    p.paragraph_format.left_indent = Cm(0.4 + level * 0.4)
    p.paragraph_format.first_line_indent = Cm(-0.25)
    # Handle inline math in list items
    segments = re.split(r'(\$[^$]+\$)', text)
    # Prepend bullet/number
    r = p.add_run(); set_run_font(r, BODY_FONT_CJK, BODY_SIZE)
    r.add_text(f'{num}. ' if num else '• ')
    contains_math = False
    for seg in segments:
        if not seg: continue
        if seg.startswith('$') and seg.endswith('$'):
            omath = converter.convert_inline(seg[1:-1])
            p._p.append(omath)
            contains_math = True
        else:
            sub = re.split(r'(\*\*[^*]+\*\*)', seg)
            for ss in sub:
                if not ss: continue
                if ss.startswith('**') and ss.endswith('**'):
                    r = p.add_run(); set_run_font(r, BODY_FONT_CJK, BODY_SIZE)
                    r.bold = True; r.add_text(ss[2:-2])
                else:
                    r = p.add_run(); set_run_font(r, BODY_FONT_CJK, BODY_SIZE)
                    r.add_text(ss)
    if contains_math:
        p.paragraph_format.line_spacing = 1.0

def add_table(rows_md):
    """Parse markdown table and create Word table with OMML in cells."""
    parsed = []
    for row in rows_md:
        if re.match(r'^[\s\|:-]+$', row): continue
        cells = [c.strip() for c in row.strip('|').split('|')]
        parsed.append(cells)
    if not parsed: return
    ncols = max(len(r) for r in parsed)
    table = doc.add_table(rows=len(parsed), cols=ncols)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = 'Table Grid'
    # Minimal cell margins
    tblPr = table._tbl.tblPr
    mar = parse_xml(f'<w:tblCellMar {nsdecls("w")}><w:top w:w="0" w:type="dxa"/><w:left w:w="20" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="20" w:type="dxa"/></w:tblCellMar>')
    tblPr.append(mar)
    for ri, row_data in enumerate(parsed):
        for ci in range(ncols):
            cell_text = row_data[ci] if ci < len(row_data) else ''
            cell = table.cell(ri, ci)
            cell.text = ''
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = Pt(12 if VALIDATION_LAYOUT else 8)
            # Process inline math in cell
            segments = re.split(r'(\$[^$]+\$)', cell_text)
            contains_math = False
            for seg in segments:
                if not seg: continue
                if seg.startswith('$') and seg.endswith('$'):
                    omath = converter.convert_inline(seg[1:-1])
                    p._p.append(omath)
                    contains_math = True
                else:
                    sub = re.split(r'(\*\*[^*]+\*\*)', seg)
                    for ss in sub:
                        if not ss: continue
                        if ss.startswith('**') and ss.endswith('**'):
                            r = p.add_run(); set_run_font(r, BODY_FONT_CJK, TABLE_SIZE)
                            r.bold = True; r.add_text(ss[2:-2])
                        else:
                            r = p.add_run(); set_run_font(r, BODY_FONT_CJK, TABLE_SIZE)
                            if ri == 0: r.bold = True
                            r.add_text(ss)
            if contains_math:
                p.paragraph_format.line_spacing = 1.0
            if ri == 0:
                shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="D6E4FF" w:val="clear"/>')
                cell._tc.get_or_add_tcPr().append(shading)

def add_quote(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(0.5)
    p.paragraph_format.space_after = Pt(0.5)
    p.paragraph_format.left_indent = Cm(0.4)
    p.paragraph_format.line_spacing = Pt(12 if VALIDATION_LAYOUT else 8.5)
    pborder = parse_xml(f'<w:pBdr {nsdecls("w")}><w:left w:val="single" w:sz="10" w:space="3" w:color="4472C4"/></w:pBdr>')
    p._p.get_or_add_pPr().insert(0, pborder)
    shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F0F4FF" w:val="clear"/>')
    p._p.get_or_add_pPr().append(shading)
    # Process inline math
    segments = re.split(r'(\$[^$]+\$)', text)
    contains_math = False
    for seg in segments:
        if not seg: continue
        if seg.startswith('$') and seg.endswith('$'):
            omath = converter.convert_inline(seg[1:-1])
            p._p.append(omath)
            contains_math = True
        else:
            r = p.add_run(); set_run_font(r, BODY_FONT_CJK, TABLE_SIZE)
            r.italic = True
            r.add_text(seg)
    if contains_math:
        p.paragraph_format.line_spacing = 1.0

def add_knowledge_framework_table():
    """Replace ASCII tree with a clean 3-column table."""
    data = [
        ['章', '节', '核心知识点'],
        ['第1章\n分子动理论与气体实验定律', '分子动理论基本观点', '分子组成、热运动、分子力'],
        ['', '油膜法估测分子大小', 'd = V/S，单分子油膜'],
        ['', '气体分子速率分布', '统计规律："中间多、两头少"'],
        ['', '气体状态参量', 'p、V、T 及压强微观机制'],
        ['', '气体实验定律', '玻意耳/查理/盖-吕萨克/理想气体状态方程'],
        ['第2章\n固体与液体', '固体类型及微观结构', '晶体/非晶体、各向异性/各向同性'],
        ['', '表面张力和毛细现象', '表面张力、浸润/不浸润、毛细现象'],
        ['', '材料及其应用', '半导体、纳米材料、石墨烯'],
        ['第3章\n热力学定律', '热力学第一定律', 'ΔU = Q + W'],
        ['', '能量的转化与守恒', '能量守恒定律'],
        ['', '热力学第二定律', '克劳修斯表述 + 开尔文表述'],
        ['', '熵', '熵增加原理、能量退降'],
    ]
    table = doc.add_table(rows=len(data), cols=3)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = 'Table Grid'
    mar = parse_xml(f'<w:tblCellMar {nsdecls("w")}><w:top w:w="0" w:type="dxa"/><w:left w:w="20" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="20" w:type="dxa"/></w:tblCellMar>')
    table._tbl.tblPr.append(mar)
    for ri, row_data in enumerate(data):
        for ci, txt in enumerate(row_data):
            cell = table.cell(ri, ci)
            cell.text = ''
            p = cell.paragraphs[0]
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)
            p.paragraph_format.line_spacing = Pt(12 if VALIDATION_LAYOUT else 8)
            r = p.add_run()
            set_run_font(r, BODY_FONT_CJK, TABLE_SIZE)
            if ri == 0:
                r.bold = True
                shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="D6E4FF" w:val="clear"/>')
                cell._tc.get_or_add_tcPr().append(shading)
            if ci == 0 and ri > 0:
                r.bold = True
            r.add_text(txt)
    # Merge chapter cells (column 0)
    merges = [(1,5), (6,8), (9,12)]  # row ranges for each chapter
    for start, end in merges:
        a = table.cell(start, 0)
        b = table.cell(end, 0)
        a.merge(b)

# ═══════════════════════════════════════════════════════════════
# PARSE MD WITH FILTERS
# ═══════════════════════════════════════════════════════════════
SKIP_SECTIONS = {'考情分析', '讲义说明'}
skip_mode = False

i = 0
in_code = False
code_buf = []
in_tbl = False
tbl_rows = []

while i < len(lines):
    line = lines[i]

    # --- Skip unwanted sections ---
    # Header info lines (适用教材, 复习范围, 课型, 来源标注)
    if line.startswith('> **') and any(k in line for k in ['适用教材','复习范围','课型','来源标注']):
        i += 1; continue
    # 考情分析 section
    if '## 一、考情分析' in line:
        skip_mode = True; i += 1; continue
    if skip_mode and line.startswith('## '):
        skip_mode = False  # next section starts
    if skip_mode:
        i += 1; continue
    # 讲义说明 footer
    if '讲义说明' in line:
        i += 1; continue
    # Empty blockquote lines
    if line.strip() == '>' or (line.startswith('>') and not line.startswith('> ')):
        i += 1; continue

    # --- Code blocks ---
    if line.strip().startswith('```'):
        if in_code:
            # Check if this is the knowledge framework
            code_content = '\n'.join(code_buf)
            if '热学' in code_content and '第1章' in code_content:
                add_knowledge_framework_table()
            else:
                # Other code blocks: render as small text
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.3)
                p.paragraph_format.line_spacing = Pt(8)
                shading = parse_xml(f'<w:shd {nsdecls("w")} w:fill="F5F5F5" w:val="clear"/>')
                p._p.get_or_add_pPr().append(shading)
                r = p.add_run(); set_run_font(r, 'Consolas', 6)
                for cl in code_buf:
                    if cl.strip():
                        r.add_text(cl + '\n')
            code_buf = []
            in_code = False
        else:
            in_code = True
        i += 1; continue
    if in_code:
        code_buf.append(line); i += 1; continue

    # --- Tables ---
    if line.strip().startswith('|') and '|' in line.strip()[1:]:
        if not in_tbl: in_tbl = True; tbl_rows = []
        tbl_rows.append(line.strip()); i += 1; continue
    else:
        if in_tbl:
            add_table(tbl_rows); tbl_rows = []; in_tbl = False

    # --- HTML tags ---
    if line.strip().startswith('<details>') or line.strip() == '</details>':
        i += 1; continue
    if line.strip().startswith('<summary>'):
        txt = re.sub(r'</?summary>', '', line.strip())
        add_para_with_inline(f'【{txt}】', size=6.5, bold=True)
        i += 1; continue

    # --- Headings ---
    if line.startswith('#### '):
        add_heading(line[5:].strip(), 4); i += 1; continue
    if line.startswith('### '):
        add_heading(line[4:].strip(), 3); i += 1; continue
    if line.startswith('## '):
        # Renumber sections (since we removed 考情分析 as section 一)
        heading = line[3:].strip()
        add_heading(heading, 2); i += 1; continue
    if line.startswith('# '):
        add_heading(line[2:].strip(), 1); i += 1; continue

    # --- Display math $$...$$ ---
    if '$$' in line and not line.strip().startswith('$$'):
        parts = re.split(r'(\$\$.*?\$\$)', line.strip())
        for part in parts:
            if not part:
                continue
            if part.startswith('$$') and part.endswith('$$'):
                add_display_math(part[2:-2].strip())
            elif part.strip():
                add_para_with_inline(part.strip())
        i += 1; continue

    if line.strip().startswith('$$'):
        mc = line.strip()[2:]
        if mc.endswith('$$'):
            add_display_math(mc[:-2])
        else:
            ml = [mc]; i += 1
            while i < len(lines) and not lines[i].strip().endswith('$$'):
                ml.append(lines[i].strip()); i += 1
            if i < len(lines):
                ml.append(lines[i].strip()[:-2])
            add_display_math(' '.join(m for m in ml if m))
        i += 1; continue

    # --- Blockquote ---
    if line.startswith('> '):
        add_quote(line[2:].strip()); i += 1; continue

    # --- List items ---
    if re.match(r'^[\s]*[-*]\s', line):
        txt = re.sub(r'^[\s]*[-*]\s+', '', line)
        add_list(txt); i += 1; continue
    if re.match(r'^[\s]*\d+\.\s', line):
        txt = re.sub(r'^[\s]*\d+\.\s+', '', line)
        num = re.match(r'^[\s]*(\d+)\.', line).group(1)
        add_list(txt, num=num); i += 1; continue

    # --- Horizontal rule (skip) ---
    if line.strip() in ('---', '***'):
        i += 1; continue

    # --- Empty lines ---
    if not line.strip():
        i += 1; continue

    # --- Regular paragraph ---
    add_para_with_inline(line.strip())
    i += 1

# Flush remaining table
if tbl_rows:
    add_table(tbl_rows)

# Remove trailing empty paragraphs
while len(doc.paragraphs) > 0:
    last_p = doc.paragraphs[-1]
    has_drawing = bool(last_p._p.findall('.//' + qn('w:drawing')))
    has_math = bool(last_p._p.findall('.//' + qn('m:oMath')))
    if last_p.text.strip() == '' and not has_drawing and not has_math and len(last_p._p) <= 2:
        last_p._element.getparent().remove(last_p._element)
    else:
        break

# ═══════════════════════════════════════════════════════════════
# SAVE
# ═══════════════════════════════════════════════════════════════
OUT = os.environ.get("OUT") or (ARGS.output if ARGS.output else os.path.splitext(MD_PATH)[0] + ".docx")
doc.save(OUT)
sz = os.path.getsize(OUT)
print(f"OK Saved: {OUT}")
print(f"   Size: {sz/1024:.1f} KB")
print(f"   Paragraphs: {len(doc.paragraphs)}, Tables: {len(doc.tables)}")

# ═══════════════════════════════════════════════════════════════
# Post-process via Word COM and export PDF unless explicitly disabled.
# ═══════════════════════════════════════════════════════════════
pdf_path = None
if not ARGS.no_pdf:
    print("Post-processing via Word COM...")
    import time
    import win32com.client

    word = win32com.client.Dispatch('Word.Application')
    word.Visible = False
    word.DisplayAlerts = False
    try:
        word_doc = word.Documents.Open(str(Path(OUT).resolve()))
        time.sleep(1)
        if not VALIDATION_LAYOUT:
            word_doc.Sections(1).PageSetup.TextColumns.SetCount(2)
            word_doc.Sections(1).PageSetup.TextColumns.Spacing = 6
        word_doc.Repaginate()
        pages = word_doc.ComputeStatistics(2)
        layout_name = "single-column validation" if VALIDATION_LAYOUT else "two-column compact"
        print(f"   {layout_name} page count: {pages}")
        if not VALIDATION_LAYOUT:
            word_doc.Save()
        pdf_path = OUT.replace('.docx', '_v2.pdf')
        word_doc.ExportAsFixedFormat(
            str(Path(pdf_path).resolve()),
            ExportFormat=17,
            OpenAfterExport=False,
        )
        print("   PDF exported")
        word_doc.Close(False)
    finally:
        try:
            word.Quit()
        except Exception:
            pass
        time.sleep(1)

    import fitz

    pdf_doc = fitz.open(pdf_path)
    print(f"   PDF pages: {len(pdf_doc)}")
    for i, page in enumerate(pdf_doc):
        text = page.get_text()
        text_lines = [line for line in text.split('\n') if line.strip()]
        print(f"   Page {i+1}: {len(text_lines)} lines, {len(text)} chars")
    pdf_doc.close()


def run_officecli_qa(policy, docx_path, pdf_path):
    if policy == "off":
        print("OfficeCLI QA skipped: disabled")
        return
    repo_root = Path(__file__).resolve().parents[4]
    qa_script = repo_root / "skills" / "_shared" / "scripts" / "officecli_doc_qa.py"
    qa_dir = repo_root / "output" / f"qa_{Path(docx_path).stem}"
    check = subprocess.run(
        [sys.executable, str(qa_script), "check", "--json"],
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check.returncode != 0:
        print("OfficeCLI QA skipped: OfficeCLI unavailable")
        if policy == "required":
            raise SystemExit(check.stdout or check.stderr or "OfficeCLI unavailable")
        return
    qa_cmd = [
        sys.executable,
        str(qa_script),
        "qa",
        "--docx",
        str(Path(docx_path).resolve()),
        "--pdf",
        str(Path(pdf_path).resolve()),
        "--out-dir",
        str(qa_dir),
        "--json",
    ]
    qa = subprocess.run(
        qa_cmd,
        cwd=str(repo_root),
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        payload = json.loads(qa.stdout)
    except json.JSONDecodeError:
        payload = {"ok": False, "stdout": qa.stdout, "stderr": qa.stderr}
    print(f"OfficeCLI QA: {payload.get('status', 'unknown')} -> {qa_dir}")
    if qa.returncode != 0 and policy == "required":
        raise SystemExit(qa.stdout or qa.stderr or "OfficeCLI QA failed")


run_officecli_qa(ARGS.officecli_qa, OUT, pdf_path)
