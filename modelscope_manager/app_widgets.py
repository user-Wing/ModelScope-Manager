"""资源页和传输页复用的 Qt 控件。"""

from __future__ import annotations

from PySide6.QtCore import QPoint, QPointF, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPalette, QPen, QPixmap, QPolygonF
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QMenu, QPushButton, QToolTip, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
from datetime import datetime
from .app_helpers import breadcrumb_levels
from .service import RemoteEntry
from .transfer_statistics import TransferSample


class SkinBackgroundLayer(QWidget):
    """Cached, cover-scaled window wallpaper with a cheap live dimmer."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._path = ""
        self._source = QPixmap()
        self._scaled = QPixmap()
        self._scaled_size = self.size()
        self._brightness = 100
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.hide()

    def set_background(self, path: str, brightness: int = 100) -> None:
        normalized = str(path or "")
        if normalized != self._path:
            self._path = normalized
            self._source = QPixmap(normalized) if normalized else QPixmap()
            self._scaled = QPixmap()
        self._brightness = min(180, max(20, int(brightness)))
        self.setVisible(not self._source.isNull())
        self.lower()
        self.update()

    def set_brightness(self, brightness: int) -> None:
        value = min(180, max(20, int(brightness)))
        if value == self._brightness:
            return
        self._brightness = value
        self.update()

    def resizeEvent(self, event) -> None:
        self._scaled = QPixmap()
        self._scaled_size = event.size()
        super().resizeEvent(event)

    def paintEvent(self, event) -> None:
        if self._source.isNull():
            return
        if self._scaled.isNull() or self._scaled_size != self.size():
            self._scaled_size = self.size()
            self._scaled = self._source.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation,
            )
        painter = QPainter(self)
        x = (self.width() - self._scaled.width()) // 2
        y = (self.height() - self._scaled.height()) // 2
        painter.drawPixmap(x, y, self._scaled)
        # Brightness 100 intentionally retains a readable Codex-like dim layer.
        dim_alpha = round((180 - self._brightness) * 210 / 160)
        if dim_alpha > 0:
            painter.fillRect(self.rect(), QColor(8, 9, 12, min(210, dim_alpha)))


class TransferChart(QWidget):
    def __init__(self, direction: str, color: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.direction = direction
        self.color = QColor(color)
        self.samples: list[TransferSample] = []
        self.start_time = 0.0
        self.end_time = 1.0
        self.setMinimumHeight(210)

    def set_data(self, samples: list[TransferSample], start_time: float, end_time: float) -> None:
        self.samples = samples
        self.start_time = float(start_time)
        self.end_time = max(self.start_time + 1, float(end_time))
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        text_color = self.palette().color(QPalette.ColorRole.Text)
        muted_color = self.palette().color(QPalette.ColorRole.Mid)
        left, top, right, bottom = 78, 28, 24, 42
        width = max(1, self.width() - left - right)
        height = max(1, self.height() - top - bottom)
        speeds = [
            sample.upload_speed if self.direction == "upload" else sample.download_speed
            for sample in self.samples
        ]
        maximum = max(speeds, default=0.0)
        unit_size, unit = self._speed_unit(maximum)
        axis_max = max(unit_size, maximum)

        painter.setPen(QPen(muted_color, 1))
        for index in range(5):
            y = top + height * index / 4
            painter.drawLine(left, int(y), left + width, int(y))
            label = f"{axis_max * (4 - index) / 4 / unit_size:.1f}"
            painter.setPen(text_color)
            painter.drawText(4, int(y - 9), left - 14, 18, Qt.AlignmentFlag.AlignRight, label)
            painter.setPen(QPen(muted_color, 1))

        start_label = datetime.fromtimestamp(self.start_time).strftime("%m-%d %H:%M")
        end_label = datetime.fromtimestamp(self.end_time).strftime("%m-%d %H:%M")
        painter.setPen(text_color)
        painter.drawText(left, top + height + 10, width // 2, 20, Qt.AlignmentFlag.AlignLeft, start_label)
        painter.drawText(left + width // 2, top + height + 10, width // 2, 20, Qt.AlignmentFlag.AlignRight, end_label)
        painter.drawText(4, 4, left - 38, 18, Qt.AlignmentFlag.AlignRight, unit)

        if speeds:
            points = QPolygonF()
            duration = self.end_time - self.start_time
            for sample, speed in zip(self.samples, speeds):
                x = left + width * (sample.timestamp - self.start_time) / duration
                y = top + height * (1 - speed / axis_max)
                points.append(QPointF(x, y))
            painter.setPen(QPen(self.color, 2))
            painter.drawPolyline(points)

    @staticmethod
    def _speed_unit(maximum: float) -> tuple[float, str]:
        units = ((1024 ** 3, "GB/s"), (1024 ** 2, "MB/s"), (1024, "KB/s"))
        for size, name in units:
            if maximum >= size:
                return float(size), name
        return 1.0, "B/s"

def _dropped_local_paths(event) -> list[str]:
    if not event.mimeData().hasUrls():
        return []
    return [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]


def _upload_target_text(path: str) -> str:
    return f"上传到 /{path}" if path else "上传到根目录"


class _BreadcrumbDropButton(QPushButton):
    paths_dropped = Signal(list, str)

    def __init__(self, text: str, target: str):
        super().__init__(text)
        self.target = target
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if _dropped_local_paths(event):
            QToolTip.showText(self.mapToGlobal(self.rect().bottomLeft()), _upload_target_text(self.target), self)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if _dropped_local_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        paths = _dropped_local_paths(event)
        QToolTip.hideText()
        if paths:
            self.paths_dropped.emit(paths, self.target)
            event.acceptProposedAction()
        else:
            event.ignore()


class _BreadcrumbDropMenu(QMenu):
    paths_dropped = Signal(list, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event) -> None:
        if _dropped_local_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        action = self.actionAt(event.position().toPoint())
        if action is not None and _dropped_local_paths(event):
            target = str(action.data() or "")
            QToolTip.showText(self.mapToGlobal(event.position().toPoint()), _upload_target_text(target), self)
            self.setActiveAction(action)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:
        action = self.actionAt(event.position().toPoint())
        paths = _dropped_local_paths(event)
        QToolTip.hideText()
        if action is not None and paths:
            self.paths_dropped.emit(paths, str(action.data() or ""))
            self.close()
            event.acceptProposedAction()
        else:
            event.ignore()


class _BreadcrumbOverflowButton(QPushButton):
    def dragEnterEvent(self, event) -> None:
        if _dropped_local_paths(event) and self.menu() is not None:
            self.menu().popup(self.mapToGlobal(QPoint(0, self.height())))
            event.acceptProposedAction()
        else:
            event.ignore()


class PathBreadcrumb(QFrame):
    path_selected = Signal(str)
    paths_dropped = Signal(list, str)

    def __init__(self):
        super().__init__()
        self.setObjectName("pathPill")
        self._path = ""
        self._root_text = "根目录"
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(6, 2, 6, 2)
        self._layout.setSpacing(1)
        self.overflow_button = _BreadcrumbOverflowButton("…")
        self.overflow_button.setAcceptDrops(True)
        self._rebuild()

    def set_path(self, path: str, root_text: str = "根目录") -> None:
        self._path = path.replace("\\", "/").strip("/")
        self._root_text = root_text
        self.setToolTip(f"/ {self._path}" if self._path else f"/ {root_text}")
        self._rebuild()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._rebuild()

    def _rebuild(self) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        levels = breadcrumb_levels(self._path, self._root_text)
        metrics = self.fontMetrics()

        def width(label: str) -> int:
            return max(42, min(220, metrics.horizontalAdvance(label) + 24))

        available = max(80, self.width() - 12)
        full_width = sum(width(label) for label, _ in levels) + max(0, len(levels) - 1) * 14
        visible_start = 0
        if full_width > available:
            visible_start = len(levels) - 1
            used = width(levels[-1][0])
            budget = max(42, available - 48)
            while visible_start > 0:
                previous = width(levels[visible_start - 1][0]) + 14
                if used + previous > budget:
                    break
                visible_start -= 1
                used += previous

        hidden = levels[:visible_start]
        self.overflow_button = _BreadcrumbOverflowButton("…")
        self.overflow_button.setAcceptDrops(True)
        self.overflow_button.setObjectName("breadcrumbButton")
        self.overflow_button.setToolTip("选择上级目录")
        if hidden:
            menu = _BreadcrumbDropMenu(self.overflow_button)
            menu.paths_dropped.connect(self.paths_dropped)
            for label, target in hidden:
                action = menu.addAction(label)
                action.setData(target)
                action.triggered.connect(
                    lambda checked=False, value=target: self.path_selected.emit(value)
                )
            self.overflow_button.setMenu(menu)
            self._layout.addWidget(self.overflow_button)
        else:
            self.overflow_button.setVisible(False)

        for offset, (label, target) in enumerate(levels[visible_start:]):
            if offset or hidden:
                separator = QLabel("›")
                separator.setObjectName("breadcrumbSeparator")
                self._layout.addWidget(separator)
            button = _BreadcrumbDropButton(label, target)
            button.setObjectName("breadcrumbButton")
            button.setProperty("breadcrumbPath", target)
            button.setMaximumWidth(width(label))
            button.clicked.connect(
                lambda checked=False, value=target: self.path_selected.emit(value)
            )
            button.paths_dropped.connect(self.paths_dropped)
            self._layout.addWidget(button)
        self._layout.addStretch(1)

class RepositoryTree(QTreeWidget):
    paths_dropped = Signal(list, object)

    def __init__(self):
        super().__init__()
        self.drop_directory: RemoteEntry | None = None
        self.setAcceptDrops(True)

    def set_drop_directory(self, directory: RemoteEntry | None) -> None:
        self.drop_directory = directory if directory and directory.is_dir else None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        item = self.itemAt(event.position().toPoint())
        target = self._directory_item(item)
        directory = target.data(0, Qt.ItemDataRole.UserRole) if target is not None else self.drop_directory
        if isinstance(directory, RemoteEntry) and directory.is_dir and event.mimeData().hasUrls():
            if target is not None:
                self.setCurrentItem(target)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        item = self.itemAt(event.position().toPoint())
        target = self._directory_item(item)
        entry = target.data(0, Qt.ItemDataRole.UserRole) if target is not None else self.drop_directory
        if not isinstance(entry, RemoteEntry) or not entry.is_dir:
            event.ignore()
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths, entry)
            event.acceptProposedAction()

    @staticmethod
    def _directory_item(item: QTreeWidgetItem | None) -> QTreeWidgetItem | None:
        while item is not None:
            entry = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(entry, RemoteEntry) and entry.is_dir:
                return item
            item = item.parent()
        return None

class RepositoryList(QListWidget):
    paths_dropped = Signal(list, object)

    def __init__(self):
        super().__init__()
        self.drop_directory: RemoteEntry | None = None
        self.setAcceptDrops(True)

    def set_drop_directory(self, directory: RemoteEntry | None) -> None:
        self.drop_directory = directory if directory and directory.is_dir else None

    def _drop_directory_at(self, position) -> RemoteEntry | None:
        item = self.itemAt(position)
        entry = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return entry if isinstance(entry, RemoteEntry) and entry.is_dir else self.drop_directory

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if self._drop_directory_at(event.position().toPoint()) is not None and event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        directory = self._drop_directory_at(event.position().toPoint())
        if directory is None:
            event.ignore()
            return
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths, directory)
            event.acceptProposedAction()

class DropArea(QFrame):
    paths_dropped = Signal(list)

    def __init__(self, title_text: str = "将文件或文件夹拖到这里", note_text: str = "也可以使用下方按钮选择，可一次添加多个项目"):
        super().__init__()
        self.setObjectName("dropArea")
        self.setProperty("dragging", False)
        self.setAcceptDrops(True)
        self.setMinimumHeight(112)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon = QLabel("⇩")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setStyleSheet("font-size: 31px; color: #0067c0; font-weight: 300;")
        title = QLabel(title_text)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-size: 15px; font-weight: 600;")
        note = QLabel(note_text)
        note.setObjectName("subtitle")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(icon)
        layout.addWidget(title)
        layout.addWidget(note)
        for child in (icon, title, note):
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def _set_dragging(self, value: bool) -> None:
        self.setProperty("dragging", value)
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
            self._set_dragging(True)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls() and all(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        self._set_dragging(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._set_dragging(False)
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        if paths:
            self.paths_dropped.emit(paths)
            event.acceptProposedAction()
