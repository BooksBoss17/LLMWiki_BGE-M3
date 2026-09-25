"""Strict MTEF XML AST to portable MathML and KB-LaTeX mapping.

The input is the XML snapshot emitted by the allowlisted ``mathtype`` parser.
This module deliberately supports a conservative structural subset.  Unknown
records, templates, variations, or private-use glyphs are returned as blocked
evidence instead of being flattened into plausible-looking mathematics.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from typing import Iterable


MATHML = "http://www.w3.org/1998/Math/MathML"
ET.register_namespace("", MATHML)

STRUCTURAL_RECORDS = {
    "slot", "char", "tmpl", "pile", "matrix", "embell", "ruler", "size",
    "full", "sub", "sub2", "sym", "subsym", "color", "end",
}
METADATA_RECORDS = {
    "mtef_version", "platform", "product", "product_version", "product_subversion",
    "application_key", "equation_options", "font_style_def", "font", "font_def",
    "encoding_def", "eqn_prefs", "color_def", "future", "mt_comment",
}
FENCE_MAP = {
    "tmANGLE": ("⟨", "⟩"),
    "tmPAREN": ("(", ")"),
    "tmBRACE": ("{", "}"),
    "tmBRACK": ("[", "]"),
    "tmBAR": ("|", "|"),
    "tmDBAR": ("‖", "‖"),
    "tmFLOOR": ("⌊", "⌋"),
    "tmCEILING": ("⌈", "⌉"),
    "tmOBRACK": ("⟦", "⟧"),
}
INTERVAL_FENCE_MAP = {
    "tvINTV_LBLB": ("[", "["),
    "tvINTV_LBRP": ("[", ")"),
    "tvINTV_RBRB": ("]", "]"),
    "tvINTV_RBLB": ("]", "["),
    "tvINTV_LPRB": ("(", "]"),
}
BIG_OPERATORS = {
    "tmINTEG": "∫", "tmSUM": "∑", "tmPROD": "∏", "tmCOPROD": "∐",
    "tmUNION": "⋃", "tmINTER": "⋂",
}
ACCENTS = {
    "tmVEC": "→", "tmTILDE": "~", "tmHAT": "^", "tmARC": "⌒",
}
SUPPORTED_TEMPLATES = set(FENCE_MAP) | set(BIG_OPERATORS) | {
    "tmINTERVAL", "tmROOT", "tmFRACT", "tmUBAR", "tmOBAR", "tmARROW",
    "tmINTOP", "tmSUMOP", "tmLIM", "tmHBRACE", "tmHBRACK", "tmLDIV",
    "tmSUB", "tmSUP", "tmSUBSUP", "tmDIRAC", "tmVEC", "tmTILDE",
    "tmHAT", "tmARC", "tmJSTATUS", "tmSTRIKE", "tmBOX",
}

UNICODE_LATEX = {
    "−": "-", "×": r"\times", "÷": r"\div", "⋅": r"\cdot", "·": r"\cdot",
    "±": r"\pm", "∓": r"\mp", "≤": r"\le", "≥": r"\ge", "≠": r"\ne",
    "≈": r"\approx", "≡": r"\equiv", "∞": r"\infty", "∂": r"\partial",
    "∇": r"\nabla", "∈": r"\in", "∉": r"\notin", "⊥": r"\perp",
    "∥": r"\parallel", "∝": r"\propto", "→": r"\to", "←": r"\leftarrow",
    "↔": r"\leftrightarrow", "⇒": r"\Rightarrow", "⇔": r"\Leftrightarrow",
    "∫": r"\int", "∬": r"\iint", "∭": r"\iiint", "∑": r"\sum",
    "∏": r"\prod", "∐": r"\coprod", "⋃": r"\bigcup", "⋂": r"\bigcap",
    "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "δ": r"\delta",
    "ε": r"\epsilon", "θ": r"\theta", "λ": r"\lambda", "μ": r"\mu",
    "ν": r"\nu", "ξ": r"\xi", "π": r"\pi", "ρ": r"\rho",
    "σ": r"\sigma", "τ": r"\tau", "φ": r"\phi", "ϕ": r"\varphi",
    "ω": r"\omega", "Δ": r"\Delta", "Θ": r"\Theta", "Λ": r"\Lambda",
    "Π": r"\Pi", "Σ": r"\Sigma", "Φ": r"\Phi", "Ω": r"\Omega",
    "η": r"\eta", "υ": r"\upsilon", "∼": r"\sim", "∠": r"\angle",
    "∣": r"\mid", "△": r"\triangle", "⋯": r"\cdots", "…": r"\ldots",
    "∶": ":", "≪": r"\ll", "≫": r"\gg", "⌒": r"\frown",
    "⩽": r"\leqslant", "⩾": r"\geqslant", "⊂": r"\subset",
    "∵": r"\because", "∴": r"\therefore", "↑": r"\uparrow", "↓": r"\downarrow",
    "∘": r"\circ",
    "°": r"^{\circ}", "~": r"\sim", "'": "'", " ": "", "\u00a0": "",
    "、": r"\text{、}", "，": ",", "。": ".", "．": ".", "：": ":", "；": ";",
    "！": "!", "？": "?", "（": "(", "）": ")", "＝": "=", "－": "-", "﹣": "-",
    "＋": "+", "＜": "<", "＞": ">", "～": r"\sim",
    "Ⅰ": r"\mathrm{I}", "Ⅱ": r"\mathrm{II}", "Ⅲ": r"\mathrm{III}",
    "₁": "_{1}", "₂": "_{2}", "²": "^{2}", "℃": r"^{\circ}\mathrm{C}",
    "—": r"\text{—}", "―": r"\text{―}", "“": r"\text{“}",
    "І": r"\text{І}", "П": r"\text{П}",
}

PRIVATE_SPACING = {
    0xEB00: "", 0xEF00: "", 0xEB01: "", 0xEF01: "",
    0xEB02: "\u2009", 0xEF02: "\u2009", 0xEB03: "\u2005", 0xEF03: "\u2005",
    0xEB04: "\u2004", 0xEF04: "\u2004", 0xEB05: "\u2003", 0xEF05: "\u2003",
}


@dataclass
class MappingResult:
    status: str
    mathml: str | None = None
    latex: str | None = None
    risk_flags: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    version: int | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class MtefMappingError(ValueError):
    pass


def _m(tag: str, text: str | None = None, **attributes: str) -> ET.Element:
    node = ET.Element(f"{{{MATHML}}}{tag}", attributes)
    node.text = text
    return node


def _local(node: ET.Element) -> str:
    return node.tag.rsplit("}", 1)[-1]


def _text(node: ET.Element, name: str, default: str = "") -> str:
    child = node.find(name)
    return (child.text or default).strip() if child is not None else default


def _variations(node: ET.Element) -> set[str]:
    return {str(child.text or "").strip() for child in node.findall("variation")}


def _token(character: str) -> ET.Element:
    if character.isdigit():
        return _m("mn", character)
    if character.isalpha() or character in "∞∂∇":
        return _m("mi", character)
    if "\u3400" <= character <= "\u9fff":
        return _m("mtext", character)
    return _m("mo", character)


def _render_char(node: ET.Element) -> ET.Element:
    raw = _text(node, "mt_code_value")
    if not re.fullmatch(r"0x[0-9A-Fa-f]{4,6}", raw):
        raise MtefMappingError(f"invalid or missing mt_code_value: {raw!r}")
    codepoint = int(raw, 16)
    if codepoint in PRIVATE_SPACING:
        value = PRIVATE_SPACING[codepoint]
        return _m("mspace", width="0em") if not value else _m("mspace", width={"\u2009": "0.1667em", "\u2005": "0.25em", "\u2004": "0.3333em", "\u2003": "1em"}[value])
    if 0xE000 <= codepoint <= 0xF8FF:
        raise MtefMappingError(f"unmapped private-use MathType glyph: {raw}")
    if not 0 <= codepoint <= 0x10FFFF:
        raise MtefMappingError(f"invalid Unicode codepoint: {raw}")
    value = chr(codepoint)
    result = _token(value)
    embellishments = [str(item.text or "").strip() for item in node.findall("embell/embell")]
    for embellishment in embellishments:
        if embellishment in {"emb1DOT", "emb2DOT", "emb3DOT", "emb4DOT"}:
            dots = {"emb1DOT": "˙", "emb2DOT": "¨", "emb3DOT": "⃛", "emb4DOT": "⃜"}[embellishment]
            wrapper = _m("mover", accent="true")
            wrapper.extend([result, _m("mo", dots)])
            result = wrapper
        elif embellishment in {"emb1PRIME", "emb2PRIME", "emb3PRIME"}:
            primes = {"emb1PRIME": "′", "emb2PRIME": "″", "emb3PRIME": "‴"}[embellishment]
            wrapper = _m("msup")
            wrapper.extend([result, _m("mo", primes)])
            result = wrapper
        elif embellishment in {
            "embHAT", "embTILDE", "embRARROW", "embLARROW", "embBARROW",
            "embR1ARROW", "embL1ARROW", "embOBAR", "embFROWN", "embSMILE",
        }:
            accent = {
                "embHAT": "^", "embTILDE": "~", "embRARROW": "→", "embLARROW": "←",
                "embBARROW": "↔", "embR1ARROW": "⇀", "embL1ARROW": "↼", "embOBAR": "¯",
                "embFROWN": "⌒", "embSMILE": "⌣",
            }[embellishment]
            wrapper = _m("mover", accent="true")
            wrapper.extend([result, _m("mo", accent)])
            result = wrapper
        elif embellishment in {"embU_1DOT", "embU_2DOT", "embU_3DOT", "embU_4DOT", "embU_BAR", "embU_TILDE"}:
            accent = {
                "embU_1DOT": ".", "embU_2DOT": "..", "embU_3DOT": "...", "embU_4DOT": "....",
                "embU_BAR": "_", "embU_TILDE": "~",
            }[embellishment]
            wrapper = _m("munder", accent="true")
            wrapper.extend([result, _m("mo", accent)])
            result = wrapper
        else:
            raise MtefMappingError(f"unsupported embellishment: {embellishment}")
    return result


def _meaningful_children(node: ET.Element) -> Iterable[ET.Element]:
    for child in node:
        if _local(child) in STRUCTURAL_RECORDS:
            yield child


def _row(items: Iterable[ET.Element]) -> ET.Element:
    values = list(items)
    if not values:
        return _m("mrow")
    if len(values) == 1:
        return values[0]
    row = _m("mrow")
    row.extend(values)
    return row


def _is_empty(node: ET.Element | None) -> bool:
    if node is None:
        return True
    if (node.text or "").strip():
        return False
    return all(_is_empty(child) for child in node)


def _render_sequence(node: ET.Element) -> ET.Element:
    output: list[ET.Element] = []
    pending_script: ET.Element | None = None
    for child in _meaningful_children(node):
        tag = _local(child)
        if tag in {"end", "full", "sub", "sub2", "sym", "subsym", "ruler", "size", "color", "embell"}:
            continue
        if tag == "tmpl" and _text(child, "selector") in {"tmSUB", "tmSUP", "tmSUBSUP"}:
            if not output:
                if pending_script is not None:
                    raise MtefMappingError("multiple leading script templates have no base")
                pending_script = child
                continue
            base = output.pop()
            output.append(_render_script(child, base))
            continue
        rendered = _render_node(child)
        if pending_script is not None:
            rendered = _render_script(pending_script, rendered)
            pending_script = None
        output.append(rendered)
    if pending_script is not None:
        # MathType can preserve a script-only object inside another script
        # slot.  Retain its explicit empty base instead of discarding the
        # nested structure or inventing a neighbouring base.
        output.append(_render_script(pending_script, _m("mrow")))
    return _row(output)


def _render_script(node: ET.Element, base: ET.Element) -> ET.Element:
    selector = _text(node, "selector")
    slots = [_render_sequence(slot) for slot in node.findall("slot")]
    if len(slots) > 2:
        raise MtefMappingError(f"invalid {selector} slot count: {len(slots)}")
    lower = slots[0] if slots else None
    upper = slots[1] if len(slots) > 1 else (slots[0] if selector == "tmSUP" and slots else None)
    if selector == "tmSUB":
        if _is_empty(lower):
            return base
        result = _m("msub")
        result.extend([base, lower])
        return result
    if selector == "tmSUP":
        if _is_empty(upper):
            return base
        result = _m("msup")
        result.extend([base, upper])
        return result
    if selector == "tmSUBSUP":
        if _is_empty(lower) and _is_empty(upper):
            return base
        if _is_empty(lower):
            result = _m("msup")
            result.extend([base, upper])
            return result
        if _is_empty(upper):
            result = _m("msub")
            result.extend([base, lower])
            return result
        result = _m("msubsup")
        result.extend([base, lower, upper])
        return result
    raise MtefMappingError(f"invalid {selector} slot count: {len(slots)}")


def _render_big_operator(node: ET.Element, selector: str, slots: list[ET.Element]) -> ET.Element:
    symbol = BIG_OPERATORS.get(selector)
    symbol_nodes = [_render_char(value) for value in node.findall("char")]
    operator = symbol_nodes[-1] if symbol_nodes else (_m("mo", symbol) if symbol else None)
    if operator is None and selector in {"tmINTOP", "tmSUMOP"} and len(slots) >= 2:
        operator = slots.pop()
    if operator is None:
        raise MtefMappingError(f"{selector} has no explicit operator")
    body = slots[0] if slots else _m("mrow")
    lower = slots[1] if len(slots) > 1 and not _is_empty(slots[1]) else None
    upper = slots[2] if len(slots) > 2 and not _is_empty(slots[2]) else None
    if lower is not None and upper is not None:
        scripted = _m("munderover")
        scripted.extend([operator, lower, upper])
    elif lower is not None:
        scripted = _m("munder")
        scripted.extend([operator, lower])
    elif upper is not None:
        scripted = _m("mover")
        scripted.extend([operator, upper])
    else:
        scripted = operator
    return _row([scripted, body])


def _render_template(node: ET.Element) -> ET.Element:
    selector = _text(node, "selector")
    if selector not in SUPPORTED_TEMPLATES:
        raise MtefMappingError(f"unsupported template selector: {selector or '<missing>'}")
    slots = [_render_sequence(slot) for slot in node.findall("slot")]
    variations = _variations(node)

    if selector in FENCE_MAP:
        if not slots:
            raise MtefMappingError(f"{selector} has no content slot")
        left, right = FENCE_MAP[selector]
        fenced = _m("mfenced", open=left, close=right)
        fenced.append(slots[0])
        return fenced
    if selector == "tmINTERVAL":
        matches = [INTERVAL_FENCE_MAP[name] for name in variations if name in INTERVAL_FENCE_MAP]
        if len(matches) != 1 or not slots:
            raise MtefMappingError("unsupported interval fence variation")
        left, right = matches[0]
        fenced = _m("mfenced", open=left, close=right)
        fenced.append(slots[0])
        return fenced
    if selector == "tmFRACT" and len(slots) >= 2:
        frac = _m("mfrac")
        frac.extend(slots[:2])
        return frac
    if selector == "tmROOT" and slots:
        if "tvROOT_NTH" in variations and len(slots) >= 2:
            root = _m("mroot")
            root.extend([slots[0], slots[1]])
            return root
        root = _m("msqrt")
        root.append(slots[0])
        return root
    if selector in {"tmUBAR", "tmOBAR"} and slots:
        wrapper = _m("munder" if selector == "tmUBAR" else "mover", accent="true")
        wrapper.extend([slots[0], _m("mo", "_") if selector == "tmUBAR" else _m("mo", "¯")])
        return wrapper
    if selector in BIG_OPERATORS or selector in {"tmINTOP", "tmSUMOP"}:
        return _render_big_operator(node, selector, slots)
    if selector in ACCENTS and slots:
        wrapper = _m("mover", accent="true")
        wrapper.extend([slots[0], _m("mo", ACCENTS[selector])])
        return wrapper
    if selector == "tmLIM" and slots:
        lower = slots[1] if len(slots) > 1 and not _is_empty(slots[1]) else None
        upper = slots[2] if len(slots) > 2 and not _is_empty(slots[2]) else None
        if lower is not None and upper is not None:
            wrapper = _m("munderover")
            wrapper.extend([_m("mi", "lim"), lower, upper])
        elif lower is not None:
            wrapper = _m("munder")
            wrapper.extend([_m("mi", "lim"), lower])
        elif upper is not None:
            wrapper = _m("mover")
            wrapper.extend([_m("mi", "lim"), upper])
        else:
            wrapper = _m("mi", "lim")
        return _row([wrapper, slots[0]])
    if selector in {"tmHBRACE", "tmHBRACK"} and slots:
        top = "tvHB_TOP" in variations
        wrapper = _m("mover" if top else "munder", accent="true")
        wrapper.extend([slots[0], _m("mo", "⏞" if top else "⏟")])
        if len(slots) > 1:
            outer = _m("mover" if top else "munder")
            outer.extend([wrapper, slots[1]])
            return outer
        return wrapper
    if selector == "tmDIRAC" and slots:
        fenced = _m("mfenced", open="⟨", close="⟩", separators="|")
        fenced.extend(slots)
        return fenced
    if selector == "tmBOX" and slots:
        return _m("menclose", notation="box") if not slots else _append(_m("menclose", notation="box"), slots[0])
    if selector == "tmSTRIKE" and slots:
        return _append(_m("menclose", notation="updiagonalstrike"), slots[0])
    if selector in {"tmARROW", "tmLDIV", "tmJSTATUS"}:
        raise MtefMappingError(f"{selector} requires visual review")
    raise MtefMappingError(f"unsupported {selector} variation/slot layout")


def _append(parent: ET.Element, child: ET.Element) -> ET.Element:
    parent.append(child)
    return parent


def _render_matrix(node: ET.Element) -> ET.Element:
    slots = [_render_sequence(slot) for slot in node.findall("slot")]
    rows_raw = _text(node, "rows") or _text(node, "row_count")
    cols_raw = _text(node, "cols") or _text(node, "column_count")
    try:
        rows = int(rows_raw) if rows_raw else 1
        cols = int(cols_raw) if cols_raw else max(1, len(slots))
    except ValueError as exc:
        raise MtefMappingError("invalid matrix dimensions") from exc
    if rows * cols != len(slots):
        raise MtefMappingError(f"matrix dimensions {rows}x{cols} do not match {len(slots)} slots")
    table = _m("mtable")
    for row_index in range(rows):
        row = _m("mtr")
        for value in slots[row_index * cols : (row_index + 1) * cols]:
            cell = _m("mtd")
            cell.append(value)
            row.append(cell)
        table.append(row)
    return table


def _render_node(node: ET.Element) -> ET.Element:
    tag = _local(node)
    if tag == "char":
        return _render_char(node)
    if tag == "tmpl":
        return _render_template(node)
    if tag == "matrix":
        return _render_matrix(node)
    if tag == "pile":
        table = _m("mtable")
        for slot in node.findall("slot"):
            row = _m("mtr")
            cell = _m("mtd")
            cell.append(_render_sequence(slot))
            row.append(cell)
            table.append(row)
        return table
    if tag == "slot":
        return _render_sequence(node)
    raise MtefMappingError(f"unsupported structural record: {tag}")


def _latex_text(value: str) -> str:
    if value in UNICODE_LATEX:
        return UNICODE_LATEX[value]
    if re.fullmatch(r"[A-Za-z0-9.,;:!?%()\[\]+\-=<>/|]", value):
        return value
    if "\u3400" <= value <= "\u9fff":
        return r"\text{" + value + "}"
    raise MtefMappingError(f"unmapped MathML token: U+{ord(value):04X}")


def mathml_to_latex(node: ET.Element) -> str:
    tag = _local(node)
    children = list(node)
    if tag in {"math", "mrow", "mtd", "mtr", "mtable"}:
        if tag == "mtable":
            rows = [" & ".join(mathml_to_latex(cell) for cell in row) for row in children]
            return r"\begin{matrix}" + r" \\ ".join(rows) + r"\end{matrix}"
        return "".join(mathml_to_latex(child) for child in children)
    if tag == "mspace":
        return ""
    if tag in {"mi", "mn", "mo", "mtext"}:
        raw = node.text or ""
        primes = {"′": "'", "″": "''", "‴": "'''"}
        return "".join(primes[char] if char in primes else _latex_text(char) for char in raw)
    if tag == "mfrac" and len(children) == 2:
        return r"\frac{" + mathml_to_latex(children[0]) + "}{" + mathml_to_latex(children[1]) + "}"
    if tag == "msqrt" and children:
        return r"\sqrt{" + mathml_to_latex(children[0]) + "}"
    if tag == "mroot" and len(children) == 2:
        return r"\sqrt[" + mathml_to_latex(children[1]) + "]{" + mathml_to_latex(children[0]) + "}"
    if tag in {"msub", "msup"} and len(children) == 2:
        marker = "_" if tag == "msub" else "^"
        return "{" + mathml_to_latex(children[0]) + "}" + marker + "{" + mathml_to_latex(children[1]) + "}"
    if tag == "msubsup" and len(children) == 3:
        return "{" + mathml_to_latex(children[0]) + "}_{" + mathml_to_latex(children[1]) + "}^{" + mathml_to_latex(children[2]) + "}"
    if tag in {"munder", "mover", "munderover"} and len(children) >= 2:
        base = mathml_to_latex(children[0])
        raw_mark = children[1].text or ""
        accent_commands = {
            "^": "hat", "~": "tilde", "→": "overrightarrow", "←": "overleftarrow",
            "↔": "overleftrightarrow", "⇀": "overrightharpoon", "↼": "overleftharpoon",
            "¯": "overline", "˙": "dot", "¨": "ddot", "⃛": "dddot", "⃜": "ddddot",
        }
        if (node.get("accent") == "true" or tag == "mover") and tag != "munder":
            command = accent_commands.get(raw_mark)
            if command:
                return "\\" + command + "{" + base + "}"
        mark = mathml_to_latex(children[1])
        latex = "{" + base + "}_{" + mark + "}" if tag == "munder" else "{" + base + "}^{" + mark + "}"
        if tag == "munderover" and len(children) == 3:
            latex = "{" + base + "}_{" + mark + "}^{" + mathml_to_latex(children[2]) + "}"
        return latex
    if tag == "mfenced" and children:
        inner = ",".join(mathml_to_latex(child) for child in children)
        return r"\left" + node.get("open", "(") + inner + r"\right" + node.get("close", ")")
    if tag == "menclose" and children:
        notation = node.get("notation")
        if notation == "box":
            return r"\boxed{" + mathml_to_latex(children[0]) + "}"
        raise MtefMappingError(f"unsupported menclose notation: {notation}")
    raise MtefMappingError(f"unsupported MathML element: {tag}")


def _risk_flags(latex: str) -> list[str]:
    flags: set[str] = set()
    normalized = re.sub(r"\s+", "", latex)
    if re.fullmatch(r"(?:[A-Za-z0-9]|\\[A-Za-z]+)", normalized):
        flags.add("single_character_formula")
    if r"\text{" in latex:
        flags.add("chinese_formula_text")
    if re.search(r"\\(?:vec|overrightarrow|overleftarrow|mathbf)\b", latex):
        flags.add("vector_notation")
    if latex.count("_") + latex.count("^") >= 2:
        flags.add("nuclear_or_multi_script")
    if re.search(r"\\(?:frac|sqrt)\b", latex):
        flags.add("fraction_or_root")
    if latex.count(r"\frac") >= 2 or re.search(r"\\(?:begin\{matrix\}|sum|int)\b", latex):
        flags.add("complex_formula_structure")
    return sorted(flags)


def map_mtef_xml(xml_text: str) -> MappingResult:
    try:
        root = ET.fromstring(xml_text)
        if _local(root) != "root" or len(root) != 1 or _local(root[0]) != "mtef":
            raise MtefMappingError("MTEF XML must contain exactly one root/mtef tree")
        mtef = root[0]
        version = int(_text(mtef, "mtef_version"))
        if version not in {3, 5}:
            raise MtefMappingError(f"unsupported MTEF version: {version}")
        for child in mtef:
            tag = _local(child)
            if tag not in STRUCTURAL_RECORDS and tag not in METADATA_RECORDS:
                raise MtefMappingError(f"unknown top-level MTEF record: {tag}")
        content = _render_sequence(mtef)
        math = _m("math", display="inline")
        math.append(content)
        latex = mathml_to_latex(math)
        if not latex:
            raise MtefMappingError("MTEF mapping produced empty LaTeX")
        return MappingResult(
            status="structure_candidate",
            mathml=ET.tostring(math, encoding="unicode"),
            latex=latex,
            risk_flags=_risk_flags(latex),
            version=version,
        )
    except (ET.ParseError, MtefMappingError, ValueError) as exc:
        return MappingResult(status="blocked", unsupported=[str(exc)])


__all__ = ["MappingResult", "MtefMappingError", "map_mtef_xml", "mathml_to_latex"]
