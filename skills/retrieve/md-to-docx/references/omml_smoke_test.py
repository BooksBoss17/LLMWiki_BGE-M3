from __future__ import annotations

from lxml import etree

from latex_to_omml import M, LatexToOMML


def count(xml, local_name: str) -> int:
    return len(xml.xpath(f".//m:{local_name}", namespaces={"m": M}))


def text(xml) -> str:
    return "".join(xml.xpath(".//m:t/text()", namespaces={"m": M}))


def main() -> None:
    converter = LatexToOMML()
    cases = {
        r"\frac{a}{b}": {"f": 1},
        r"x_{总}": {"sSub": 1},
        r"v^2": {"sSup": 1},
        r"\sqrt{LC}": {"rad": 1},
        r"\frac{B^2L^2v}{R_{\text{总}}}": {"f": 1, "sSup": 2, "sSub": 1, "contains": "B2L2vR总"},
        r"U=\frac{U_m}{\sqrt{2}}": {"f": 1, "sSub": 1, "rad": 1},
    }
    for latex, expected in cases.items():
        xml = converter.convert_inline(latex)
        for name in ("f", "sSub", "sSup", "rad"):
            if name in expected:
                actual = count(xml, name)
                assert actual == expected[name], f"{latex}: expected {name}={expected[name]}, got {actual}"
        if "contains" in expected:
            actual_text = text(xml)
            for ch in expected["contains"]:
                assert ch in actual_text, f"{latex}: missing {ch!r} in {actual_text!r}"
    print("OMML smoke tests passed")


if __name__ == "__main__":
    main()
