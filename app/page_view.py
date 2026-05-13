"""PDF viewer: stacks pages in a QGraphicsScene, handles zoom, pan, and tool input."""
from __future__ import annotations

from typing import Optional

import fitz
from PyQt6.QtCore import Qt, QPointF, QRectF, pyqtSignal, QEvent
from PyQt6.QtGui import (
    QPixmap, QImage, QPainter, QPen, QColor, QBrush, QWheelEvent, QMouseEvent,
)
from PyQt6.QtWidgets import (
    QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QGraphicsItem,
    QGraphicsRectItem, QGraphicsPathItem, QGraphicsEllipseItem, QGraphicsLineItem,
)

from .document import PDFDocument


PAGE_GAP = 12  # pixels between pages
PAGE_BG = QColor(245, 245, 248)


class PageItem(QGraphicsPixmapItem):
    """One rendered PDF page. Stores its page index, PDF-points size, and
    the *logical* (device-independent) display size of its pixmap."""

    def __init__(self, page_index: int, pdf_w: float, pdf_h: float):
        super().__init__()
        self.page_index = page_index
        self.pdf_w = pdf_w
        self.pdf_h = pdf_h
        self.display_w = 0.0
        self.display_h = 0.0
        self.setAcceptHoverEvents(False)
        self.setShapeMode(QGraphicsPixmapItem.ShapeMode.BoundingRectShape)
        self.setTransformationMode(Qt.TransformationMode.SmoothTransformation)

    def set_rendered(self, pm: QPixmap):
        self.setPixmap(pm)
        dpr = pm.devicePixelRatio() or 1.0
        self.display_w = pm.width() / dpr
        self.display_h = pm.height() / dpr


