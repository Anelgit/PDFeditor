"""Dialogs: search/replace, form fill, split, extract range, sticky note, properties."""
from __future__ import annotations

import fitz
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QCheckBox, QSpinBox, QListWidget, QListWidgetItem, QPlainTextEdit,
    QDialogButtonBox, QFormLayout, QComboBox, QFileDialog, QMessageBox,
)

from .document import PDFDocument, SearchHit, TextSpan


class SearchDialog(QDialog):
    """Find text across the document. Click a result to jump to it."""

    jumpRequested = pyqtSignal(int, object)  # page, fitz.Rect

    def __init__(self, doc: PDFDocument, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find")
        self.resize(420, 480)
        self.doc = doc
        v = QVBoxLayout(self)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Search…")
        self.case = QCheckBox("Match case")
        btn = QPushButton("Find")
        btn.clicked.connect(self._do_search)
        self.input.returnPressed.connect(self._do_search)
        row.addWidget(self.input, 1)
        row.addWidget(self.case)
        row.addWidget(btn)
        v.addLayout(row)

        self.results = QListWidget()
        self.results.itemActivated.connect(self._on_pick)
        self.results.itemClicked.connect(self._on_pick)
        v.addWidget(self.results, 1)

        self.status = QLabel("")
        v.addWidget(self.status)

    def _do_search(self):
        self.results.clear()
        needle = self.input.text().strip()
        if not needle:
            return
        hits = self.doc.search(needle, case_sensitive=self.case.isChecked())
        for h in hits:
            item = QListWidgetItem(f"Page {h.page + 1}  —  ({h.rect.x0:.0f}, {h.rect.y0:.0f})")
            item.setData(Qt.ItemDataRole.UserRole, (h.page, h.rect))
            self.results.addItem(item)
        self.status.setText(f"{len(hits)} match(es)")

    def _on_pick(self, item: QListWidgetItem):
        page, rect = item.data(Qt.ItemDataRole.UserRole)
        self.jumpRequested.emit(page, rect)


class StickyNoteDialog(QDialog):
    def __init__(self, parent=None, initial: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Sticky note")
        self.resize(360, 220)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Note text:"))
        self.text = QPlainTextEdit(initial)
        v.addWidget(self.text, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def value(self) -> str:
        return self.text.toPlainText()


class FreeTextDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add text")
        self.resize(380, 240)
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.text = QPlainTextEdit()
        self.size = QSpinBox(); self.size.setRange(6, 144); self.size.setValue(12)
        form.addRow("Text:", self.text)
        form.addRow("Font size:", self.size)
        v.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def values(self):
        return self.text.toPlainText(), self.size.value()


class ExtractRangeDialog(QDialog):
    """Pick a page range and an output path."""

    def __init__(self, page_count: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Extract pages")
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.start = QSpinBox(); self.start.setRange(1, page_count); self.start.setValue(1)
        self.end = QSpinBox(); self.end.setRange(1, page_count); self.end.setValue(page_count)
        form.addRow("From page:", self.start)
        form.addRow("To page:", self.end)
        v.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def values(self):
        return self.start.value() - 1, self.end.value() - 1


class EditTextDialog(QDialog):
    """Edit a single text span: redact + re-stamp at the same baseline."""

    def __init__(self, span: TextSpan, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit text")
        self.resize(480, 280)
        self.span = span
        v = QVBoxLayout(self)
        info = QLabel(
            f"<b>Font:</b> {span.fontname or '(unknown)'}    "
            f"<b>Size:</b> {span.fontsize:.1f}    "
            f"<b>Page:</b> {span.page + 1}"
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        v.addWidget(info)
        v.addWidget(QLabel("Text:"))
        self.text = QPlainTextEdit(span.text)
        v.addWidget(self.text, 1)
        warn = QLabel(
            "<i>Replacement uses a closest-matching Base14 font. "
            "If the new text is much wider than the original it may overflow "
            "neighbouring text.</i>"
        )
        warn.setWordWrap(True)
        warn.setStyleSheet("color: #555;")
        v.addWidget(warn)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def new_text(self) -> str:
        return self.text.toPlainText()


class FindReplaceDialog(QDialog):
    """Find every occurrence and replace document-wide."""

    def __init__(self, doc: PDFDocument, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Find & Replace")
        self.resize(440, 200)
        self.doc = doc
        v = QVBoxLayout(self)
        form = QFormLayout()
        self.find = QLineEdit()
        self.replace = QLineEdit()
        self.case = QCheckBox("Match case")
        form.addRow("Find:", self.find)
        form.addRow("Replace with:", self.replace)
        form.addRow("", self.case)
        v.addLayout(form)
        self.status = QLabel("")
        v.addWidget(self.status)
        bb = QDialogButtonBox()
        self.btn_count = bb.addButton("Count matches", QDialogButtonBox.ButtonRole.ActionRole)
        self.btn_apply = bb.addButton("Replace all", QDialogButtonBox.ButtonRole.AcceptRole)
        bb.addButton(QDialogButtonBox.StandardButton.Close)
        bb.rejected.connect(self.reject)
        self.btn_count.clicked.connect(self._count)
        self.btn_apply.clicked.connect(self._apply)
        v.addWidget(bb)
        self._replaced = 0

    def _count(self):
        needle = self.find.text()
        if not needle:
            self.status.setText("")
            return
        hits = self.doc.search(needle, case_sensitive=self.case.isChecked())
        self.status.setText(f"{len(hits)} match(es) found.")

    def _apply(self):
        needle = self.find.text()
        if not needle:
            return
        n = self.doc.find_and_replace(needle, self.replace.text(),
                                      case_sensitive=self.case.isChecked())
        self._replaced = n
        self.status.setText(f"Replaced {n} occurrence(s).")
        if n:
            self.accept()

    def replaced_count(self) -> int:
        return self._replaced


class FormFillDialog(QDialog):
    """Edit form-field values on the current page."""

    def __init__(self, doc: PDFDocument, page_idx: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Form fields — page {page_idx + 1}")
        self.resize(440, 480)
        self.doc = doc
        self.page_idx = page_idx
        self.editors: list[tuple[str, object, int]] = []

        v = QVBoxLayout(self)
        form = QFormLayout()
        widgets = self.doc.form_fields(page_idx)
        if not widgets:
            v.addWidget(QLabel("No form fields on this page."))
        for w in widgets:
            ftype = w.field_type
            name = w.field_name or "(unnamed)"
            if ftype == fitz.PDF_WIDGET_TYPE_CHECKBOX:
                cb = QCheckBox()
                cb.setChecked(bool(w.field_value))
                form.addRow(name, cb)
                self.editors.append((name, cb, ftype))
            elif ftype == fitz.PDF_WIDGET_TYPE_COMBOBOX:
                combo = QComboBox()
                combo.addItems(w.choice_values or [])
                if w.field_value:
                    idx = combo.findText(str(w.field_value))
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                form.addRow(name, combo)
                self.editors.append((name, combo, ftype))
            else:
                le = QLineEdit(str(w.field_value or ""))
                form.addRow(name, le)
                self.editors.append((name, le, ftype))
        v.addLayout(form)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept); bb.rejected.connect(self.reject)
        v.addWidget(bb)

    def apply(self):
        for name, w, ftype in self.editors:
            if isinstance(w, QCheckBox):
                self.doc.set_form_value(self.page_idx, name, w.isChecked())
            elif isinstance(w, QComboBox):
                self.doc.set_form_value(self.page_idx, name, w.currentText())
            elif isinstance(w, QLineEdit):
                self.doc.set_form_value(self.page_idx, name, w.text())
