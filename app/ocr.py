"""OCR helper. Uses pytesseract if available; raises with a clear message otherwise."""
from __future__ import annotations

import io
import os

import fitz


def _check_tesseract():
    try:
        import pytesseract  # noqa: F401
    except ImportError as e:
        raise RuntimeError(
            "pytesseract is not installed. Run: pip install pytesseract"
        ) from e
    # The pytesseract module wraps the system 'tesseract' binary.
    # If it can't find the binary, calls below will raise.


def ocr_page(doc: fitz.Document, page_idx: int, lang: str = "eng",
             dpi: int = 300) -> str:
    """Run OCR on a single page; returns the extracted text."""
    _check_tesseract()
    import pytesseract
    from PIL import Image

    page = doc[page_idx]
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    return pytesseract.image_to_string(img, lang=lang)


def ocr_to_textlayer(doc: fitz.Document, page_idx: int, lang: str = "eng",
                     dpi: int = 300):
    """Add an invisible text layer to the page so the PDF becomes searchable."""
    _check_tesseract()
    import pytesseract
    from PIL import Image

    page = doc[page_idx]
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    img = Image.open(io.BytesIO(pix.tobytes("png")))
    data = pytesseract.image_to_data(img, lang=lang,
                                     output_type=pytesseract.Output.DICT)
    # Insert invisible (render-mode 3) text at each word's location.
    n = len(data["text"])
    for i in range(n):
        word = data["text"][i].strip()
        if not word:
            continue
        x = data["left"][i] / zoom
        y = data["top"][i] / zoom
        h = data["height"][i] / zoom
        # PyMuPDF y is from top; place baseline at y + h
        try:
            page.insert_text(
                (x, y + h * 0.9), word,
                fontname="helv", fontsize=h * 0.9,
                color=(0, 0, 0), render_mode=3,  # invisible
            )
        except Exception:
            continue
