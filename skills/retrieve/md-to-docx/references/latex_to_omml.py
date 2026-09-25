"""
LaTeX -> OMML converter for teaching handouts.

The converter intentionally supports a small, stable subset used in physics
review materials: fractions, square roots, subscript/superscript, text groups,
Greek letters, common operators, and plain variables/Chinese labels.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from lxml import etree


M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def m_ns(tag: str) -> str:
    return f"{{{M}}}{tag}"


SYMBOLS = {
    r"\Delta": "Δ",
    r"\Phi": "Φ",
    r"\phi": "φ",
    r"\rho": "ρ",
    r"\times": "×",
    r"\cdot": "·",
    r"\leq": "≤",
    r"\le": "≤",
    r"\geq": "≥",
    r"\ge": "≥",
    r"\neq": "≠",
    r"\approx": "≈",
    r"\propto": "∝",
    r"\infty": "∞",
    r"\Rightarrow": "⇒",
    r"\rightarrow": "→",
    r"\to": "→",
    r"\pi": "π",
    r"\alpha": "α",
    r"\beta": "β",
    r"\gamma": "γ",
    r"\theta": "θ",
    r"\sigma": "σ",
    r"\lambda": "λ",
    r"\mu": "μ",
    r"\nu": "ν",
    r"\omega": "ω",
    r"\Omega": "Ω",
    r"\varepsilon": "ε",
    r"\circ": "°",
    r"\partial": "∂",
    r"\sin": "sin",
    r"\cos": "cos",
    r"\tan": "tan",
    r"\arcsin": "arcsin",
    r"\arccos": "arccos",
    r"\int": "∫",
    r"\sum": "∑",
    r"\sim": "∼",
    r"\triangle": "△",
    r"\ll": "≪",
    r"\cdots": "⋯",
    r"\rm": "",
    r"\quad": " ",
    r"\qquad": " ",
    r"\,": " ",
    r"\;": " ",
    r"\!": "",
}


@dataclass
class Token:
    kind: str
    value: str


class LatexToOMML:
    """Convert a practical subset of LaTeX math into Word OMML XML."""

    def __init__(self) -> None:
        self.tokens: list[Token] = []
        self.pos = 0

    def convert_inline(self, latex: str):
        omath = etree.Element(m_ns("oMath"), nsmap={"m": M})
        self._build(omath, latex)
        return omath

    def convert_display(self, latex: str):
        omath_para = etree.Element(m_ns("oMathPara"), nsmap={"m": M})
        omath = etree.SubElement(omath_para, m_ns("oMath"))
        self._build(omath, latex)
        return omath_para

    def _build(self, parent, latex: str) -> None:
        self.tokens = self._tokenize(self._normalize(latex))
        self.pos = 0
        nodes = self._parse_until()
        for node in nodes:
            parent.append(node)

    def _normalize(self, latex: str) -> str:
        latex = latex.strip()
        latex = latex.replace(r"\frac12", r"\frac{1}{2}")
        latex = latex.replace(r"\frac 12", r"\frac{1}{2}")
        latex = latex.replace(r"\sqrt2", r"\sqrt{2}")
        latex = latex.replace(r"\left", "").replace(r"\right", "")
        latex = re.sub(r"(\\[A-Za-z]+)\s+(?=\{)", r"\1", latex)
        latex = re.sub(r"([_^])\s+(?=\{)", r"\1", latex)
        return latex

    def _tokenize(self, text: str) -> list[Token]:
        tokens: list[Token] = []
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\\":
                m = re.match(r"\\[A-Za-z]+", text[i:])
                if m:
                    tokens.append(Token("cmd", m.group(0)))
                    i += len(m.group(0))
                elif i + 1 < len(text):
                    tokens.append(Token("cmd", text[i : i + 2]))
                    i += 2
                else:
                    i += 1
            elif ch in "{}_^":
                tokens.append(Token("op", ch))
                i += 1
            elif ch.isspace():
                tokens.append(Token("text", " "))
                i += 1
            else:
                tokens.append(Token("text", ch))
                i += 1
        return tokens

    def _peek(self) -> Token | None:
        if self.pos >= len(self.tokens):
            return None
        return self.tokens[self.pos]

    def _advance(self) -> Token | None:
        token = self._peek()
        if token is not None:
            self.pos += 1
        return token

    def _parse_until(self, end: str | None = None) -> list:
        nodes = []
        while self.pos < len(self.tokens):
            token = self._peek()
            if token is None:
                break
            if end is not None and token.kind == "op" and token.value == end:
                self._advance()
                break
            if token.kind == "op" and token.value == "}":
                break
            if token.kind == "op" and token.value == "{":
                self._advance()
                nodes.extend(self._parse_until("}"))
                continue

            base = self._parse_atom()
            if base is None:
                continue
            nodes.append(self._apply_scripts(base))
        return nodes

    def _parse_atom(self):
        token = self._advance()
        if token is None:
            return None
        if token.kind == "text":
            return self._run(token.value)
        if token.kind == "op":
            return self._run(token.value)

        cmd = token.value
        if cmd in (r"\frac", r"\dfrac", r"\cfrac"):
            numerator = self._read_group_nodes()
            denominator = self._read_group_nodes()
            f = etree.Element(m_ns("f"))
            num = etree.SubElement(f, m_ns("num"))
            den = etree.SubElement(f, m_ns("den"))
            self._append_all(num, numerator)
            self._append_all(den, denominator)
            return f

        if cmd == r"\sqrt":
            rad = etree.Element(m_ns("rad"))
            deg_hide = etree.SubElement(rad, m_ns("radPr"))
            deg_hide.append(etree.Element(m_ns("degHide")))
            etree.SubElement(rad, m_ns("deg"))
            elem = etree.SubElement(rad, m_ns("e"))
            self._append_all(elem, self._read_group_nodes())
            return rad

        if cmd == r"\text":
            return self._run(self._read_group_text())

        if cmd in (r"\mathrm", r"\mathbf", r"\mathit", r"\textbf"):
            nodes = self._read_group_nodes()
            if len(nodes) == 1:
                return nodes[0]
            box = etree.Element(m_ns("box"))
            elem = etree.SubElement(box, m_ns("e"))
            self._append_all(elem, nodes)
            return box

        if cmd in SYMBOLS:
            return self._run(SYMBOLS[cmd])

        return self._run(cmd.lstrip("\\"))

    def _apply_scripts(self, base):
        sub_nodes = None
        sup_nodes = None
        while True:
            token = self._peek()
            if token is None or token.kind != "op" or token.value not in ("_", "^"):
                break
            op = self._advance().value
            script_nodes = self._read_script_nodes()
            if op == "_":
                sub_nodes = script_nodes
            else:
                sup_nodes = script_nodes

        if sub_nodes is None and sup_nodes is None:
            return base

        if sub_nodes is not None and sup_nodes is not None:
            node = etree.Element(m_ns("sSubSup"))
            etree.SubElement(node, m_ns("e")).append(base)
            self._append_all(etree.SubElement(node, m_ns("sub")), sub_nodes)
            self._append_all(etree.SubElement(node, m_ns("sup")), sup_nodes)
            return node

        if sub_nodes is not None:
            node = etree.Element(m_ns("sSub"))
            etree.SubElement(node, m_ns("e")).append(base)
            self._append_all(etree.SubElement(node, m_ns("sub")), sub_nodes)
            return node

        node = etree.Element(m_ns("sSup"))
        etree.SubElement(node, m_ns("e")).append(base)
        self._append_all(etree.SubElement(node, m_ns("sup")), sup_nodes or [])
        return node

    def _read_group_nodes(self) -> list:
        token = self._peek()
        if token is not None and token.kind == "op" and token.value == "{":
            self._advance()
            return self._parse_until("}")
        atom = self._parse_atom()
        return [self._apply_scripts(atom)] if atom is not None else []

    def _read_script_nodes(self) -> list:
        token = self._peek()
        if token is not None and token.kind == "op" and token.value == "{":
            self._advance()
            return self._parse_until("}")
        atom = self._parse_atom()
        return [atom] if atom is not None else []

    def _read_group_text(self) -> str:
        token = self._peek()
        if token is None or token.kind != "op" or token.value != "{":
            return ""
        self._advance()
        depth = 1
        parts = []
        while self.pos < len(self.tokens) and depth:
            token = self._advance()
            if token.kind == "op" and token.value == "{":
                depth += 1
                parts.append(token.value)
            elif token.kind == "op" and token.value == "}":
                depth -= 1
                if depth:
                    parts.append(token.value)
            elif token.kind == "cmd" and token.value in SYMBOLS:
                parts.append(SYMBOLS[token.value])
            else:
                parts.append(token.value)
        return "".join(parts)

    def _run(self, text: str):
        run = etree.Element(m_ns("r"))
        t = etree.SubElement(run, m_ns("t"))
        t.text = text
        return run

    def _append_all(self, parent, nodes: list) -> None:
        for node in nodes:
            parent.append(node)
