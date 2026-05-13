"""Verify span_at, replace_span, and find_and_replace work end-to-end."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import fitz
from app.document import PDFDocument

# Build a test PDF.
d = fitz.open()
p = d.new_page()
p.insert_text((72, 120), "The quick brown fox", fontsize=16, fontname="helv")
p.insert_text((72, 160), "jumps over the lazy dog", fontsize=14, fontname="tiro")
p.insert_text((72, 200), "Original word: REPLACEME and more", fontsize=12)
d.save("smoke_edit_in.pdf"); d.close()

doc = PDFDocument()
doc.open("smoke_edit_in.pdf")

# 1) Span pick.
spans = doc.spans_on_page(0)
print(f"spans on page 0: {len(spans)}")
assert len(spans) >= 3
fox_span = next((s for s in spans if "fox" in s.text), None)
assert fox_span is not None
print(f"fox span text='{fox_span.text}' font={fox_span.fontname} size={fox_span.fontsize}")

# Click in the middle of the fox span — should find it.
mid_x = (fox_span.bbox.x0 + fox_span.bbox.x1) / 2
mid_y = (fox_span.bbox.y0 + fox_span.bbox.y1) / 2
picked = doc.span_at(0, (mid_x, mid_y))
assert picked is not None and "fox" in picked.text, f"got {picked!r}"

# Click outside any text should return None.
assert doc.span_at(0, (0, 0)) is None

# 2) Replace span text.
doc.replace_span(fox_span, "The QUICK red CAT")
text_after = doc.extract_text(0)
print("after replace_span:")
print(text_after)
assert "fox" not in text_after
assert "red CAT" in text_after

# 3) Find & Replace.
n = doc.find_and_replace("REPLACEME", "SUCCESS")
print(f"find_and_replace count = {n}")
assert n == 1
text_after2 = doc.extract_text(0)
print("after find_and_replace:")
print(text_after2)
assert "REPLACEME" not in text_after2
assert "SUCCESS" in text_after2

# 4) Undo should bring fox back eventually.
doc.undo()  # undoes find_and_replace
doc.undo()  # undoes replace_span
text_after3 = doc.extract_text(0)
assert "fox" in text_after3, f"expected fox back, got: {text_after3!r}"
assert "REPLACEME" in text_after3

# 5) Case-insensitive find & replace.
n2 = doc.find_and_replace("THE", "***", case_sensitive=False)
print(f"case-insensitive matched {n2}")
assert n2 >= 2

doc.close()
os.remove("smoke_edit_in.pdf")
print("EDIT SMOKE PASS")
