r"""Parse legacy Microsoft Word EQ fields into portable LaTeX.

The field storage lives in OOXML ``w:fldChar``/``w:instrText``.  Instruction
semantics follow Microsoft's ``Field codes: Eq (Equation) field`` reference.
Historical physics-specific interpretations are disabled unless a caller
provides a validated, hash-bound compatibility rule.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree

from math_conversion_contract import MathConversionResult, conversion_issue


W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
EQ_STANDARD = "Microsoft Support: Field codes: Eq (Equation) field"


UNICODE_LATEX = {
    "Δ": r"\Delta ",
    "×": r"\times ",
    "·": r"\cdot ",
    "ν": r"\nu ",
    "θ": r"\theta ",
    "α": r"\alpha ",
    "β": r"\beta ",
    "μ": r"\mu ",
    "λ": r"\lambda ",
    "π": r"\pi ",
    "ρ": r"\rho ",
    "φ": r"\varphi ",
    "ε": r"\varepsilon ",
    "≤": r"\leq ",
    "≥": r"\geq ",
    "≠": r"\neq ",
    "≈": r"\approx ",
    "∞": r"\infty ",
    "°": r"^{\circ}",
    "＋": "+",
    "－": "-",
}


def _is_cjk(char: str) -> bool:
    return "\u3400" <= char <= "\u9fff"


def clean_eq_text(text: str) -> str:
    """Convert literal EQ text without inventing mathematical structure."""
    value = str(text or "").strip()
    if value.startswith("\\'"):
        value = value[2:]
    rendered: list[str] = []
    cjk: list[str] = []

    def flush_cjk() -> None:
        if cjk:
            rendered.append(r"\text{" + "".join(cjk) + "}")
            cjk.clear()

    for char in value:
        if _is_cjk(char):
            cjk.append(char)
            continue
        flush_cjk()
        rendered.append(UNICODE_LATEX.get(char, char))
    flush_cjk()
    return "".join(rendered).strip()


@dataclass
class _Fragment:
    latex: str = ""
    warnings: list[dict[str, Any]] = field(default_factory=list)
    issues: list[dict[str, Any]] = field(default_factory=list)
    status: str = "converted"
    compatibility_rules: list[str] = field(default_factory=list)

    @classmethod
    def combine(cls, fragments: list["_Fragment"], *, joiner: str = "") -> "_Fragment":
        status = "converted"
        if any(item.status == "unsupported" for item in fragments):
            status = "unsupported"
        elif any(item.status == "needs_review" for item in fragments):
            status = "needs_review"
        return cls(
            latex=joiner.join(item.latex for item in fragments),
            warnings=[warning for item in fragments for warning in item.warnings],
            issues=[issue for item in fragments for issue in item.issues],
            status=status,
            compatibility_rules=[rule for item in fragments for rule in item.compatibility_rules],
        )


class _EqParser:
    def __init__(self, source: str, *, compatibility_rule: str | None = None):
        self.source = source
        self.pos = 0
        self.compatibility_rule = compatibility_rule
        self.compatibility_rule_used = False

    def parse(self) -> _Fragment:
        fragment = self._parse_expression(stops=frozenset())
        self._skip_space()
        if self.pos != len(self.source):
            return self._malformed("unexpected trailing EQ content")
        if self.compatibility_rule and not self.compatibility_rule_used:
            fragment.issues.append(
                conversion_issue(
                    "eq_compatibility_rule_not_applicable",
                    "The supplied compatibility rule does not match this EQ field.",
                    standard_reference=EQ_STANDARD,
                    details={"rule_id": self.compatibility_rule},
                )
            )
            fragment.status = "unsupported"
        return fragment

    def _parse_expression(self, *, stops: frozenset[str]) -> _Fragment:
        fragments: list[_Fragment] = []
        literal: list[str] = []
        literal_parentheses = 0

        def flush() -> None:
            if literal:
                fragments.append(_Fragment(latex=clean_eq_text("".join(literal))))
                literal.clear()

        while self.pos < len(self.source):
            char = self.source[self.pos]
            if char in stops and literal_parentheses == 0:
                break
            if char == "\\":
                flush()
                fragments.append(self._parse_command())
            else:
                literal.append(char)
                self.pos += 1
                if char == "(":
                    literal_parentheses += 1
                elif char == ")" and literal_parentheses:
                    literal_parentheses -= 1
        flush()
        return _Fragment.combine(fragments)

    def _parse_command(self) -> _Fragment:
        start = self.pos
        name = self._read_control()
        handlers = {
            "f": self._parse_fraction,
            "r": self._parse_radical,
            "s": self._parse_script,
            "x": self._parse_box,
            "o": self._parse_overstrike,
            "b": self._parse_bracket,
            "a": self._parse_array,
        }
        handler = handlers.get(name)
        if handler is None:
            return _Fragment(
                status="unsupported",
                issues=[
                    conversion_issue(
                        "eq_unsupported_instruction",
                        f"Unsupported EQ instruction: \\{name}",
                        standard_reference=EQ_STANDARD,
                        location=f"offset:{start}",
                    )
                ],
            )
        try:
            return handler()
        except ValueError as exc:
            return self._malformed(str(exc), location=f"offset:{start}")

    def _parse_fraction(self) -> _Fragment:
        args = self._parse_arguments()
        if len(args) != 2:
            raise ValueError(r"\f requires numerator and denominator")
        combined = _Fragment.combine(args)
        combined.latex = rf"\frac{{{args[0].latex}}}{{{args[1].latex}}}"
        return combined

    def _parse_radical(self) -> _Fragment:
        args = self._parse_arguments()
        if len(args) == 1:
            combined = _Fragment.combine(args)
            combined.latex = rf"\sqrt{{{args[0].latex}}}"
            return combined
        if len(args) == 2:
            combined = _Fragment.combine(args)
            combined.latex = rf"\sqrt[{args[0].latex}]{{{args[1].latex}}}"
            return combined
        raise ValueError(r"\r requires a radicand and optional root degree")

    def _parse_script(self) -> _Fragment:
        self._skip_space()
        if self._peek() == "(":
            args = self._parse_arguments()
            return _Fragment(
                status="needs_review",
                issues=[
                    conversion_issue(
                        "eq_stacked_script_not_portable",
                        r"Bare \s() stacking cannot be represented losslessly in the portable target.",
                        standard_reference=EQ_STANDARD,
                    )
                ],
                warnings=[warning for item in args for warning in item.warnings],
            )

        switch = self._read_control()
        if switch not in {"up", "do"}:
            raise ValueError(r"\s requires \upN() or \doN() in strict mode")
        displacement = self._read_optional_integer()
        args = self._parse_arguments()
        if len(args) != 1:
            raise ValueError(r"\s script switch requires exactly one element")
        result = _Fragment.combine(args)
        result.latex = ("^{" if switch == "up" else "_{") + args[0].latex + "}"
        if displacement is not None:
            result.warnings.append(
                conversion_issue(
                    "eq_vertical_displacement_ignored",
                    "EQ point displacement is layout metadata and is not encoded in portable LaTeX.",
                    standard_reference=EQ_STANDARD,
                    details={"switch": switch, "points": displacement},
                )
            )
        return result

    def _parse_box(self) -> _Fragment:
        switches: list[str] = []
        self._skip_space()
        while self._peek() == "\\" and self._peek_control() in {"to", "bo", "le", "ri"}:
            switches.append(self._read_control())
            self._skip_space()
        args = self._parse_arguments()
        if len(args) != 1:
            raise ValueError(r"\x requires exactly one element")
        child = _Fragment.combine(args)
        if switches == ["to"]:
            if self.compatibility_rule == "eq-x-top-as-vector":
                self.compatibility_rule_used = True
                child.latex = rf"\vec{{{args[0].latex}}}"
                child.compatibility_rules.append(self.compatibility_rule)
            else:
                child.latex = rf"\overline{{{args[0].latex}}}"
            return child
        if switches == ["bo"]:
            return _Fragment(
                status="needs_review",
                issues=[
                    conversion_issue(
                        "eq_bottom_border_not_portable",
                        "Bottom-border EQ boxes are not stable in the current Word export chain.",
                        standard_reference=EQ_STANDARD,
                    )
                ],
            )
        return _Fragment(
            status="needs_review",
            issues=[
                conversion_issue(
                    "eq_box_not_portable",
                    "This EQ box/border combination is not portable across Wiki and Word output.",
                    standard_reference=EQ_STANDARD,
                    details={"switches": switches},
                )
            ],
        )

    def _parse_overstrike(self) -> _Fragment:
        alignment = "ac"
        self._skip_space()
        if self._peek() == "\\" and self._peek_control() in {"al", "ac", "ar"}:
            alignment = self._read_control()
        args = self._parse_arguments()
        if self.compatibility_rule == "eq-overstrike-as-nuclear-scripts" and len(args) == 2:
            self.compatibility_rule_used = True
            combined = _Fragment.combine(args)
            combined.latex = rf"^{{{args[0].latex}}}_{{{args[1].latex}}}"
            combined.compatibility_rules.append(self.compatibility_rule)
            return combined
        return _Fragment(
            status="needs_review",
            issues=[
                conversion_issue(
                    "eq_overstrike_not_portable",
                    "EQ overstrike is not equivalent to scripts and has no portable lossless target.",
                    standard_reference=EQ_STANDARD,
                    details={"alignment": alignment, "element_count": len(args)},
                )
            ],
        )

    def _parse_bracket(self) -> _Fragment:
        left = "("
        right = ")"
        self._skip_space()
        while self._peek() == "\\" and self._peek_control() in {"lc", "rc", "bc"}:
            switch = self._read_control()
            value = self._read_switch_character()
            if switch == "lc":
                left = value
            elif switch == "rc":
                right = value
            else:
                left = value
                right = {"(": ")", "[": "]", "{": "}", "<": ">"}.get(value, value)
            self._skip_space()
        args = self._parse_arguments()
        body = _Fragment.combine(args, joiner=",")
        body.latex = rf"\left{_latex_delimiter(left)}{body.latex}\right{_latex_delimiter(right)}"
        return body

    def _parse_array(self) -> _Fragment:
        columns = 1
        alignment = "ac"
        vertical_spacing: int | None = None
        self._skip_space()
        while self._peek() == "\\" and self._peek_control() in {"al", "ac", "ar", "co", "vs", "hs"}:
            switch = self._read_control()
            if switch in {"al", "ac", "ar"}:
                alignment = switch
            elif switch == "co":
                columns = self._read_optional_integer(required=True)
            elif switch in {"vs", "hs"}:
                value = self._read_optional_integer(required=True)
                if switch == "vs":
                    vertical_spacing = value
            self._skip_space()
        args = self._parse_arguments()
        if columns == 1 and len(args) == 1:
            result = _Fragment.combine(args)
            if vertical_spacing is not None:
                result.warnings.append(
                    conversion_issue(
                        "eq_array_spacing_ignored",
                        "EQ array spacing is layout metadata and is not encoded in portable LaTeX.",
                        standard_reference=EQ_STANDARD,
                        details={"alignment": alignment, "vertical_spacing": vertical_spacing},
                    )
                )
            return result
        return _Fragment(
            status="needs_review",
            issues=[
                conversion_issue(
                    "eq_array_not_portable",
                    "Multi-item or multi-column EQ arrays require visual review.",
                    standard_reference=EQ_STANDARD,
                    details={"columns": columns, "element_count": len(args), "alignment": alignment},
                )
            ],
        )

    def _parse_arguments(self) -> list[_Fragment]:
        self._skip_space()
        if self._peek() != "(":
            raise ValueError("expected '('")
        self.pos += 1
        args: list[_Fragment] = []
        while True:
            args.append(self._parse_expression(stops=frozenset({",", ")"})))
            if self.pos >= len(self.source):
                raise ValueError("unclosed EQ argument list")
            if self.source[self.pos] == ",":
                self.pos += 1
                continue
            self.pos += 1
            break
        return args

    def _read_control(self) -> str:
        if self._peek() != "\\":
            raise ValueError("expected EQ control word")
        self.pos += 1
        start = self.pos
        while self.pos < len(self.source) and self.source[self.pos].isalpha():
            self.pos += 1
        if self.pos == start:
            if self.pos >= len(self.source):
                raise ValueError("trailing backslash in EQ field")
            self.pos += 1
        return self.source[start:self.pos]

    def _peek_control(self) -> str:
        saved = self.pos
        try:
            return self._read_control()
        except ValueError:
            return ""
        finally:
            self.pos = saved

    def _read_switch_character(self) -> str:
        self._skip_space()
        if self._peek() == "\\":
            self.pos += 1
        if self.pos >= len(self.source):
            raise ValueError("missing EQ bracket character")
        value = self.source[self.pos]
        self.pos += 1
        return value

    def _read_optional_integer(self, *, required: bool = False) -> int | None:
        self._skip_space()
        start = self.pos
        if self._peek() in {"+", "-"}:
            self.pos += 1
        while self.pos < len(self.source) and self.source[self.pos].isdigit():
            self.pos += 1
        token = self.source[start:self.pos]
        if not token or token in {"+", "-"}:
            if required:
                raise ValueError("missing numeric EQ switch value")
            self.pos = start
            return None
        return int(token)

    def _skip_space(self) -> None:
        while self.pos < len(self.source) and self.source[self.pos].isspace():
            self.pos += 1

    def _peek(self) -> str:
        return self.source[self.pos] if self.pos < len(self.source) else ""

    @staticmethod
    def _malformed(message: str, *, location: str | None = None) -> _Fragment:
        return _Fragment(
            status="unsupported",
            issues=[
                conversion_issue(
                    "eq_malformed_instruction",
                    message,
                    standard_reference=EQ_STANDARD,
                    location=location,
                )
            ],
        )


def _latex_delimiter(value: str) -> str:
    return {"{": r"\{", "}": r"\}", "<": r"\langle", ">": r"\rangle"}.get(value, value)


def _normalize_latex_spacing(value: str) -> str:
    value = re.sub(r"\s+}", "}", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def convert_eq_field(instr: str, *, compatibility_rule: str | None = None) -> MathConversionResult:
    """Return a structured conversion result for one EQ field instruction."""
    raw = str(instr or "").strip()
    match = re.match(r"(?is)^eq\b(.*)$", raw)
    if not match:
        return MathConversionResult(
            status="unsupported",
            source_kind="eq",
            latex=None,
            standard_reference=EQ_STANDARD,
            unsupported_constructs=[
                conversion_issue(
                    "not_eq_field",
                    "The field instruction does not begin with EQ.",
                    standard_reference=EQ_STANDARD,
                )
            ],
        )
    body = match.group(1).strip()
    if not body:
        return MathConversionResult(
            status="unsupported",
            source_kind="eq",
            latex=None,
            standard_reference=EQ_STANDARD,
            unsupported_constructs=[
                conversion_issue(
                    "eq_empty_instruction",
                    "The EQ field has no instruction body.",
                    standard_reference=EQ_STANDARD,
                )
            ],
        )
    fragment = _EqParser(body, compatibility_rule=compatibility_rule).parse()
    latex = _normalize_latex_spacing(fragment.latex) if fragment.status == "converted" else None
    return MathConversionResult(
        status=fragment.status,
        source_kind="eq",
        latex=latex,
        standard_reference=EQ_STANDARD,
        warnings=fragment.warnings,
        unsupported_constructs=fragment.issues,
        compatibility_rules=fragment.compatibility_rules,
    )


def parse_eq_field(instr: str, *, compatibility_rule: str | None = None) -> str | None:
    """Compatibility API: return LaTeX, return None for non-EQ, and block unsafe EQ."""
    result = convert_eq_field(instr, compatibility_rule=compatibility_rule)
    if any(item.get("code") == "not_eq_field" for item in result.unsupported_constructs):
        return None
    return result.require_latex()


def parse_nested_eq(text: str) -> str:
    result = convert_eq_field("eq " + str(text or "").strip())
    return result.require_latex()


def parse_fractions_in_text(text: str) -> str:
    parser = _EqParser(str(text or ""))
    return parser.parse().latex


def extract_field_results(docx_path: str | Path) -> list[tuple[str, MathConversionResult]]:
    """Extract every EQ field and retain blocked results for auditing."""
    with zipfile.ZipFile(docx_path, "r") as archive:
        doc_xml = archive.read("word/document.xml")
    tree = etree.fromstring(doc_xml)
    results: list[tuple[str, MathConversionResult]] = []
    in_field = False
    current_instr: list[str] = []
    for elem in tree.iter():
        tag = str(elem.tag).split("}", 1)[-1]
        if tag == "fldChar":
            field_type = elem.get(f"{{{W}}}fldCharType", "")
            if field_type == "begin":
                in_field = True
                current_instr = []
            elif field_type == "end":
                if in_field and current_instr:
                    instruction = "".join(current_instr).strip()
                    if re.match(r"(?i)^eq\b", instruction):
                        results.append((instruction, convert_eq_field(instruction)))
                in_field = False
                current_instr = []
        elif tag == "instrText" and in_field:
            current_instr.append(elem.text or "")
    return results


def extract_field_codes(docx_path: str | Path) -> list[tuple[str, str]]:
    return [(instruction, result.require_latex()) for instruction, result in extract_field_results(docx_path)]


if __name__ == "__main__":
    import sys

    if len(sys.argv) <= 1:
        raise SystemExit("Usage: python eq_field_to_latex.py <input.docx>")
    fields = extract_field_results(sys.argv[1])
    print(f"Found {len(fields)} EQ field codes:\n")
    blocked = 0
    for index, (instruction, result) in enumerate(fields, start=1):
        print(f"  [{index}] {instruction}")
        if result.status == "converted":
            print(f"       -> ${result.latex}$")
        else:
            blocked += 1
            codes = ", ".join(item["code"] for item in result.unsupported_constructs)
            print(f"       !! {result.status}: {codes}")
    raise SystemExit(1 if blocked else 0)
