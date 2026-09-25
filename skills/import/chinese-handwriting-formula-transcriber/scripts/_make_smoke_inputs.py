import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def choose_font(size: int):
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\arial.ttf",
    ]
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    font_text = choose_font(42)
    font_formula = choose_font(52)

    text_img = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(text_img)
    draw.text((32, 40), "\u7269\u7406\u7b54\u9898\uff1a\u7531\u725b\u987f\u7b2c\u4e8c\u5b9a\u5f8b\u5f97 F=ma", fill="black", font=font_text)
    text_img.save(out_dir / "synthetic_chinese_text.png")

    formula_img = Image.new("RGB", (900, 260), "white")
    draw = ImageDraw.Draw(formula_img)
    draw.text((48, 72), r"v = v0 + at,  Ek = 1/2 mv^2", fill="black", font=font_formula)
    formula_img.save(out_dir / "synthetic_formula.png")

    page_img = Image.new("RGB", (1200, 900), "white")
    draw = ImageDraw.Draw(page_img)
    draw.rectangle((35, 35, 1165, 865), outline="black", width=3)
    draw.text((80, 80), "\u624b\u5199\u4e2d\u6587\u548c\u7269\u7406\u516c\u5f0f\u6d4b\u8bd5", fill="black", font=font_text)
    draw.text((80, 180), r"F = ma", fill="black", font=font_formula)
    draw.text((80, 290), r"v^2 - v0^2 = 2ax", fill="black", font=font_formula)
    draw.text((80, 410), "\u56e0\u6b64\u52a0\u901f\u5ea6\u65b9\u5411\u5411\u53f3", fill="black", font=font_text)
    page_img.save(out_dir / "synthetic_page.png")

    print(out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
