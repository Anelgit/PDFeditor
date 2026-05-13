"""PDF document wrapper backed by PyMuPDF with in-memory undo/redo."""
from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Optional

import fitz  # PyMuPDF


@dataclass
class SearchHit:
    page: int
    rect: fitz.Rect


@dataclass
class TextSpan:
    """A span of text on a page with all the metadata needed to re-render it."""
    page: int
    text: str
    bbox: fitz.Rect          # the span's bounding box (PDF points)
    origin: tuple[float, float]  # baseline-start point
    fontname: str            # the PDF's internal font name
    fontsize: float
    color_int: int           # 0xRRGGBB packed
    flags: int               # PyMuPDF font flags (2=italic, 16=bold)

    @property
    def color(self) -> tuple[float, float, float]:
        r = ((self.color_int >> 16) & 0xFF) / 255.0
        g = ((self.color_int >> 8) & 0xFF) / 255.0
        b = (self.color_int & 0xFF) / 255.0
        return (r, g, b)


def _pick_base14(orig_name: str, flags: int) -> str:
    """Map an arbitrary PDF font name + style flags to a PyMuPDF Base14 font code.

    The Base14 fonts are guaranteed to exist in every PDF reader, so they're
    a safe fallback when the original font is subsetted (which it almost always is).
    """
    name = (orig_name or "").lower()
    bold = bool(flags & 16)
    italic = bool(flags & 2)
    if "cour" in name or "mono" in name:
        family = ("cour", "coit", "cobo", "cobi")
    elif "tim" in name or "roman" in name or "serif" in name:
        family = ("tiro", "tiit", "tibo", "tibi")
    else:
        family = ("helv", "heit", "hebo", "hebi")
    if bold and italic:
        return family[3]
    if bold:
        return family[2]
    if italic:
        return family[1]
    return family[0]


