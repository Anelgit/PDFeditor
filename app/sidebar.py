"""Page thumbnail sidebar with drag-to-reorder."""
from __future__ import annotations

import fitz
from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtGui import QPixmap, QImage, QIcon
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QAbstractItemView, QMenu,
)

from .document import PDFDocument


THUMB_W = 140


class ThumbnailSidebar(QListWidget):
    pageActivated = pyqtSignal(int)       # double-click / select
    reorderRequested = pyqtSignal(int, int)  # src, dst
    deletePage = pyqtSignal(int)
    duplicatePage = pyqtSignal(int)
    rotatePage = pyqtSignal(int, int)     # idx, +/-90
    insertBlankAt = pyqtSignal(int)
    extractPage = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc: PDFDocument | None = None
        self.setIconSize(QSize(THUMB_W, int(THUMB_W * 1.4)))
        self.setSpacing(8)
        self.setMovement(QListWidget.Movement.Snap)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemDoubleClicked.connect(self._on_dclick)
        self.itemClicked.connect(self._on_click)
        self.model().rowsMoved.connect(self._on_rows_moved)
        self._suppress_move = False

    def set_document(self, doc: PDFDocument):
        self.doc = doc
        self.reload()

    def reload(self):
        self._suppress_move = True
        self.clear()
        if not self.doc or not self.doc.is_open:
            self._suppress_move = False
            return
        for i in range(self.doc.page_count):
            self.addItem(self._make_item(i))
        self._suppress_move = False

    def refresh_page(self, page_idx: int):
        if not self.doc or page_idx < 0 or page_idx >= self.count():
            return
        new = self._make_item(page_idx)
        self._suppress_move = True
        self.takeItem(page_idx)
        self.insertItem(page_idx, new)
        self._suppress_move = False

    def _make_item(self, page_idx: int) -> QListWidgetItem:
        page = self.doc.page(page_idx)
        dpr = max(1.0, float(self.devicePixelRatioF()))
        zoom = (THUMB_W / page.rect.width) * dpr
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format.Format_RGB888).copy()
        pm = QPixmap.fromImage(img)
        pm.setDevicePixelRatio(dpr)
        logical_h = pm.height() / dpr
        item = QListWidgetItem(QIcon(pm), f"  {page_idx + 1}")
        item.setData(Qt.ItemDataRole.UserRole, page_idx)
        item.setSizeHint(QSize(THUMB_W + 12, int(logical_h) + 24))
        return item

    def _on_click(self, item: QListWidgetItem):
        self.pageActivated.emit(item.data(Qt.ItemDataRole.UserRole))

    def _on_dclick(self, item: QListWidgetItem):
        self.pageActivated.emit(item.data(Qt.ItemDataRole.UserRole))

    def _on_rows_moved(self, parent, src_start, src_end, dst_parent, dst_row):
        if self._suppress_move:
            return
        src = src_start
        # Qt's destination row index counts the to-be-inserted slot
        dst = dst_row if dst_row < src else dst_row - 1
        self.reorderRequested.emit(src, dst)

    def _show_menu(self, pos):
        item = self.itemAt(pos)
        if item is None:
            return
        idx = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("Rotate 90° CW", lambda: self.rotatePage.emit(idx, 90))
        menu.addAction("Rotate 90° CCW", lambda: self.rotatePage.emit(idx, -90))
        menu.addAction("Rotate 180°", lambda: self.rotatePage.emit(idx, 180))
        menu.addSeparator()
        menu.addAction("Duplicate page", lambda: self.duplicatePage.emit(idx))
        menu.addAction("Insert blank before", lambda: self.insertBlankAt.emit(idx))
        menu.addAction("Insert blank after", lambda: self.insertBlankAt.emit(idx + 1))
        menu.addAction("Extract to new PDF…", lambda: self.extractPage.emit(idx))
        menu.addSeparator()
        menu.addAction("Delete page", lambda: self.deletePage.emit(idx))
        menu.exec(self.mapToGlobal(pos))
