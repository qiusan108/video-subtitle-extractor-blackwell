"""Shared VelaRec-inspired palette for the Subtitle Studio desktop UI."""

from PySide6.QtCore import Qt, QRect
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette
from qfluentwidgets import (
    CardWidget, NavigationWidget, Theme, setTheme, setThemeColor,
)


WINDOW = '#0B1017'
SIDEBAR = '#101721'
PANEL = '#131A24'
ELEVATED = '#192331'
BORDER = '#293647'
TEXT = '#F3F6FA'
MUTED = '#A3B0C0'
ACCENT = '#75AFFF'


class StudioBrandCard(NavigationWidget):
    """Compact brand header that fits the permanently expanded sidebar."""

    def __init__(self, icon_path, title, subtitle, parent=None):
        super().__init__(False, parent)
        self._icon = QIcon(str(icon_path))
        self._title = title
        self._subtitle = subtitle

    def setCompacted(self, is_compacted):
        self.isCompacted = is_compacted
        self.setFixedSize(40 if is_compacted else self.EXPAND_WIDTH, 72)
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.drawPixmap(8, 18, self._icon.pixmap(34, 34))
        if self.isCompacted:
            return
        painter.setPen(QColor(TEXT))
        painter.setFont(QFont('Microsoft YaHei', 11, QFont.Weight.DemiBold))
        painter.drawText(QRect(50, 13, self.width() - 54, 26),
                         Qt.AlignmentFlag.AlignVCenter, self._title)
        painter.setPen(QColor(MUTED))
        painter.setFont(QFont('Microsoft YaHei', 8))
        painter.drawText(QRect(50, 37, self.width() - 54, 20),
                         Qt.AlignmentFlag.AlignVCenter, self._subtitle)


def apply_brand_theme(app):
    """Set a readable dark base without overriding Fluent control styles."""
    setTheme(Theme.DARK)
    setThemeColor(QColor(ACCENT))
    app.setFont(QFont('Microsoft YaHei', 10))
    palette = app.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor(WINDOW))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(PANEL))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(ELEVATED))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(ELEVATED))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(ACCENT))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(MUTED))
    app.setPalette(palette)


def style_cards(root):
    """Give existing Fluent cards the same navy surfaces as VelaRec."""
    for card in root.findChildren(CardWidget):
        card.setObjectName('studioCard')
        card.setStyleSheet(
            f'CardWidget#studioCard {{ background-color: {PANEL}; '
            f'border: 1px solid {BORDER}; border-radius: 12px; }}'
        )