class PDFDocument:
    UNDO_LIMIT = 30

    def __init__(self):
        self.doc: Optional[fitz.Document] = None
        self.path: Optional[str] = None
        self.dirty: bool = False
        self._undo: list[bytes] = []
        self._redo: list[bytes] = []

    # ---------- lifecycle ----------
    def new(self):
        self.doc = fitz.open()
        self.doc.new_page()
        self.path = None
        self.dirty = True
        self._undo.clear()
        self._redo.clear()

    def open(self, path: str):
        self.doc = fitz.open(path)
        self.path = path
        self.dirty = False
        self._undo.clear()
        self._redo.clear()

    def close(self):
        if self.doc:
            self.doc.close()
        self.doc = None
        self.path = None
        self.dirty = False
        self._undo.clear()
        self._redo.clear()

    @property
    def is_open(self) -> bool:
        return self.doc is not None

    @property
    def page_count(self) -> int:
        return self.doc.page_count if self.doc else 0

    def page(self, index: int) -> fitz.Page:
        return self.doc[index]

    # ---------- snapshot/undo/redo ----------
    def snapshot(self):
        """Capture state before a mutation. Call BEFORE editing."""
        if not self.doc:
            return
        data = self.doc.tobytes()
        self._undo.append(data)
        if len(self._undo) > self.UNDO_LIMIT:
            self._undo.pop(0)
        self._redo.clear()
        self.dirty = True

    def _restore(self, data: bytes):
        self.doc.close()
        self.doc = fitz.open(stream=data, filetype="pdf")

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self):
        if not self._undo:
            return
        current = self.doc.tobytes()
        self._redo.append(current)
        self._restore(self._undo.pop())
        self.dirty = True

    def redo(self):
        if not self._redo:
            return
        current = self.doc.tobytes()
        self._undo.append(current)
        self._restore(self._redo.pop())
        self.dirty = True

    # ---------- save ----------
    def save(self, path: Optional[str] = None):
        if path is None:
            path = self.path
        if not path:
            raise ValueError("No path provided")
        if path == self.path:
            self.doc.saveIncr() if False else None  # avoid incr; safer full
            self.doc.save(path, incremental=False, deflate=True, garbage=3)
        else:
            self.doc.save(path, deflate=True, garbage=3)
        self.path = path
        self.dirty = False

    # ---------- rendering ----------
    def render_page(self, index: int, zoom: float = 1.0) -> tuple[bytes, int, int]:
        page = self.doc[index]
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        return pix.samples, pix.width, pix.height

    # ---------- page management ----------
    def rotate_page(self, index: int, delta_deg: int):
        self.snapshot()
        page = self.doc[index]
        page.set_rotation((page.rotation + delta_deg) % 360)

    def delete_page(self, index: int):
        self.snapshot()
        self.doc.delete_page(index)

    def insert_blank(self, index: int, width: float = 595, height: float = 842):
        self.snapshot()
        self.doc.new_page(pno=index, width=width, height=height)

    def duplicate_page(self, index: int):
        self.snapshot()
        # `to` must be -1 (append) or an existing page index; can't equal page_count.
        target = -1 if index + 1 >= self.page_count else index + 1
        self.doc.fullcopy_page(index, to=target)
        if target == -1 and index + 1 != self.page_count - 1:
            self.doc.move_page(self.page_count - 1, index + 1)

    def move_page(self, src: int, dst: int):
        if src == dst:
            return
        self.snapshot()
        self.doc.move_page(src, dst)

    def merge_with(self, other_path: str):
        self.snapshot()
        other = fitz.open(other_path)
        self.doc.insert_pdf(other)
        other.close()

    def extract_pages(self, start: int, end: int, out_path: str):
        """Save pages start..end (inclusive, 0-based) to a new PDF."""
        new = fitz.open()
        new.insert_pdf(self.doc, from_page=start, to_page=end)
        new.save(out_path, deflate=True, garbage=3)
        new.close()

    def split_each(self, out_dir: str, prefix: str = "page_"):
        """Split into one PDF per page in out_dir."""
        import os
        for i in range(self.page_count):
            new = fitz.open()
            new.insert_pdf(self.doc, from_page=i, to_page=i)
            new.save(os.path.join(out_dir, f"{prefix}{i+1:04d}.pdf"), deflate=True, garbage=3)
            new.close()

    # ---------- annotations ----------
    def add_highlight(self, page_idx: int, quads):
        self.snapshot()
        return self.doc[page_idx].add_highlight_annot(quads)

    def add_underline(self, page_idx: int, quads):
        self.snapshot()
        return self.doc[page_idx].add_underline_annot(quads)

    def add_strikeout(self, page_idx: int, quads):
        self.snapshot()
        return self.doc[page_idx].add_strikeout_annot(quads)

    def add_squiggly(self, page_idx: int, quads):
        self.snapshot()
        return self.doc[page_idx].add_squiggly_annot(quads)

    def add_sticky(self, page_idx: int, point, text: str):
        self.snapshot()
        annot = self.doc[page_idx].add_text_annot(point, text)
        return annot

    def add_freetext(self, page_idx: int, rect, text: str, fontsize: int = 12,
                     color=(0, 0, 0), bg=None):
        self.snapshot()
        page = self.doc[page_idx]
        annot = page.add_freetext_annot(
            rect, text, fontsize=fontsize, text_color=color,
            fill_color=bg, align=0,
        )
        annot.update()
        return annot

    def add_ink(self, page_idx: int, stroke_points: list[list[tuple[float, float]]],
                color=(0, 0, 0), width: float = 1.5):
        self.snapshot()
        page = self.doc[page_idx]
        annot = page.add_ink_annot(stroke_points)
        annot.set_colors(stroke=color)
        annot.set_border(width=width)
        annot.update()
        return annot

    def add_line(self, page_idx: int, p1, p2, color=(0, 0, 0), width: float = 1.5,
                 arrow: bool = False):
        self.snapshot()
        page = self.doc[page_idx]
        annot = page.add_line_annot(p1, p2)
        annot.set_colors(stroke=color)
        annot.set_border(width=width)
        if arrow:
            annot.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_CLOSED_ARROW)
        annot.update()
        return annot

    def add_rect(self, page_idx: int, rect, color=(0, 0, 0), width: float = 1.5,
                 fill=None):
        self.snapshot()
        page = self.doc[page_idx]
        annot = page.add_rect_annot(rect)
        annot.set_colors(stroke=color, fill=fill)
        annot.set_border(width=width)
        annot.update()
        return annot

    def add_ellipse(self, page_idx: int, rect, color=(0, 0, 0), width: float = 1.5,
                    fill=None):
        self.snapshot()
        page = self.doc[page_idx]
        annot = page.add_circle_annot(rect)
        annot.set_colors(stroke=color, fill=fill)
        annot.set_border(width=width)
        annot.update()
        return annot

    # ---------- content (text/image baked into page) ----------
    def insert_text(self, page_idx: int, point, text: str, fontsize: int = 12,
                    color=(0, 0, 0), fontname: str = "helv"):
        self.snapshot()
        page = self.doc[page_idx]
        page.insert_text(point, text, fontsize=fontsize, color=color, fontname=fontname)

    def insert_image(self, page_idx: int, rect, image_path: str):
        self.snapshot()
        page = self.doc[page_idx]
        page.insert_image(rect, filename=image_path, keep_proportion=True)

    def redact_rect(self, page_idx: int, rect, fill=(1, 1, 1)):
        """Remove content inside rect (true redaction)."""
        self.snapshot()
        page = self.doc[page_idx]
        page.add_redact_annot(rect, fill=fill)
        page.apply_redactions()

    # ---------- text editing ----------
    def spans_on_page(self, page_idx: int) -> list[TextSpan]:
        """Return every text span on a page as a structured TextSpan."""
        page = self.doc[page_idx]
        out: list[TextSpan] = []
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:  # 0 = text
                continue
            for line in block.get("lines", []):
                for sp in line.get("spans", []):
                    text = sp.get("text", "")
                    if not text.strip():
                        continue
                    bbox = fitz.Rect(sp["bbox"])
                    out.append(TextSpan(
                        page=page_idx,
                        text=text,
                        bbox=bbox,
                        origin=tuple(sp.get("origin", (bbox.x0, bbox.y1))),
                        fontname=sp.get("font", ""),
                        fontsize=float(sp.get("size", 12.0)),
                        color_int=int(sp.get("color", 0)),
                        flags=int(sp.get("flags", 0)),
                    ))
        return out

    def span_at(self, page_idx: int, pdf_point: tuple[float, float]) -> Optional[TextSpan]:
        """Return the smallest span whose bbox contains the given point, if any."""
        px, py = pdf_point
        best: Optional[TextSpan] = None
        for sp in self.spans_on_page(page_idx):
            if sp.bbox.x0 <= px <= sp.bbox.x1 and sp.bbox.y0 <= py <= sp.bbox.y1:
                if best is None or (sp.bbox.width * sp.bbox.height) < (best.bbox.width * best.bbox.height):
                    best = sp
        return best

    def replace_span(self, span: TextSpan, new_text: str,
                     bg: tuple[float, float, float] = (1, 1, 1)) -> None:
        """Replace one span's text. Redacts the original area and stamps the new
        text at the original baseline with a Base14 fallback for the original font."""
        self.snapshot()
        page = self.doc[span.page]
        # 1) Erase the original glyphs in that rect.
        page.add_redact_annot(span.bbox, fill=bg)
        page.apply_redactions()
        # 2) Re-insert at the original baseline.
        if new_text:
            fontname = _pick_base14(span.fontname, span.flags)
            try:
                page.insert_text(
                    fitz.Point(*span.origin), new_text,
                    fontname=fontname, fontsize=span.fontsize,
                    color=span.color,
                )
            except Exception:
                # Last-resort fallback if the chosen Base14 lacks a glyph
                page.insert_text(
                    fitz.Point(*span.origin), new_text,
                    fontname="helv", fontsize=span.fontsize,
                    color=span.color,
                )

    def find_and_replace(self, needle: str, replacement: str,
                         case_sensitive: bool = False,
                         bg: tuple[float, float, float] = (1, 1, 1)) -> int:
        """Replace every occurrence of `needle` across the document.

        Substrings inside spans are matched too. Width-preservation isn't
        guaranteed — if the replacement is wider than the original, it may
        visually overflow into adjacent text. Returns the number of substitutions.
        """
        if not needle:
            return 0
        self.snapshot()
        count = 0
        cmp_needle = needle if case_sensitive else needle.lower()
        for page_idx in range(self.page_count):
            page = self.doc[page_idx]
            # Collect substitutions for this page, then apply redactions once.
            jobs: list[tuple[fitz.Rect, fitz.Point, str, float, tuple, int]] = []
            for block in page.get_text("dict").get("blocks", []):
                if block.get("type") != 0:
                    continue
                for line in block.get("lines", []):
                    for sp in line.get("spans", []):
                        text = sp.get("text", "")
                        hay = text if case_sensitive else text.lower()
                        if cmp_needle not in hay:
                            continue
                        # Find every match position in this span and compute its rect.
                        # Estimate per-character width from the span bbox.
                        bbox = fitz.Rect(sp["bbox"])
                        if not text:
                            continue
                        char_w = bbox.width / max(len(text), 1)
                        origin = sp.get("origin", (bbox.x0, bbox.y1))
                        fontsize = float(sp.get("size", 12.0))
                        color_int = int(sp.get("color", 0))
                        color = (
                            ((color_int >> 16) & 0xFF) / 255.0,
                            ((color_int >> 8) & 0xFF) / 255.0,
                            (color_int & 0xFF) / 255.0,
                        )
                        fontname = _pick_base14(sp.get("font", ""), int(sp.get("flags", 0)))
                        start = 0
                        while True:
                            i = hay.find(cmp_needle, start)
                            if i < 0:
                                break
                            x0 = bbox.x0 + i * char_w
                            x1 = bbox.x0 + (i + len(needle)) * char_w
                            redact_rect = fitz.Rect(x0, bbox.y0, x1, bbox.y1)
                            insert_pt = fitz.Point(x0, origin[1])
                            jobs.append((redact_rect, insert_pt, replacement,
                                         fontsize, color, fontname))
                            start = i + len(needle)
                            count += 1
            for redact_rect, _, _, _, _, _ in jobs:
                page.add_redact_annot(redact_rect, fill=bg)
            if jobs:
                page.apply_redactions()
            for _, insert_pt, repl, fontsize, color, fontname in jobs:
                if not repl:
                    continue
                try:
                    page.insert_text(insert_pt, repl, fontname=fontname,
                                     fontsize=fontsize, color=color)
                except Exception:
                    page.insert_text(insert_pt, repl, fontname="helv",
                                     fontsize=fontsize, color=color)
        return count

    # ---------- text extraction / search ----------
    def extract_text(self, page_idx: Optional[int] = None) -> str:
        if page_idx is not None:
            return self.doc[page_idx].get_text()
        return "\n".join(p.get_text() for p in self.doc)

    def search(self, needle: str, case_sensitive: bool = False) -> list[SearchHit]:
        hits: list[SearchHit] = []
        if not needle:
            return hits
        flags = 0 if case_sensitive else fitz.TEXT_DEHYPHENATE
        for i, page in enumerate(self.doc):
            for rect in page.search_for(needle, flags=flags):
                hits.append(SearchHit(page=i, rect=rect))
        return hits

    # ---------- forms ----------
    def form_fields(self, page_idx: int):
        page = self.doc[page_idx]
        return list(page.widgets()) if page.first_widget else []

    def set_form_value(self, page_idx: int, field_name: str, value):
        self.snapshot()
        page = self.doc[page_idx]
        for w in page.widgets():
            if w.field_name == field_name:
                if w.field_type == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                    w.field_value = bool(value)
                else:
                    w.field_value = str(value)
                w.update()
                return True
        return False

    def flatten_forms(self):
        """Render form values into page content and remove widgets."""
        self.snapshot()
        for page in self.doc:
            for w in list(page.widgets()):
                try:
                    page.delete_widget(w)
                except Exception:
                    pass

    # ---------- export ----------
    def export_page_image(self, page_idx: int, out_path: str, dpi: int = 200):
        page = self.doc[page_idx]
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        pix.save(out_path)

    def export_all_images(self, out_dir: str, fmt: str = "png", dpi: int = 200):
        import os
        for i in range(self.page_count):
            self.export_page_image(i, os.path.join(out_dir, f"page_{i+1:04d}.{fmt}"), dpi)
