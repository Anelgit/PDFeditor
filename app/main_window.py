"""Main window: menus, toolbars, signal wiring, file ops."""
from __future__ import annotations

import os
import tempfile
from typing import Optional

import fitz
from PyQt6.QtCore import Qt, QPointF, QSettings, QSize
from PyQt6.QtGui import (
    QAction, QActionGroup, QIcon, QKeySequence, QColor,
)
from PyQt6.QtWidgets import (
    QMainWindow, QSplitter, QStatusBar, QLabel, QToolBar, QFileDialog,
    QMessageBox, QInputDialog, QColorDialog, QSpinBox, QWidget,
    QHBoxLayout, QPushButton, QDialog,
)

from .document import PDFDocument
from .page_view import PageView
from .sidebar import ThumbnailSidebar
from .dialogs import (
    SearchDialog, StickyNoteDialog, FreeTextDialog,
    ExtractRangeDialog, FormFillDialog,
    EditTextDialog, FindReplaceDialog,
)


TOOLS = [
    ("hand", "Hand (pan)"),
    ("edit_text", "Edit text"),
    ("highlight", "Highlight"),
    ("underline", "Underline"),
    ("strikeout", "Strikethrough"),
    ("squiggly", "Squiggly"),
    ("sticky", "Sticky note"),
    ("freetext", "Free text annotation"),
    ("text", "Insert text"),
    ("image_rect", "Insert image (drag rect)"),
    ("ink", "Freehand draw"),
    ("line", "Line"),
    ("arrow", "Arrow"),
    ("rect", "Rectangle"),
    ("ellipse", "Ellipse"),
    ("signature", "Signature"),
    ("redact", "Redact"),
]


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Editor")
        self.resize(1280, 860)
        self.settings = QSettings("local", "PDFEditor")

        self.doc = PDFDocument()

        # Central widgets
        self.sidebar = ThumbnailSidebar()
        self.view = PageView()
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.sidebar)
        splitter.addWidget(self.view)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([200, 1080])
        self.setCentralWidget(splitter)

        # Status bar
        self.status = QStatusBar(); self.setStatusBar(self.status)
        self.page_label = QLabel("—")
        self.zoom_label = QLabel("100%")
        self.tool_label = QLabel("Tool: hand")
        self.status.addPermanentWidget(self.tool_label)
        self.status.addPermanentWidget(self.zoom_label)
        self.status.addPermanentWidget(self.page_label)

        # Wire signals
        self.view.pageChanged.connect(self._on_page_changed)
        self.view.zoomChanged.connect(self._on_zoom_changed)
        self.view.pageClicked.connect(self._on_page_clicked)
        self.view.pageDragged.connect(self._on_page_dragged)
        self.view.textRectFinished.connect(self._on_rect_finished)
        self.view.inkStrokeFinished.connect(self._on_ink_finished)

        self.sidebar.pageActivated.connect(self.view.goto_page)
        self.sidebar.reorderRequested.connect(self._on_reorder)
        self.sidebar.deletePage.connect(self._on_delete_page)
        self.sidebar.duplicatePage.connect(self._on_duplicate_page)
        self.sidebar.rotatePage.connect(self._on_rotate_page)
        self.sidebar.insertBlankAt.connect(self._on_insert_blank_at)
        self.sidebar.extractPage.connect(self._on_extract_single)

        # State
        self.search_dialog: Optional[SearchDialog] = None

        # Build menus + toolbar
        self._build_actions()
        self._build_menus()
        self._build_toolbar()

        self._update_ui_state()

    # ============================================================
    # ACTIONS / MENUS / TOOLBAR
    # ============================================================
    def _act(self, text, slot=None, shortcut=None, checkable=False) -> QAction:
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if checkable:
            a.setCheckable(True)
        if slot is not None:
            a.triggered.connect(slot)
        return a

    def _build_actions(self):
        # File
        self.act_new = self._act("&New", self.new_doc, "Ctrl+N")
        self.act_open = self._act("&Open…", self.open_file, "Ctrl+O")
        self.act_save = self._act("&Save", self.save_file, "Ctrl+S")
        self.act_save_as = self._act("Save &As…", self.save_as, "Ctrl+Shift+S")
        self.act_close = self._act("&Close", self.close_doc, "Ctrl+W")
        self.act_print = self._act("&Print…", self.print_doc, "Ctrl+P")
        self.act_export_images = self._act("Export pages as &images…", self.export_images)
        self.act_export_text = self._act("Export &text…", self.export_text)
        self.act_quit = self._act("&Quit", self.close, "Ctrl+Q")

        # Edit
        self.act_undo = self._act("&Undo", self.undo, "Ctrl+Z")
        self.act_redo = self._act("&Redo", self.redo, "Ctrl+Y")
        self.act_find = self._act("&Find…", self.show_search, "Ctrl+F")
        self.act_find_replace = self._act("Find & &Replace…", self.show_find_replace, "Ctrl+H")

        # View
        self.act_zoom_in = self._act("Zoom &in", self.view.zoom_in, "Ctrl++")
        self.act_zoom_in_alt = self._act("Zoom in (alt)", self.view.zoom_in, "Ctrl+=")
        self.act_zoom_out = self._act("Zoom &out", self.view.zoom_out, "Ctrl+-")
        self.act_zoom_actual = self._act("&Actual size", lambda: self.view.set_zoom(1.0), "Ctrl+1")
        self.act_fit_width = self._act("Fit &width", self.view.fit_width, "Ctrl+2")
        self.act_fit_page = self._act("Fit &page", self.view.fit_page, "Ctrl+0")
        self.act_goto = self._act("&Go to page…", self.goto_page_dialog, "Ctrl+G")
        self.act_next_page = self._act("&Next page", self.next_page, "Ctrl+Right")
        self.act_prev_page = self._act("&Previous page", self.prev_page, "Ctrl+Left")

        # Page
        self.act_rot_cw = self._act("Rotate page &CW", lambda: self.rotate_current(90))
        self.act_rot_ccw = self._act("Rotate page CC&W", lambda: self.rotate_current(-90))
        self.act_rot_180 = self._act("Rotate page &180°", lambda: self.rotate_current(180))
        self.act_del_page = self._act("&Delete current page", self.delete_current_page)
        self.act_dup_page = self._act("D&uplicate current page", self.duplicate_current_page)
        self.act_blank_before = self._act("Insert blank &before current", self.blank_before)
        self.act_blank_after = self._act("Insert blank &after current", self.blank_after)
        self.act_merge = self._act("&Merge with another PDF…", self.merge_pdf)
        self.act_split = self._act("&Split each page to its own PDF…", self.split_each)
        self.act_extract = self._act("&Extract page range…", self.extract_range)

        # Forms
        self.act_form_fill = self._act("Fill form &fields on current page…", self.fill_forms)
        self.act_form_flatten = self._act("Fla&tten forms", self.flatten_forms)

        # OCR
        self.act_ocr_page_text = self._act("OCR current page → show &text", self.ocr_page_text)
        self.act_ocr_make_searchable = self._act("Make current page &searchable (invisible text layer)",
                                                 self.ocr_make_searchable_page)
        self.act_ocr_all_searchable = self._act("Make &entire document searchable",
                                                self.ocr_make_searchable_all)

        # Help
        self.act_about = self._act("&About", self.about)

    def _build_menus(self):
        mb = self.menuBar()

        m_file = mb.addMenu("&File")
        for a in [self.act_new, self.act_open, self.act_save, self.act_save_as,
                  self.act_close]:
            m_file.addAction(a)
        m_file.addSeparator()
        m_file.addAction(self.act_print)
        m_file.addAction(self.act_export_images)
        m_file.addAction(self.act_export_text)
        m_file.addSeparator()
        m_file.addAction(self.act_quit)

        m_edit = mb.addMenu("&Edit")
        m_edit.addAction(self.act_undo)
        m_edit.addAction(self.act_redo)
        m_edit.addSeparator()
        m_edit.addAction(self.act_find)
        m_edit.addAction(self.act_find_replace)

        m_view = mb.addMenu("&View")
        for a in [self.act_zoom_in, self.act_zoom_out, self.act_zoom_actual,
                  self.act_fit_width, self.act_fit_page]:
            m_view.addAction(a)
        m_view.addSeparator()
        m_view.addAction(self.act_goto)
        m_view.addAction(self.act_next_page)
        m_view.addAction(self.act_prev_page)

        m_page = mb.addMenu("&Page")
        for a in [self.act_rot_cw, self.act_rot_ccw, self.act_rot_180]:
            m_page.addAction(a)
        m_page.addSeparator()
        for a in [self.act_dup_page, self.act_blank_before, self.act_blank_after, self.act_del_page]:
            m_page.addAction(a)
        m_page.addSeparator()
        m_page.addAction(self.act_merge)
        m_page.addAction(self.act_split)
        m_page.addAction(self.act_extract)

        m_form = mb.addMenu("F&orms")
        m_form.addAction(self.act_form_fill)
        m_form.addAction(self.act_form_flatten)

        m_ocr = mb.addMenu("OC&R")
        m_ocr.addAction(self.act_ocr_page_text)
        m_ocr.addAction(self.act_ocr_make_searchable)
        m_ocr.addAction(self.act_ocr_all_searchable)

        m_help = mb.addMenu("&Help")
        m_help.addAction(self.act_about)

    def _build_toolbar(self):
        # File toolbar
        tb = QToolBar("File"); tb.setIconSize(QSize(20, 20))
        self.addToolBar(tb)
        tb.addAction(self.act_open)
        tb.addAction(self.act_save)
        tb.addSeparator()
        tb.addAction(self.act_undo)
        tb.addAction(self.act_redo)
        tb.addSeparator()
        tb.addAction(self.act_zoom_out)
        tb.addAction(self.act_zoom_in)
        tb.addAction(self.act_fit_width)
        tb.addAction(self.act_fit_page)
        tb.addSeparator()
        tb.addAction(self.act_find)

        # Tools toolbar
        ttb = QToolBar("Tools"); ttb.setIconSize(QSize(20, 20))
        self.addToolBar(ttb)
        self.tool_group = QActionGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_actions: dict[str, QAction] = {}
        for tid, label in TOOLS:
            a = QAction(label, self); a.setCheckable(True)
            a.triggered.connect(lambda checked, t=tid: self.set_tool(t))
            self.tool_group.addAction(a)
            ttb.addAction(a)
            self.tool_actions[tid] = a
        self.tool_actions["hand"].setChecked(True)

        ttb.addSeparator()
        # color
        self.color_btn = QPushButton("Color")
        self.color_btn.clicked.connect(self.pick_color)
        self._refresh_color_btn()
        ttb.addWidget(self.color_btn)
        # stroke width
        self.width_spin = QSpinBox()
        self.width_spin.setRange(1, 20); self.width_spin.setValue(2)
        self.width_spin.setPrefix("w:")
        self.width_spin.valueChanged.connect(lambda v: setattr(self.view, "tool_stroke_width", float(v)))
        ttb.addWidget(self.width_spin)
        # font size
        self.size_spin = QSpinBox()
        self.size_spin.setRange(6, 144); self.size_spin.setValue(12)
        self.size_spin.setPrefix("size:")
        self.size_spin.valueChanged.connect(lambda v: setattr(self.view, "tool_fontsize", int(v)))
        ttb.addWidget(self.size_spin)

    def _refresh_color_btn(self):
        r, g, b = self.view.tool_color
        c = QColor(int(r * 255), int(g * 255), int(b * 255))
        self.color_btn.setStyleSheet(
            f"QPushButton {{ background: {c.name()}; color: {'white' if c.lightness() < 128 else 'black'}; padding: 4px 10px; }}"
        )

    # ============================================================
    # FILE OPS
    # ============================================================
    def new_doc(self):
        if not self._confirm_discard():
            return
        self.doc.new()
        self.view.set_document(self.doc)
        self.sidebar.set_document(self.doc)
        self._update_title()
        self._update_ui_state()

    def open_file(self):
        if not self._confirm_discard():
            return
        last_dir = self.settings.value("lastDir", os.path.expanduser("~"))
        path, _ = QFileDialog.getOpenFileName(self, "Open PDF", last_dir, "PDF (*.pdf)")
        if not path:
            return
        self.open_path(path)

    def open_path(self, path: str):
        try:
            self.doc.open(path)
        except Exception as e:
            QMessageBox.critical(self, "Open failed", str(e))
            return
        self.settings.setValue("lastDir", os.path.dirname(path))
        self.view.set_document(self.doc)
        self.sidebar.set_document(self.doc)
        self._update_title()
        self._update_ui_state()

    def save_file(self):
        if not self.doc.is_open:
            return
        if not self.doc.path:
            return self.save_as()
        try:
            # PyMuPDF can't overwrite the file it has open without incremental;
            # use save-to-temp + replace.
            tmp = self.doc.path + ".tmp"
            self.doc.doc.save(tmp, deflate=True, garbage=3)
            self.doc.doc.close()
            os.replace(tmp, self.doc.path)
            self.doc.doc = fitz.open(self.doc.path)
            self.doc.dirty = False
            self.status.showMessage("Saved.", 3000)
            self._update_title()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def save_as(self):
        if not self.doc.is_open:
            return
        last_dir = self.settings.value("lastDir", os.path.expanduser("~"))
        default_name = os.path.basename(self.doc.path) if self.doc.path else "Untitled.pdf"
        path, _ = QFileDialog.getSaveFileName(self, "Save PDF as",
                                              os.path.join(last_dir, default_name),
                                              "PDF (*.pdf)")
        if not path:
            return
        try:
            self.doc.save(path)
            self.settings.setValue("lastDir", os.path.dirname(path))
            self.status.showMessage("Saved.", 3000)
            self._update_title()
        except Exception as e:
            QMessageBox.critical(self, "Save failed", str(e))

    def close_doc(self):
        if not self._confirm_discard():
            return
        self.doc.close()
        self.view.set_document(self.doc)
        self.sidebar.set_document(self.doc)
        self._update_title()
        self._update_ui_state()

    def print_doc(self):
        if not self.doc.is_open:
            return
        try:
            from PyQt6.QtPrintSupport import QPrinter, QPrintDialog
            from PyQt6.QtGui import QPainter as QPainter2, QImage as QImage2
        except ImportError:
            QMessageBox.warning(self, "Print",
                                "QtPrintSupport is not available. Install PyQt6 with print support.")
            return
        printer = QPrinter(QPrinter.PrinterMode.HighResolution)
        dlg = QPrintDialog(printer, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        painter = QPainter2(printer)
        first = True
        for i in range(self.doc.page_count):
            if not first:
                printer.newPage()
            first = False
            page = self.doc.page(i)
            zoom = printer.resolution() / 72.0
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            img = QImage2(pix.samples, pix.width, pix.height, pix.stride,
                          QImage2.Format.Format_RGB888).copy()
            pr = printer.pageRect(QPrinter.Unit.DevicePixel)
            painter.drawImage(pr, img)
        painter.end()

    def export_images(self):
        if not self.doc.is_open:
            return
        d = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if not d:
            return
        try:
            self.doc.export_all_images(d, fmt="png", dpi=200)
            self.status.showMessage(f"Exported {self.doc.page_count} images to {d}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def export_text(self):
        if not self.doc.is_open:
            return
        path, _ = QFileDialog.getSaveFileName(self, "Save text as", "",
                                              "Text (*.txt)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.doc.extract_text())
            self.status.showMessage("Text exported.", 3000)
        except Exception as e:
            QMessageBox.critical(self, "Export failed", str(e))

    def _confirm_discard(self) -> bool:
        if not self.doc.is_open or not self.doc.dirty:
            return True
        ans = QMessageBox.question(
            self, "Unsaved changes",
            "Discard unsaved changes?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if ans == QMessageBox.StandardButton.Cancel:
            return False
        if ans == QMessageBox.StandardButton.Save:
            self.save_file()
            return not self.doc.dirty
        return True

    def closeEvent(self, ev):
        if self._confirm_discard():
            ev.accept()
        else:
            ev.ignore()

    # ============================================================
    # EDIT
    # ============================================================
    def undo(self):
        if not self.doc.can_undo():
            return
        self.doc.undo()
        self.view.reload()
        self.sidebar.reload()
        self._update_ui_state()

    def redo(self):
        if not self.doc.can_redo():
            return
        self.doc.redo()
        self.view.reload()
        self.sidebar.reload()
        self._update_ui_state()

    def show_search(self):
        if not self.doc.is_open:
            return
        if self.search_dialog is None:
            self.search_dialog = SearchDialog(self.doc, self)
            self.search_dialog.jumpRequested.connect(self._goto_hit)
        self.search_dialog.show()
        self.search_dialog.raise_()
        self.search_dialog.activateWindow()

    def _goto_hit(self, page: int, rect):
        self.view.goto_page(page)

    def show_find_replace(self):
        if not self.doc.is_open:
            return
        dlg = FindReplaceDialog(self.doc, self)
        dlg.exec()
        if dlg.replaced_count() > 0:
            self.view.reload()
            self.sidebar.reload()
            self._update_ui_state()

    # ============================================================
    # VIEW
    # ============================================================
    def goto_page_dialog(self):
        if not self.doc.is_open:
            return
        n, ok = QInputDialog.getInt(self, "Go to page", "Page:",
                                    self.view.current_page() + 1, 1, self.doc.page_count)
        if ok:
            self.view.goto_page(n - 1)

    def next_page(self):
        self.view.goto_page(self.view.current_page() + 1)

    def prev_page(self):
        self.view.goto_page(self.view.current_page() - 1)

    # ============================================================
    # PAGE OPS
    # ============================================================
    def _current(self) -> int:
        return self.view.current_page()

    def rotate_current(self, deg: int):
        self.doc.rotate_page(self._current(), deg)
        self.view.reload()
        self.sidebar.reload()
        self._update_ui_state()

    def delete_current_page(self):
        if self.doc.page_count <= 1:
            QMessageBox.information(self, "Delete", "Can't delete the only page.")
            return
        idx = self._current()
        self._on_delete_page(idx)

    def duplicate_current_page(self):
        self._on_duplicate_page(self._current())

    def blank_before(self):
        self._on_insert_blank_at(self._current())

    def blank_after(self):
        self._on_insert_blank_at(self._current() + 1)

    def merge_pdf(self):
        path, _ = QFileDialog.getOpenFileName(self, "Merge with PDF", "", "PDF (*.pdf)")
        if not path:
            return
        try:
            self.doc.merge_with(path)
            self.view.reload()
            self.sidebar.reload()
            self._update_ui_state()
        except Exception as e:
            QMessageBox.critical(self, "Merge failed", str(e))

    def split_each(self):
        d = QFileDialog.getExistingDirectory(self, "Choose output folder")
        if not d:
            return
        try:
            self.doc.split_each(d)
            self.status.showMessage(f"Split into {self.doc.page_count} files in {d}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Split failed", str(e))

    def extract_range(self):
        if not self.doc.is_open:
            return
        dlg = ExtractRangeDialog(self.doc.page_count, self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        start, end = dlg.values()
        if end < start:
            start, end = end, start
        path, _ = QFileDialog.getSaveFileName(self, "Save extracted PDF as", "",
                                              "PDF (*.pdf)")
        if not path:
            return
        try:
            self.doc.extract_pages(start, end, path)
            self.status.showMessage(f"Extracted pages {start + 1}–{end + 1} → {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Extract failed", str(e))

    # Sidebar callbacks
    def _on_reorder(self, src: int, dst: int):
        self.doc.move_page(src, dst)
        self.view.reload()
        self.sidebar.reload()
        self._update_ui_state()

    def _on_delete_page(self, idx: int):
        if self.doc.page_count <= 1:
            QMessageBox.information(self, "Delete", "Can't delete the only page.")
            return
        self.doc.delete_page(idx)
        self.view.reload()
        self.sidebar.reload()
        self._update_ui_state()

    def _on_duplicate_page(self, idx: int):
        self.doc.duplicate_page(idx)
        self.view.reload()
        self.sidebar.reload()
        self._update_ui_state()

    def _on_rotate_page(self, idx: int, deg: int):
        self.doc.rotate_page(idx, deg)
        self.view.refresh_page(idx)
        self.sidebar.refresh_page(idx)
        self._update_ui_state()

    def _on_insert_blank_at(self, idx: int):
        self.doc.insert_blank(idx)
        self.view.reload()
        self.sidebar.reload()
        self._update_ui_state()

    def _on_extract_single(self, idx: int):
        path, _ = QFileDialog.getSaveFileName(self, "Save page as PDF", "",
                                              "PDF (*.pdf)")
        if not path:
            return
        try:
            self.doc.extract_pages(idx, idx, path)
            self.status.showMessage(f"Extracted page {idx + 1} → {path}", 5000)
        except Exception as e:
            QMessageBox.critical(self, "Extract failed", str(e))

    # ============================================================
    # TOOL HANDLERS
    # ============================================================
    def set_tool(self, tool_id: str):
        # signature uses the image insertion path with a stored signature file
        if tool_id == "signature":
            self.view.set_tool("image_rect")
            self._sig_active = True
        else:
            self._sig_active = False
            self.view.set_tool(tool_id)
        self.tool_label.setText(f"Tool: {tool_id}")

    def pick_color(self):
        r, g, b = self.view.tool_color
        initial = QColor(int(r * 255), int(g * 255), int(b * 255))
        c = QColorDialog.getColor(initial, self, "Pick tool color")
        if c.isValid():
            self.view.tool_color = (c.redF(), c.greenF(), c.blueF())
            self._refresh_color_btn()

    def _on_page_clicked(self, page_idx: int, pt: QPointF):
        tool = self.view.tool
        if tool == "sticky":
            dlg = StickyNoteDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                self.doc.add_sticky(page_idx, fitz.Point(pt.x(), pt.y()), dlg.value())
                self.view.refresh_page(page_idx)
        elif tool == "text":
            dlg = FreeTextDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                text, size = dlg.values()
                if text:
                    self.doc.insert_text(page_idx, fitz.Point(pt.x(), pt.y()),
                                         text, fontsize=size,
                                         color=self.view.tool_color)
                    self.view.refresh_page(page_idx)
        elif tool == "edit_text":
            span = self.doc.span_at(page_idx, (pt.x(), pt.y()))
            if span is None:
                self.status.showMessage("No text under cursor.", 3000)
            else:
                dlg = EditTextDialog(span, self)
                if dlg.exec() == QDialog.DialogCode.Accepted:
                    new = dlg.new_text()
                    if new != span.text:
                        self.doc.replace_span(span, new)
                        self.view.refresh_page(page_idx)
        self._update_ui_state()

    def _on_page_dragged(self, page_idx: int, p1: QPointF, p2: QPointF):
        tool = self.view.tool
        if tool in ("line", "arrow"):
            self.doc.add_line(page_idx,
                              fitz.Point(p1.x(), p1.y()), fitz.Point(p2.x(), p2.y()),
                              color=self.view.tool_color,
                              width=self.view.tool_stroke_width,
                              arrow=(tool == "arrow"))
            self.view.refresh_page(page_idx)
        self._update_ui_state()

    def _on_ink_finished(self, page_idx: int, points: list):
        self.doc.add_ink(page_idx, [points],
                         color=self.view.tool_color,
                         width=self.view.tool_stroke_width)
        self.view.refresh_page(page_idx)
        self._update_ui_state()

    def _on_rect_finished(self, page_idx: int, payload):
        kind, rect = payload
        if kind in ("highlight", "underline", "strikeout", "squiggly"):
            quads = self._text_quads_in_rect(page_idx, rect)
            if not quads:
                quads = [rect]
            fn = {
                "highlight": self.doc.add_highlight,
                "underline": self.doc.add_underline,
                "strikeout": self.doc.add_strikeout,
                "squiggly": self.doc.add_squiggly,
            }[kind]
            fn(page_idx, quads)
            self.view.refresh_page(page_idx)
        elif kind == "shape":
            tool = self.view.tool
            if tool == "rect":
                self.doc.add_rect(page_idx, rect, color=self.view.tool_color,
                                  width=self.view.tool_stroke_width)
            elif tool == "ellipse":
                self.doc.add_ellipse(page_idx, rect, color=self.view.tool_color,
                                     width=self.view.tool_stroke_width)
            self.view.refresh_page(page_idx)
        elif kind == "freetext":
            dlg = FreeTextDialog(self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                text, size = dlg.values()
                if text:
                    self.doc.add_freetext(page_idx, rect, text, fontsize=size,
                                          color=self.view.tool_color)
                    self.view.refresh_page(page_idx)
        elif kind == "image":
            img_path = self._image_path_for_insert()
            if img_path:
                try:
                    self.doc.insert_image(page_idx, rect, img_path)
                    self.view.refresh_page(page_idx)
                except Exception as e:
                    QMessageBox.critical(self, "Insert image failed", str(e))
        elif kind == "redact":
            if QMessageBox.question(
                self, "Redact",
                "Permanently remove content inside the selected area?",
            ) == QMessageBox.StandardButton.Yes:
                self.doc.redact_rect(page_idx, rect)
                self.view.refresh_page(page_idx)
        self._update_ui_state()

    def _image_path_for_insert(self) -> Optional[str]:
        if getattr(self, "_sig_active", False):
            sig = self.settings.value("signaturePath", "")
            if sig and os.path.exists(sig):
                return sig
            # ask once
            path, _ = QFileDialog.getOpenFileName(
                self, "Pick signature image", "",
                "Images (*.png *.jpg *.jpeg)"
            )
            if path:
                self.settings.setValue("signaturePath", path)
            return path or None
        path, _ = QFileDialog.getOpenFileName(
            self, "Pick image to insert", "",
            "Images (*.png *.jpg *.jpeg *.bmp)"
        )
        return path or None

    def _text_quads_in_rect(self, page_idx: int, rect):
        """Find text-word rects inside the given rect for text-aware annotations."""
        page = self.doc.page(page_idx)
        sel = fitz.Rect(rect)
        quads = []
        for w in page.get_text("words"):
            x0, y0, x1, y1 = w[0], w[1], w[2], w[3]
            wr = fitz.Rect(x0, y0, x1, y1)
            if wr.intersects(sel):
                quads.append(wr)
        return quads

    # ============================================================
    # FORMS / OCR
    # ============================================================
    def fill_forms(self):
        if not self.doc.is_open:
            return
        idx = self._current()
        dlg = FormFillDialog(self.doc, idx, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            dlg.apply()
            self.view.refresh_page(idx)
            self._update_ui_state()

    def flatten_forms(self):
        if not self.doc.is_open:
            return
        if QMessageBox.question(self, "Flatten forms",
                                "Flatten all form fields? This is hard to undo manually."
                                ) != QMessageBox.StandardButton.Yes:
            return
        self.doc.flatten_forms()
        self.view.reload()
        self._update_ui_state()

    def ocr_page_text(self):
        if not self.doc.is_open:
            return
        from . import ocr
        try:
            text = ocr.ocr_page(self.doc.doc, self._current())
        except Exception as e:
            QMessageBox.critical(self, "OCR failed", str(e))
            return
        QMessageBox.information(self, "OCR result", text[:8000] or "(no text)")

    def ocr_make_searchable_page(self):
        if not self.doc.is_open:
            return
        from . import ocr
        try:
            self.doc.snapshot()
            ocr.ocr_to_textlayer(self.doc.doc, self._current())
        except Exception as e:
            QMessageBox.critical(self, "OCR failed", str(e))
            return
        self.view.refresh_page(self._current())
        self.status.showMessage("Added invisible OCR text layer.", 3000)
        self._update_ui_state()

    def ocr_make_searchable_all(self):
        if not self.doc.is_open:
            return
        from . import ocr
        try:
            self.doc.snapshot()
            for i in range(self.doc.page_count):
                self.status.showMessage(f"OCR page {i + 1}/{self.doc.page_count}…")
                ocr.ocr_to_textlayer(self.doc.doc, i)
        except Exception as e:
            QMessageBox.critical(self, "OCR failed", str(e))
            return
        self.view.reload()
        self.status.showMessage("OCR complete.", 5000)
        self._update_ui_state()

    # ============================================================
    # HELP / STATE
    # ============================================================
    def about(self):
        QMessageBox.about(
            self, "About PDF Editor",
            "A free, local PDF editor built with PyQt6 + PyMuPDF.\n\n"
            "Features: view, annotate, draw, insert text/images, "
            "rotate/delete/duplicate/merge/split/extract pages, fill forms, "
            "redact, OCR, export."
        )

    def _on_page_changed(self, idx: int):
        if self.doc.is_open:
            self.page_label.setText(f"Page {idx + 1} / {self.doc.page_count}")
        else:
            self.page_label.setText("—")

    def _on_zoom_changed(self, z: float):
        self.zoom_label.setText(f"{int(z * 100)}%")

    def _update_title(self):
        if not self.doc.is_open:
            self.setWindowTitle("PDF Editor")
            return
        name = os.path.basename(self.doc.path) if self.doc.path else "Untitled"
        dot = " •" if self.doc.dirty else ""
        self.setWindowTitle(f"{name}{dot} — PDF Editor")

    def _update_ui_state(self):
        has = self.doc.is_open
        for a in [self.act_save, self.act_save_as, self.act_close, self.act_print,
                  self.act_export_images, self.act_export_text,
                  self.act_find, self.act_find_replace,
                  self.act_zoom_in, self.act_zoom_out, self.act_zoom_actual,
                  self.act_fit_width, self.act_fit_page, self.act_goto, self.act_next_page,
                  self.act_prev_page,
                  self.act_rot_cw, self.act_rot_ccw, self.act_rot_180,
                  self.act_del_page, self.act_dup_page, self.act_blank_before,
                  self.act_blank_after, self.act_merge, self.act_split, self.act_extract,
                  self.act_form_fill, self.act_form_flatten,
                  self.act_ocr_page_text, self.act_ocr_make_searchable,
                  self.act_ocr_all_searchable]:
            a.setEnabled(has)
        self.act_undo.setEnabled(self.doc.can_undo())
        self.act_redo.setEnabled(self.doc.can_redo())
        self._update_title()
        if has:
            self.page_label.setText(f"Page {self.view.current_page() + 1} / {self.doc.page_count}")
            self.zoom_label.setText(f"{int(self.view.zoom * 100)}%")
