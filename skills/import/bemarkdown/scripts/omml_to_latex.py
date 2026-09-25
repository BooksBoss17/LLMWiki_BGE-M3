"""Standards-based OMML (Office Math) to portable LaTeX conversion.

OMML structure follows ECMA-376 Part 1 section 22.1 / ISO/IEC 29500-1.
There is no normative OMML-to-LaTeX mapping, so this module explicitly limits
automatic output to the portable subset supported by the Wiki and Word chains.
Unknown or non-portable structures are reported instead of being flattened.
"""
from __future__ import annotations

import zipfile
import re
from dataclasses import dataclass, field
from typing import Any

from lxml import etree

from math_conversion_contract import MathConversionResult, conversion_issue

M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
OMML_STANDARD = "ECMA-376 Part 1 section 22.1 / ISO/IEC 29500-1 Office Math"


UNICODE_LATEX = {
    'Δ': r'\Delta ',
    '×': r'\times ',
    '·': r'\cdot ',
    '≤': r'\leq ',
    '≥': r'\geq ',
    '≠': r'\neq ',
    '≈': r'\approx ',
    '∞': r'\infty ',
    'π': r'\pi ',
    'θ': r'\theta ',
    'α': r'\alpha ',
    'β': r'\beta ',
    'μ': r'\mu ',
    'λ': r'\lambda ',
    'ρ': r'\rho ',
    'φ': r'\varphi ',
    '→': r'\to ',
    '∂': r'\partial ',
    '°': r'^{\circ}',
}

PROPERTY_CONTAINERS = frozenset(
    {
        "accPr", "barPr", "borderBoxPr", "boxPr", "ctrlPr", "dPr", "eqArrPr",
        "fPr", "funcPr", "groupChrPr", "limLowPr", "limUppPr", "mPr", "naryPr",
        "oMathParaPr", "phantPr", "radPr", "rPr", "sPrePr", "sSubPr", "sSubSupPr",
        "sSupPr",
    }
)
PROPERTY_LEAVES = frozenset(
    {
        "aln", "alnScr", "argSz", "baseJc", "begChr", "brk", "brkBin", "brkBinSub",
        "cGp", "cGpRule", "chr", "count", "cSp", "degHide", "diff", "endChr", "grow",
        "hideBot", "hideLeft", "hideRight", "hideTop", "interSp", "intraSp", "jc", "limLoc",
        "lit", "maxDist", "mcJc", "noBreak", "nor", "objDist", "opEmu", "plcHide", "pos",
        "rSp", "rSpRule", "scr", "sepChr", "show", "shp", "smallFrac", "spcDef", "sty",
        "subHide", "supHide", "transp", "type", "vertJc", "wrapIndent", "wrapRight", "zeroAsc",
        "zeroDesc", "zeroWid",
    }
)
CONTAINERS = frozenset({"oMath", "oMathPara", "e", "num", "den", "sub", "sup", "deg", "lim", "fName"})


def _local_name(elem: etree._Element) -> str:
    return etree.QName(elem).localname


def _namespace(elem: etree._Element) -> str | None:
    return etree.QName(elem).namespace


def _mval(elem: etree._Element | None, default: str = "") -> str:
    if elem is None:
        return default
    return elem.get(f"{{{M}}}val", elem.get("val", default))


def _normalize_text(text: str) -> str:
    value = str(text or "")
    for source, target in UNICODE_LATEX.items():
        value = value.replace(source, target)
    return value


