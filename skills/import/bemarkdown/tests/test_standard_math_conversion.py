from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from lxml import etree


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from eq_field_to_latex import convert_eq_field, parse_eq_field  # noqa: E402
from math_conversion_contract import (  # noqa: E402
    HashBoundCompatibilityOverrides,
    MathConversionError,
)
from omml_to_latex import convert_omml, omml_to_latex  # noqa: E402
from omml_docx_to_md import DocumentMathConversionBlocked, convert_docx_to_md  # noqa: E402


M = "http://schemas.openxmlformats.org/officeDocument/2006/math"


def omml(xml: str):
    return etree.fromstring(xml.format(M=M).encode("utf-8"))


class OmmlStandardConversionTests(unittest.TestCase):
    def test_fraction_radical_and_scripts_follow_omml_structure(self) -> None:
        formula = omml(
            '<m:oMath xmlns:m="{M}">'
            '<m:f><m:num><m:r><m:t>1</m:t></m:r></m:num>'
            '<m:den><m:rad><m:radPr/><m:deg><m:r><m:t>3</m:t></m:r></m:deg>'
            '<m:e><m:sSup><m:e><m:r><m:t>x</m:t></m:r></m:e>'
            '<m:sup><m:r><m:t>2</m:t></m:r></m:sup></m:sSup></m:e>'
            '</m:rad></m:den></m:f></m:oMath>'
        )
        result = convert_omml(formula)
        self.assertEqual(result.status, "converted")
        self.assertEqual(result.latex, r"\frac{1}{\sqrt[3]{x^{2}}}")

    def test_delimiter_uses_all_elements_and_declared_separator(self) -> None:
        formula = omml(
            '<m:d xmlns:m="{M}"><m:dPr><m:begChr m:val="["/>'
            '<m:endChr m:val="]"/><m:sepChr m:val=";"/></m:dPr>'
            '<m:e><m:r><m:t>a</m:t></m:r></m:e>'
            '<m:e><m:r><m:t>b</m:t></m:r></m:e></m:d>'
        )
        self.assertEqual(convert_omml(formula).latex, r"\left[a;b\right]")

    def test_normal_run_style_is_preserved_as_mathrm(self) -> None:
        formula = omml(
            '<m:r xmlns:m="{M}"><m:rPr><m:nor/></m:rPr><m:t>sin</m:t></m:r>'
        )
        self.assertEqual(convert_omml(formula).latex, r"\mathrm{sin}")

    def test_nonportable_observed_nodes_require_review(self) -> None:
        for xml, code in (
            ('<m:phant xmlns:m="{M}"><m:e><m:r><m:t>x</m:t></m:r></m:e></m:phant>', "omml_phantom_not_portable"),
            ('<m:eqArr xmlns:m="{M}"><m:e><m:r><m:t>x=1</m:t></m:r></m:e></m:eqArr>', "omml_equation_array_not_portable"),
        ):
            result = convert_omml(omml(xml))
            self.assertEqual(result.status, "needs_review")
            self.assertIn(code, {item["code"] for item in result.unsupported_constructs})

    def test_unknown_math_node_never_flattens_silently(self) -> None:
        formula = omml(
            '<m:oMath xmlns:m="{M}"><m:futureMath><m:r><m:t>x</m:t></m:r>'
            '</m:futureMath></m:oMath>'
        )
        result = convert_omml(formula)
        self.assertEqual(result.status, "unsupported")
        self.assertIsNone(result.latex)
        with self.assertRaises(MathConversionError):
            omml_to_latex(formula)


class EqStandardConversionTests(unittest.TestCase):
    def test_fraction_and_indexed_radical(self) -> None:
        self.assertEqual(parse_eq_field(r"eq \f(1,2)"), r"\frac{1}{2}")
        self.assertEqual(parse_eq_field(r"eq \r(3,x)"), r"\sqrt[3]{x}")

    def test_literal_parentheses_inside_fraction_are_not_argument_delimiters(self) -> None:
        self.assertEqual(
            parse_eq_field(r"eq \f((m＋m0)g,2NIL)"),
            r"\frac{(m+m0)g}{2NIL}",
        )

    def test_superscript_displacement_is_parsed_and_reported(self) -> None:
        result = convert_eq_field(r"eq \s\up12(2)")
        self.assertEqual(result.status, "converted")
        self.assertEqual(result.latex, "^{2}")
        self.assertIn("eq_vertical_displacement_ignored", {item["code"] for item in result.warnings})

    def test_top_border_is_overline_not_vector(self) -> None:
        self.assertEqual(parse_eq_field(r"eq \x\to(v)"), r"\overline{v}")

    def test_overstrike_does_not_masquerade_as_nuclear_scripts(self) -> None:
        result = convert_eq_field(r"eq \o\al(232,90)")
        self.assertEqual(result.status, "needs_review")
        self.assertIsNone(result.latex)
        with self.assertRaises(MathConversionError):
            parse_eq_field(r"eq \o\al(232,90)")

    def test_bracket_array_and_nested_fraction_are_parsed(self) -> None:
        result = convert_eq_field(r"eq \b\lc\|\rc\|(\a\vs4\al\co1(\f(ΔU,ΔI)))")
        self.assertEqual(result.status, "converted")
        self.assertEqual(result.latex, r"\left|\frac{\Delta U}{\Delta I}\right|")

    def test_boxed_text_requires_review_for_portable_word_output(self) -> None:
        result = convert_eq_field(r"eq \x(光照强度)")
        self.assertEqual(result.status, "needs_review")
        self.assertIn("eq_box_not_portable", {item["code"] for item in result.unsupported_constructs})

    def test_unknown_and_malformed_eq_are_explicitly_unsupported(self) -> None:
        for instruction in (r"eq \i(0,1,x)", r"eq \f(1,2"):
            result = convert_eq_field(instruction)
            self.assertEqual(result.status, "unsupported")
            self.assertIsNone(result.latex)


