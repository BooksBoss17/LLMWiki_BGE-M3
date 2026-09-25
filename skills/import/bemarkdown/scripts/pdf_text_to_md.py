"""Convert a text-based PDF to Markdown using page rendering + VLM transcription.

For text-based PDFs where formulas are fragmented (Word-exported PDFs),
direct text extraction produces garbage. Instead:
1. Render each page as high-res image (3x)
2. VLM transcribes each page to text + LaTeX
3. Merge into single MD file

For pages with circuit diagrams: VLM describes them, we keep image refs.
"""
import pymupdf
import os
import re
import time

def convert_text_pdf_to_md(pdf_path, output_dir, scale=3):
    """Convert a text-based PDF to Markdown via page rendering + VLM.
    
    Args:
        pdf_path: Path to the PDF file
        output_dir: Output directory
        scale: Render scale (3 = 3x zoom for better OCR)
    
    Returns:
        Path to the output MD file
    """
    os.makedirs(output_dir, exist_ok=True)
    
    doc = pymupdf.open(pdf_path)
    total_pages = len(doc)
    
    # Step 1: Render all pages as images
    print(f"Step 1: Render {total_pages} pages at {scale}x...")
    page_images = []
    for i in range(total_pages):
        page = doc[i]
        mat = pymupdf.Matrix(scale, scale)
        pix = page.get_pixmap(matrix=mat)
        img_path = os.path.join(output_dir, f"page_{i+1}.png")
        pix.save(img_path)
        page_images.append(img_path)
        print(f"  Page {i+1}: {pix.width}x{pix.height}")
    
    # Step 2: VLM transcription (done by caller via vision_analyze)
    # This function just renders; the actual VLM calls happen in the main script
    return page_images


if __name__ == '__main__':
    import sys
    if len(sys.argv) < 3:
        raise SystemExit('Usage: python pdf_text_to_md.py <input.pdf> <output_dir> [scale]')
    pdf_path = sys.argv[1]
    output_dir = sys.argv[2]
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    pages = convert_text_pdf_to_md(pdf_path, output_dir, scale=scale)
    print(f"\nRendered {len(pages)} pages {output_dir}")
    print("Next: use vision_analyze on VLM transcription")