def _normalize_latex(value: str) -> str:
    value = re.sub(r'\s+}', '}', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def _delimiter(value: str) -> str:
    if value == "":
        return "."
    return {"{": r"\{", "}": r"\}", "<": r"\langle", ">": r"\rangle"}.get(value, value)


@dataclass
class _OmmlFragment:
    latex: str = ""
    warnings: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    status: str = "converted"

    @classmethod
    def combine(cls, items: list["_OmmlFragment"], *, joiner: str = "") -> "_OmmlFragment":
        status = "converted"
        if any(item.status == "unsupported" for item in items):
            status = "unsupported"
        elif any(item.status == "needs_review" for item in items):
            status = "needs_review"
        return cls(
            latex=joiner.join(item.latex for item in items),
            warnings=[warning for item in items for warning in item.warnings],
            issues=[issue for item in items for issue in item.issues],
            status=status,
        )


class _OmmlRenderer:
    def render(self, elem: etree._Element | None, path: str = "/") -> _OmmlFragment:
        if elem is None:
            return self._unsupported("omml_required_child_missing", "Required OMML child is missing.", path)
        tag = _local_name(elem)
        namespace = _namespace(elem)
        here = f"{path.rstrip('/')}/{tag}"

        if namespace not in {M, W}:
            return self._unsupported("omml_unknown_namespace", f"Unknown math namespace: {namespace}", here)
        if namespace == W:
            return _OmmlFragment()
        if tag in PROPERTY_CONTAINERS or tag in PROPERTY_LEAVES:
            return _OmmlFragment()
        if tag == "t":
            return _OmmlFragment(latex=_normalize_text(elem.text or ""))
        if tag == "r":
            return self._render_run(elem, here)
        if tag in CONTAINERS:
            return self._children(elem, here, skip_properties=True)
        if tag == "f":
            return self._binary_structure(elem, "num", "den", lambda first, second: rf"\frac{{{first}}}{{{second}}}", here)
        if tag == "sSub":
            return self._binary_structure(elem, "e", "sub", lambda base, sub: f"{base}_{{{sub}}}", here)
        if tag == "sSup":
            return self._binary_structure(elem, "e", "sup", lambda base, sup: f"{base}^{{{sup}}}", here)
        if tag == "sSubSup":
            return self._scripts(elem, here)
        if tag == "sPre":
            return self._prescripts(elem, here)
        if tag == "rad":
            return self._radical(elem, here)
        if tag == "d":
            return self._delimited(elem, here)
        if tag == "bar":
            return self._bar(elem, here)
        if tag == "nary":
            return self._nary(elem, here)
        if tag == "func":
            return self._function(elem, here)
        if tag in {"limLow", "limUpp"}:
            return self._limit(elem, here, upper=tag == "limUpp")
        if tag == "acc":
            return self._accent(elem, here)
        if tag == "phant":
            return self._needs_review(
                "omml_phantom_not_portable",
                "OMML phantom layout is not stable in the current Word export chain.",
                here,
            )
        if tag == "eqArr":
            return self._needs_review(
                "omml_equation_array_not_portable",
                "OMML equation arrays require a reviewed target layout.",
                here,
            )
        if tag in {"box", "borderBox", "groupChr", "m", "mr"}:
            return self._needs_review(
                f"omml_{tag}_not_portable",
                f"OMML {tag} is not losslessly supported by the portable target.",
                here,
            )
        return self._unsupported(
            "omml_unknown_math_node",
            f"Unsupported OMML node: {tag}",
            here,
            details={"tag": tag},
        )

    def _children(self, elem: etree._Element, path: str, *, skip_properties: bool = False) -> _OmmlFragment:
        items: list[_OmmlFragment] = []
        for index, child in enumerate(elem):
            tag = _local_name(child)
            if skip_properties and (tag in PROPERTY_CONTAINERS or tag in PROPERTY_LEAVES):
                continue
            items.append(self.render(child, f"{path}[{index + 1}]"))
        return _OmmlFragment.combine(items)

    def _render_run(self, elem: etree._Element, path: str) -> _OmmlFragment:
        rendered = self._children(elem, path, skip_properties=True)
        rpr = elem.find(f"{{{M}}}rPr")
        normal = rpr is not None and rpr.find(f"{{{M}}}nor") is not None
        style = _mval(rpr.find(f"{{{M}}}sty") if rpr is not None else None)
        if rendered.status != "converted" or not rendered.latex:
            return rendered
        if normal or style == "p":
            rendered.latex = rf"\mathrm{{{rendered.latex}}}"
        elif style in {"b", "bi"}:
            rendered.latex = rf"\mathbf{{{rendered.latex}}}"
            if style == "bi":
                rendered.warnings.append(
                    conversion_issue(
                        "omml_bold_italic_simplified",
                        "Portable target preserves bold but not the OMML bold-italic distinction.",
                        standard_reference=OMML_STANDARD,
                        location=path,
                    )
                )
        return rendered

    def _binary_structure(self, elem, first_name, second_name, builder, path) -> _OmmlFragment:
        first = self.render(elem.find(f"{{{M}}}{first_name}"), f"{path}/{first_name}")
        second = self.render(elem.find(f"{{{M}}}{second_name}"), f"{path}/{second_name}")
        combined = _OmmlFragment.combine([first, second])
        if combined.status == "converted":
            combined.latex = builder(first.latex, second.latex)
        return combined

    def _scripts(self, elem, path) -> _OmmlFragment:
        base = self.render(elem.find(f"{{{M}}}e"), f"{path}/e")
        sub = self.render(elem.find(f"{{{M}}}sub"), f"{path}/sub")
        sup = self.render(elem.find(f"{{{M}}}sup"), f"{path}/sup")
        result = _OmmlFragment.combine([base, sub, sup])
        if result.status == "converted":
            result.latex = f"{base.latex}_{{{sub.latex}}}^{{{sup.latex}}}"
        return result

    def _prescripts(self, elem, path) -> _OmmlFragment:
        base = self.render(elem.find(f"{{{M}}}e"), f"{path}/e")
        sub = self.render(elem.find(f"{{{M}}}sub"), f"{path}/sub")
        sup = self.render(elem.find(f"{{{M}}}sup"), f"{path}/sup")
        result = _OmmlFragment.combine([base, sub, sup])
        if result.status == "converted":
            result.latex = f"{{}}_{{{sub.latex}}}^{{{sup.latex}}}{base.latex}"
        return result

    def _radical(self, elem, path) -> _OmmlFragment:
        radicand = self.render(elem.find(f"{{{M}}}e"), f"{path}/e")
        degree_elem = elem.find(f"{{{M}}}deg")
        degree = self.render(degree_elem, f"{path}/deg") if degree_elem is not None else _OmmlFragment()
        result = _OmmlFragment.combine([radicand, degree])
        if result.status == "converted":
            result.latex = rf"\sqrt[{degree.latex}]{{{radicand.latex}}}" if degree.latex else rf"\sqrt{{{radicand.latex}}}"
        return result

    def _delimited(self, elem, path) -> _OmmlFragment:
        props = elem.find(f"{{{M}}}dPr")
        left = _mval(props.find(f"{{{M}}}begChr") if props is not None else None, "(")
        right = _mval(props.find(f"{{{M}}}endChr") if props is not None else None, ")")
        separator = _mval(props.find(f"{{{M}}}sepChr") if props is not None else None, ",")
        values = [self.render(child, f"{path}/e[{index}]") for index, child in enumerate(elem.findall(f"{{{M}}}e"), start=1)]
        if not values:
            return self._unsupported("omml_delimiter_missing_element", "OMML delimiter has no element.", path)
        result = _OmmlFragment.combine(values, joiner=_normalize_text(separator))
        if result.status == "converted":
            result.latex = rf"\left{_delimiter(left)}{result.latex}\right{_delimiter(right)}"
        return result

    def _bar(self, elem, path) -> _OmmlFragment:
        value = self.render(elem.find(f"{{{M}}}e"), f"{path}/e")
        props = elem.find(f"{{{M}}}barPr")
        position = _mval(props.find(f"{{{M}}}pos") if props is not None else None, "top")
        if position in {"top", "bot"} and value.status == "converted":
            if position == "top":
                value.latex = rf"\overline{{{value.latex}}}"
                return value
            return self._needs_review(
                "omml_bottom_bar_not_portable",
                "OMML bottom bars are not stable in the current Word export chain.",
                path,
            )
        return value

    def _nary(self, elem, path) -> _OmmlFragment:
        props = elem.find(f"{{{M}}}naryPr")
        operator = _mval(props.find(f"{{{M}}}chr") if props is not None else None, "∫")
        op_latex = {"∫": r"\int", "∑": r"\sum", "∏": r"\prod", "∮": r"\oint"}.get(operator)
        if op_latex is None:
            return self._needs_review("omml_nary_operator_not_portable", f"Unsupported n-ary operator: {operator}", path)
        base = self.render(elem.find(f"{{{M}}}e"), f"{path}/e")
        sub_elem = elem.find(f"{{{M}}}sub")
        sup_elem = elem.find(f"{{{M}}}sup")
        sub = self.render(sub_elem, f"{path}/sub") if sub_elem is not None else _OmmlFragment()
        sup = self.render(sup_elem, f"{path}/sup") if sup_elem is not None else _OmmlFragment()
        result = _OmmlFragment.combine([base, sub, sup])
        if result.status == "converted":
            result.latex = op_latex + (f"_{{{sub.latex}}}" if sub.latex else "") + (f"^{{{sup.latex}}}" if sup.latex else "") + base.latex
        return result

    def _function(self, elem, path) -> _OmmlFragment:
        name = self.render(elem.find(f"{{{M}}}fName"), f"{path}/fName")
        value = self.render(elem.find(f"{{{M}}}e"), f"{path}/e")
        result = _OmmlFragment.combine([name, value])
        if result.status == "converted":
            raw_name = name.latex.strip()
            known = {"sin", "cos", "tan", "cot", "log", "ln", "exp", "max", "min"}
            function = "\\" + raw_name if raw_name in known else rf"\operatorname{{{raw_name}}}"
            result.latex = f"{function}{{{value.latex}}}"
        return result

    def _limit(self, elem, path, *, upper: bool) -> _OmmlFragment:
        base = self.render(elem.find(f"{{{M}}}e"), f"{path}/e")
        limit = self.render(elem.find(f"{{{M}}}lim"), f"{path}/lim")
        result = _OmmlFragment.combine([base, limit])
        if result.status == "converted":
            result.latex = f"{base.latex}{'^' if upper else '_'}{{{limit.latex}}}"
        return result

    def _accent(self, elem, path) -> _OmmlFragment:
        props = elem.find(f"{{{M}}}accPr")
        accent = _mval(props.find(f"{{{M}}}chr") if props is not None else None, "̂")
        command = {"→": r"\vec", "̂": r"\hat", "~": r"\tilde", "¯": r"\bar", ".": r"\dot", "¨": r"\ddot"}.get(accent)
        if command is None:
            return self._needs_review("omml_accent_not_portable", f"Unsupported OMML accent: {accent}", path)
        value = self.render(elem.find(f"{{{M}}}e"), f"{path}/e")
        if value.status == "converted":
            value.latex = f"{command}{{{value.latex}}}"
        return value

    @staticmethod
    def _unsupported(code, message, path, details=None) -> _OmmlFragment:
        return _OmmlFragment(
            status="unsupported",
            issues=[conversion_issue(code, message, standard_reference=OMML_STANDARD, location=path, details=details)],
        )

    @staticmethod
    def _needs_review(code, message, path) -> _OmmlFragment:
        return _OmmlFragment(
            status="needs_review",
            issues=[conversion_issue(code, message, standard_reference=OMML_STANDARD, location=path)],
        )


def convert_omml(elem: etree._Element) -> MathConversionResult:
    """Convert one OMML subtree and retain all standard/portability diagnostics."""
    fragment = _OmmlRenderer().render(elem)
    latex = _normalize_latex(fragment.latex) if fragment.status == "converted" else None
    return MathConversionResult(
        status=fragment.status,
        source_kind="omml",
        latex=latex,
        standard_reference=OMML_STANDARD,
        warnings=fragment.warnings,
        unsupported_constructs=fragment.issues,
    )


def omml_to_latex(elem: etree._Element) -> str:
    """Compatibility API that blocks unknown or non-portable OMML."""
    return convert_omml(elem).require_latex()


def extract_omml_formulas(docx_path):
    """Extract all OMML formulas from a DOCX file, returning (formula_list, modified_xml_tree).

    Returns:
        formulas: list of LaTeX strings, in document order
        tree: the lxml tree of document.xml (for later full-document conversion)
    """
    with zipfile.ZipFile(docx_path, 'r') as z:
        with z.open('word/document.xml') as f:
            doc_xml = f.read()

    tree = etree.fromstring(doc_xml)
    omaths = tree.findall(f'.//{{{M}}}oMath')

    formulas = []
    for om in omaths:
        latex = omml_to_latex(om)
        # Clean up: remove trailing spaces before }
        latex = re.sub(r'\s+}', '}', latex)
        latex = re.sub(r'\s+', ' ', latex).strip()
        formulas.append(latex)

    return formulas, tree


def replace_omml_with_latex(tree):
    """Replace all oMath elements in the XML tree with text nodes containing LaTeX.

    This modifies the tree in-place so that mammoth (or any other DOCX→HTML converter)
    will see the LaTeX text instead of dropping the OMML.
    """
    M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
    W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"

    omaths = tree.findall(f'.//{{{M}}}oMath')
    count = 0
    for om in omaths:
        latex = omml_to_latex(om)
        latex = re.sub(r'\s+}', '}', latex)
        latex = re.sub(r'\s+', ' ', latex).strip()

        # Determine if this is inline or block (oMathPara parent = block)
        parent = om.getparent()
        is_block = parent is not None and parent.tag == f'{{{M}}}oMathPara'

        # Create a w:r element with the LaTeX text
        nsmap = {None: "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
        r = etree.SubElement(parent, f'{{{W}}}r')
        rPr = etree.SubElement(r, f'{{{W}}}rPr')
        # Mark as italic for formula appearance
        t_elem = etree.SubElement(r, f'{{{W}}}t')
        t_elem.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')

        if is_block:
            t_elem.text = f'$$ {latex} $$'
        else:
            t_elem.text = f' ${latex}$ '

        # Remove the oMath element
        parent.remove(om)
        count += 1

    return count


def docx_with_omml_to_md(docx_path, output_dir):
    """Convert a DOCX with OMML formulas to Markdown.

    Strategy:
    1. Read document.xml from the DOCX zip
    2. Replace all oMath elements with LaTeX text nodes
    3. Re-zip the modified document.xml back into a new DOCX
    4. Run mammoth on the modified DOCX
    5. Post-process mammoth output
    """
    import mammoth
    from markdownify import markdownify as md_convert
    import os, io

    os.makedirs(output_dir, exist_ok=True)
    media_dir = os.path.join(output_dir, "media")
    os.makedirs(media_dir, exist_ok=True)

    # Step 1: Read DOCX
    with zipfile.ZipFile(docx_path, 'r') as z:
        with z.open('word/document.xml') as f:
            doc_xml = f.read()
        # Read all other files
        other_files = {}
        for name in z.namelist():
            if name != 'word/document.xml':
                other_files[name] = z.read(name)

    # Step 2: Parse, replace OMML, serialize back
    tree = etree.fromstring(doc_xml)
    formula_count = replace_omml_with_latex(tree)
    modified_xml = etree.tostring(tree, xml_declaration=True, encoding='UTF-8', standalone=True)

    # Step 3: Create modified DOCX in memory
    modified_docx = io.BytesIO()
    with zipfile.ZipFile(modified_docx, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('word/document.xml', modified_xml)
        for name, data in other_files.items():
            z.writestr(name, data)
    modified_docx.seek(0)

    # Step 4: Run mammoth on modified DOCX
    img_counter = [0]

    def save_image(image):
        img_counter[0] += 1
        ext = image.content_type.split('/')[-1] if image.content_type else 'png'
        if ext == 'jpeg':
            ext = 'jpg'
        filename = f"image_{img_counter[0]:04d}.{ext}"
        filepath = os.path.join(media_dir, filename)
        with open(filepath, 'wb') as f:
            with image.open() as img_stream:
                f.write(img_stream.read())
        return {"src": f"media/{filename}"}

    result = mammoth.convert_to_html(modified_docx, convert_image=mammoth.images.img_element(save_image))
    html = result.value
    md = md_convert(html)

    # Step 5: Post-process
    md = md.replace('\\.', '.')
    md = md.replace('\\(', '(')
    md = md.replace('\\)', ')')
    md = md.replace('\\!', '!')
    md = md.replace('\xa0', ' ')
    md = re.sub(r'\n{3,}', '\n\n', md)

    md_path = os.path.join(output_dir, os.path.splitext(os.path.basename(docx_path))[0] + '.md')
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(md)

    return md_path, formula_count, img_counter[0]


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 2:
        print("Usage: python omml_to_latex.py <docx_path> [output_dir]")
        sys.exit(1)

    docx_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else './output'

    # First: just extract and show formulas
    formulas, tree = extract_omml_formulas(docx_path)
    print(f"Found {len(formulas)} OMML formulas:")
    for i, f in enumerate(formulas):
        print(f"  [{i+1:2d}] ${f}$")

    # Second: full conversion
    print(f"\nConverting {docx_path}...")
    md_path, fcount, icount = docx_with_omml_to_md(docx_path, output_dir)
    print(f"\n✅ Converted: {md_path}")
    print(f"   OMML formulas replaced: {fcount}")
    print(f"   Images extracted: {icount}")