class HashBoundCompatibilityTests(unittest.TestCase):
    def test_override_requires_matching_source_and_instruction_hashes(self) -> None:
        instruction = r"eq \x\to(v)"
        source_sha = "a" * 64
        payload = {
            "schema_version": 1,
            "source_sha256": source_sha,
            "overrides": [
                {
                    "source_kind": "eq",
                    "formula_index": 5,
                    "instruction_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
                    "rule_id": "eq-x-top-as-vector",
                    "visual_evidence": "Word rendering and source context show a vector arrow over v.",
                }
            ],
        }
        overrides = HashBoundCompatibilityOverrides.from_payload(payload, source_sha256=source_sha)
        self.assertEqual(overrides.rule_for(5, instruction), "eq-x-top-as-vector")
        self.assertIsNone(overrides.rule_for(4, instruction))
        self.assertIsNone(overrides.rule_for(5, r"eq \x\to(a)"))
        with self.assertRaises(ValueError):
            HashBoundCompatibilityOverrides.from_payload(payload, source_sha256="b" * 64)

    def test_validated_legacy_rule_is_reported(self) -> None:
        result = convert_eq_field(r"eq \x\to(v)", compatibility_rule="eq-x-top-as-vector")
        self.assertEqual(result.status, "converted")
        self.assertEqual(result.latex, r"\vec{v}")
        self.assertEqual(result.compatibility_rules, ["eq-x-top-as-vector"])


class DocxMathGateTests(unittest.TestCase):
    @staticmethod
    def _write_docx(path: Path, body: str) -> None:
        document = (
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            f'xmlns:m="{M}"><w:body>{body}<w:sectPr/></w:body></w:document>'
        )
        relationships = (
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>'
        )
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("word/document.xml", document.encode("utf-8"))
            archive.writestr("word/_rels/document.xml.rels", relationships.encode("utf-8"))

    def test_success_writes_markdown_and_structured_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "simple.docx"
            output = root / "out"
            self._write_docx(
                source,
                '<w:p><m:oMath><m:f><m:num><m:r><m:t>1</m:t></m:r></m:num>'
                '<m:den><m:r><m:t>2</m:t></m:r></m:den></m:f></m:oMath></w:p>',
            )
            md_path, formula_count, _ = convert_docx_to_md(source, output)
            self.assertEqual(formula_count, 1)
            self.assertIn(r"\frac{1}{2}", Path(md_path).read_text(encoding="utf-8"))
            report = json.loads((output / "formula_conversion_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "converted")
            self.assertEqual(report["summary"]["converted"], 1)

    def test_blocked_formula_writes_report_but_no_final_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "blocked.docx"
            output = root / "out"
            self._write_docx(
                source,
                '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText>eq \\o\\al(232,90)</w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>',
            )
            with self.assertRaises(DocumentMathConversionBlocked):
                convert_docx_to_md(source, output)
            self.assertFalse((output / "blocked.md").exists())
            report = json.loads((output / "formula_conversion_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["status"], "blocked")
            self.assertEqual(report["summary"]["needs_review"], 1)

    def test_incomplete_field_markers_are_not_silently_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "incomplete.docx"
            output = root / "out"
            self._write_docx(
                source,
                '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText>eq \\f(1,2)</w:instrText></w:r></w:p>',
            )
            with self.assertRaises(DocumentMathConversionBlocked):
                convert_docx_to_md(source, output)
            report = json.loads((output / "formula_conversion_report.json").read_text(encoding="utf-8"))
            codes = {
                issue["code"]
                for formula in report["formulas"]
                for issue in formula["unsupported_constructs"]
            }
            self.assertIn("eq_incomplete_field", codes)

    def test_nested_eq_field_is_counted_without_treating_outer_field_as_math(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "nested.docx"
            output = root / "out"
            self._write_docx(
                source,
                '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText>IF </w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                '<w:r><w:instrText>eq \\f(1,2)</w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r>'
                '<w:r><w:instrText> = 0</w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>',
            )
            md_path, formula_count, _ = convert_docx_to_md(source, output)
            self.assertEqual(formula_count, 1)
            self.assertIn(r"\frac{1}{2}", Path(md_path).read_text(encoding="utf-8"))

    def test_docx_conversion_applies_only_hash_bound_reviewed_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "legacy.docx"
            output = root / "out"
            instruction = r"eq \x\to(v)"
            self._write_docx(
                source,
                '<w:p><w:r><w:fldChar w:fldCharType="begin"/></w:r>'
                f'<w:r><w:instrText>{instruction}</w:instrText></w:r>'
                '<w:r><w:fldChar w:fldCharType="end"/></w:r></w:p>',
            )
            source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
            overrides = {
                "schema_version": 1,
                "source_sha256": source_sha,
                "overrides": [
                    {
                        "source_kind": "eq",
                        "formula_index": 1,
                        "instruction_sha256": hashlib.sha256(instruction.encode("utf-8")).hexdigest(),
                        "rule_id": "eq-x-top-as-vector",
                        "visual_evidence": "Reviewed source rendering shows an arrow over v.",
                    }
                ],
            }
            md_path, _, _ = convert_docx_to_md(source, output, compatibility_overrides=overrides)
            self.assertIn(r"\vec{v}", Path(md_path).read_text(encoding="utf-8"))
            report = json.loads((output / "formula_conversion_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["formulas"][0]["compatibility_rules"], ["eq-x-top-as-vector"])


if __name__ == "__main__":
    unittest.main()
