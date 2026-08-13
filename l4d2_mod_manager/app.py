"""L4D2 Boss 应用入口。

本模块只负责启动应用，所有 UI 实现分散在 ``main_window`` 及其子模块中，
独立组件位于 ``components``，主题与样式位于 ``theme``。
"""

from __future__ import annotations

import sys

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import QApplication

from .main_window import MainWindow
from .theme import TITLE_ICON


def main() -> int:
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(TITLE_ICON)))
    window = MainWindow()
    window.show()
    return app.exec_()