class PageView(QGraphicsView):
    """Stacks all PDF pages vertically, dispatches input to the active tool."""

    pageChanged = pyqtSignal(int)        # current page index
    zoomChanged = pyqtSignal(float)      # current zoom factor
    pageClicked = pyqtSignal(int, QPointF)  # page_index, pdf_point
    pageDragged = pyqtSignal(int, QPointF, QPointF)  # page_index, pdf_p1, pdf_p2
    inkStrokeFinished = pyqtSignal(int, list)  # page_index, [pdf_points]
    textRectFinished = pyqtSignal(int, object)  # page_index, fitz.Rect
    rectSelected = pyqtSignal(int, object)  # page_index, fitz.Rect (text selection rect)

    MIN_ZOOM = 0.25
    MAX_ZOOM = 6.0

    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setBackgroundBrush(PAGE_BG)
        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
        )
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)

        self.doc: Optional[PDFDocument] = None
        self.zoom: float = 1.0
        self.page_items: list[PageItem] = []

        # Tool state
        self.tool: str = "hand"
        self.tool_color = (1, 1, 0)         # PDF color (0..1)
        self.tool_stroke_width: float = 1.5
        self.tool_fontsize: int = 12

        # transient drawing state
        self._drag_start_scene: Optional[QPointF] = None
        self._drag_start_page: Optional[int] = None
        self._preview_item: Optional[QGraphicsItem] = None
        self._ink_points: list[tuple[float, float]] = []
        self._panning = False
        self._pan_anchor = None

        self.verticalScrollBar().valueChanged.connect(self._update_current_page)

    # ---------- document binding ----------
    def set_document(self, doc: PDFDocument):
        self.doc = doc
        self.reload()

    def reload(self):
        """Re-render every page (use sparingly — call refresh_page for single-page edits)."""
        self._scene.clear()
        self.page_items.clear()
        if not self.doc or not self.doc.is_open:
            return
        y = 0.0
        max_w = 0.0
        for i in range(self.doc.page_count):
            item = self._make_page_item(i)
            item.setPos(0, y)
            self._scene.addItem(item)
            self.page_items.append(item)
            y += item.display_h + PAGE_GAP
            max_w = max(max_w, item.display_w)
        # center all pages horizontally
        for item in self.page_items:
            item.setX((max_w - item.display_w) / 2)
        self._scene.setSceneRect(0, 0, max_w, max(y - PAGE_GAP, 1))
        self._update_current_page()

    def refresh_page(self, page_idx: int):
        """Re-render a single page's pixmap in place."""
        if not self.doc or page_idx < 0 or page_idx >= len(self.page_items):
            return
        item = self.page_items[page_idx]
        item.set_rendered(self._render_pixmap(page_idx))

    def _device_pixel_ratio(self) -> float:
        # Honor the screen the viewport is on, including HiDPI / fractional scaling.
        try:
            dpr = self.viewport().devicePixelRatioF()
        except AttributeError:
            dpr = self.devicePixelRatioF()
        return max(1.0, float(dpr))

    def _render_pixmap(self, page_idx: int) -> QPixmap:
        page = self.doc.page(page_idx)
        dpr = self._device_pixel_ratio()
        render_scale = self.zoom * dpr
        mat = fitz.Matrix(render_scale, render_scale)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        img = QImage(pix.samples, pix.width, pix.height, pix.stride,
                     QImage.Format.Format_RGB888)
        pm = QPixmap.fromImage(img.copy())
        pm.setDevicePixelRatio(dpr)
        return pm

    def _make_page_item(self, page_idx: int) -> PageItem:
        page = self.doc.page(page_idx)
        rect = page.rect
        item = PageItem(page_idx, rect.width, rect.height)
        item.set_rendered(self._render_pixmap(page_idx))
        return item

    # ---------- zoom ----------
    def set_zoom(self, z: float):
        z = max(self.MIN_ZOOM, min(self.MAX_ZOOM, z))
        if abs(z - self.zoom) < 1e-3:
            return
        # remember current scroll position as a PDF-space y-offset within the current page
        cur_page = self.current_page()
        cur_item = self.page_items[cur_page] if self.page_items else None
        pdf_y_in_page = 0.0
        if cur_item is not None:
            scene_y = self.mapToScene(self.viewport().rect().center()).y()
            pdf_y_in_page = (scene_y - cur_item.y()) / self.zoom

        self.zoom = z
        self.reload()
        self.zoomChanged.emit(self.zoom)

        if cur_item is not None and cur_page < len(self.page_items):
            new_item = self.page_items[cur_page]
            target_scene_y = new_item.y() + pdf_y_in_page * self.zoom
            self.centerOn(self._scene.width() / 2, target_scene_y)

    def zoom_in(self):
        self.set_zoom(self.zoom * 1.25)

    def zoom_out(self):
        self.set_zoom(self.zoom / 1.25)

    def fit_width(self):
        if not self.page_items:
            return
        max_pdf_w = max(p.pdf_w for p in self.page_items)
        view_w = self.viewport().width() - 24
        self.set_zoom(view_w / max_pdf_w)

    def fit_page(self):
        if not self.page_items:
            return
        item = self.page_items[self.current_page()]
        zw = (self.viewport().width() - 24) / item.pdf_w
        zh = (self.viewport().height() - 24) / item.pdf_h
        self.set_zoom(min(zw, zh))

    # ---------- navigation ----------
    def current_page(self) -> int:
        if not self.page_items:
            return 0
        center_y = self.mapToScene(self.viewport().rect().center()).y()
        for item in self.page_items:
            top = item.y()
            bot = top + item.display_h
            if top <= center_y <= bot:
                return item.page_index
        # fallback: nearest
        return min(self.page_items, key=lambda it: abs(it.y() - center_y)).page_index

    def goto_page(self, page_idx: int):
        if 0 <= page_idx < len(self.page_items):
            item = self.page_items[page_idx]
            self.centerOn(self._scene.width() / 2,
                          item.y() + item.display_h / 2)

    def _update_current_page(self):
        self.pageChanged.emit(self.current_page())

    # ---------- tool & coordinate helpers ----------
    def set_tool(self, name: str):
        self.tool = name
        if name == "hand":
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.viewport().setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            self.viewport().setCursor(Qt.CursorShape.CrossCursor)

    def _hit_page(self, scene_pos: QPointF) -> tuple[Optional[PageItem], Optional[QPointF]]:
        for item in self.page_items:
            r = QRectF(item.x(), item.y(), item.display_w, item.display_h)
            if r.contains(scene_pos):
                local = scene_pos - item.pos()
                return item, QPointF(local.x() / self.zoom, local.y() / self.zoom)
        return None, None

    # ---------- mouse handling ----------
    def wheelEvent(self, ev: QWheelEvent):
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if ev.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            ev.accept()
            return
        super().wheelEvent(ev)

    def mousePressEvent(self, ev: QMouseEvent):
        if ev.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._pan_anchor = ev.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            ev.accept()
            return
        if self.tool == "hand":
            return super().mousePressEvent(ev)

        scene_pos = self.mapToScene(ev.position().toPoint())
        item, pdf_pt = self._hit_page(scene_pos)
        if not item:
            return super().mousePressEvent(ev)

        self._drag_start_scene = scene_pos
        self._drag_start_page = item.page_index

        if self.tool == "ink":
            self._ink_points = [(pdf_pt.x(), pdf_pt.y())]
            # start preview path in scene
            from PyQt6.QtGui import QPainterPath
            path = QPainterPath(scene_pos)
            self._preview_item = self._scene.addPath(
                path, QPen(self._qcolor(), max(1.0, self.tool_stroke_width * self.zoom))
            )
        elif self.tool in ("rect", "ellipse", "line", "arrow", "freetext",
                           "highlight", "underline", "strikeout", "squiggly",
                           "redact", "image_rect"):
            pen = QPen(self._qcolor(), max(1.0, self.tool_stroke_width * self.zoom),
                       Qt.PenStyle.DashLine)
            if self.tool in ("rect", "freetext", "redact", "image_rect",
                             "highlight", "underline", "strikeout", "squiggly"):
                self._preview_item = self._scene.addRect(QRectF(scene_pos, scene_pos), pen)
            elif self.tool == "ellipse":
                self._preview_item = self._scene.addEllipse(QRectF(scene_pos, scene_pos), pen)
            else:  # line, arrow
                self._preview_item = self._scene.addLine(
                    scene_pos.x(), scene_pos.y(), scene_pos.x(), scene_pos.y(), pen
                )
        elif self.tool == "sticky":
            self.pageClicked.emit(item.page_index, pdf_pt)
            self._drag_start_scene = None  # one-shot
        elif self.tool == "text":
            self.pageClicked.emit(item.page_index, pdf_pt)
            self._drag_start_scene = None
        elif self.tool == "image_point":
            self.pageClicked.emit(item.page_index, pdf_pt)
            self._drag_start_scene = None
        elif self.tool == "edit_text":
            self.pageClicked.emit(item.page_index, pdf_pt)
            self._drag_start_scene = None
        else:
            super().mousePressEvent(ev)
        ev.accept()

    def mouseMoveEvent(self, ev: QMouseEvent):
        if self._panning and self._pan_anchor is not None:
            delta = ev.position() - self._pan_anchor
            self._pan_anchor = ev.position()
            h = self.horizontalScrollBar(); v = self.verticalScrollBar()
            h.setValue(h.value() - int(delta.x()))
            v.setValue(v.value() - int(delta.y()))
            ev.accept(); return

        if self._drag_start_scene is None:
            return super().mouseMoveEvent(ev)

        scene_pos = self.mapToScene(ev.position().toPoint())

        if self.tool == "ink" and self._preview_item is not None:
            item, pdf_pt = self._hit_page(scene_pos)
            if item and item.page_index == self._drag_start_page:
                self._ink_points.append((pdf_pt.x(), pdf_pt.y()))
                from PyQt6.QtGui import QPainterPath
                first_x, first_y = self._ink_points[0]
                path = QPainterPath(QPointF(item.x() + first_x * self.zoom,
                                            item.y() + first_y * self.zoom))
                for x, y in self._ink_points[1:]:
                    path.lineTo(item.x() + x * self.zoom, item.y() + y * self.zoom)
                self._preview_item.setPath(path)
        elif self._preview_item is not None:
            r = QRectF(self._drag_start_scene, scene_pos).normalized()
            if isinstance(self._preview_item, QGraphicsRectItem):
                self._preview_item.setRect(r)
            elif isinstance(self._preview_item, QGraphicsEllipseItem):
                self._preview_item.setRect(r)
            elif isinstance(self._preview_item, QGraphicsLineItem):
                self._preview_item.setLine(
                    self._drag_start_scene.x(), self._drag_start_scene.y(),
                    scene_pos.x(), scene_pos.y()
                )
        ev.accept()

    def mouseReleaseEvent(self, ev: QMouseEvent):
        if self._panning and ev.button() == Qt.MouseButton.MiddleButton:
            self._panning = False
            self._pan_anchor = None
            self.viewport().setCursor(
                Qt.CursorShape.OpenHandCursor if self.tool == "hand" else Qt.CursorShape.CrossCursor
            )
            ev.accept(); return

        if self._drag_start_scene is None:
            return super().mouseReleaseEvent(ev)

        scene_pos = self.mapToScene(ev.position().toPoint())
        page_idx = self._drag_start_page

        # remove preview
        if self._preview_item is not None:
            self._scene.removeItem(self._preview_item)
            self._preview_item = None

        item = self.page_items[page_idx] if 0 <= page_idx < len(self.page_items) else None
        if item is None:
            self._drag_start_scene = None
            return

        p1_local = (self._drag_start_scene - item.pos())
        p2_local = (scene_pos - item.pos())
        p1_pdf = QPointF(p1_local.x() / self.zoom, p1_local.y() / self.zoom)
        p2_pdf = QPointF(p2_local.x() / self.zoom, p2_local.y() / self.zoom)

        if self.tool == "ink":
            if len(self._ink_points) >= 2:
                self.inkStrokeFinished.emit(page_idx, list(self._ink_points))
            self._ink_points = []
        elif self.tool in ("rect", "ellipse"):
            r = fitz.Rect(
                min(p1_pdf.x(), p2_pdf.x()), min(p1_pdf.y(), p2_pdf.y()),
                max(p1_pdf.x(), p2_pdf.x()), max(p1_pdf.y(), p2_pdf.y()),
            )
            if r.width > 2 and r.height > 2:
                self.textRectFinished.emit(page_idx, ("shape", r))
        elif self.tool in ("line", "arrow"):
            self.pageDragged.emit(page_idx, p1_pdf, p2_pdf)
        elif self.tool == "freetext":
            r = fitz.Rect(
                min(p1_pdf.x(), p2_pdf.x()), min(p1_pdf.y(), p2_pdf.y()),
                max(p1_pdf.x(), p2_pdf.x()), max(p1_pdf.y(), p2_pdf.y()),
            )
            if r.width > 5 and r.height > 5:
                self.textRectFinished.emit(page_idx, ("freetext", r))
        elif self.tool == "image_rect":
            r = fitz.Rect(
                min(p1_pdf.x(), p2_pdf.x()), min(p1_pdf.y(), p2_pdf.y()),
                max(p1_pdf.x(), p2_pdf.x()), max(p1_pdf.y(), p2_pdf.y()),
            )
            if r.width > 5 and r.height > 5:
                self.textRectFinished.emit(page_idx, ("image", r))
        elif self.tool == "redact":
            r = fitz.Rect(
                min(p1_pdf.x(), p2_pdf.x()), min(p1_pdf.y(), p2_pdf.y()),
                max(p1_pdf.x(), p2_pdf.x()), max(p1_pdf.y(), p2_pdf.y()),
            )
            if r.width > 2 and r.height > 2:
                self.textRectFinished.emit(page_idx, ("redact", r))
        elif self.tool in ("highlight", "underline", "strikeout", "squiggly"):
            r = fitz.Rect(
                min(p1_pdf.x(), p2_pdf.x()), min(p1_pdf.y(), p2_pdf.y()),
                max(p1_pdf.x(), p2_pdf.x()), max(p1_pdf.y(), p2_pdf.y()),
            )
            if r.width > 2 and r.height > 2:
                self.textRectFinished.emit(page_idx, (self.tool, r))

        self._drag_start_scene = None
        self._drag_start_page = None
        ev.accept()

    def _qcolor(self) -> QColor:
        r, g, b = self.tool_color
        return QColor(int(r * 255), int(g * 255), int(b * 255))
