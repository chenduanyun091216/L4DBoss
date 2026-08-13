import re

SRC = "l4d2_mod_manager/app.py"
lines = open(SRC, encoding="utf-8").read().splitlines()

COMMON_HEADER = '''from __future__ import annotations

import os
import sys
import time
import subprocess
import ctypes
import re
try:
    import winreg
except ImportError:  # pragma: no cover - only relevant on non-Windows tooling
    winreg = None
from html import escape
from collections import Counter
from copy import deepcopy
from pathlib import Path
from threading import Event

from PyQt5.QtCore import QEvent, QObject, QPoint, QRunnable, QSize, QTimer, QUrl, Qt, QThreadPool, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QFont, QIcon, QLinearGradient, QPainter, QPixmap
from PyQt5.QtWidgets import (
    QAction, QApplication, QComboBox, QDialog, QFileDialog, QFrame, QGridLayout,
    QHBoxLayout, QInputDialog, QLabel, QLineEdit, QMainWindow, QMenu, QMessageBox,
    QAbstractItemView, QProgressBar, QPushButton, QScrollArea, QSizeGrip, QSizePolicy, QSplitter, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget, QWidgetAction,
)

from .categories import CATEGORIES, SIMPLE_CATEGORIES, infer_categories, simple_categories
from .collection_sync import delete_collection_folder, restore_collection_files, sync_collection_files
from .models import Mod, ModCollection
from .steam_client import SteamClient
from .storage import AppStorage
from .vpk_scanner import is_conflict_relevant_path, scan_mod_directory
from .theme import *
from .components import *
'''

# 提取 MainWindow 内方法行范围
mw_start = 1792  # 0-based of line 1793
mw_end = 4028
starts = []
for i in range(mw_start, mw_end):
    if re.match(r"^    def ", lines[i]):
        starts.append(i)
methods = {}
for idx, s in enumerate(starts):
    e = starts[idx + 1] if idx + 1 < len(starts) else mw_end
    while lines[e - 1].strip() == "" and e > s + 1:
        e -= 1
    name = re.match(r"^    def (\w+)", lines[s]).group(1)
    methods[name] = (s, e)

def block(a, b):
    return "\n".join(lines[a - 1:b]) + "\n"

THEME_RANGE = (1, 483)
COMP_RANGES = [
    (486, 509), (512, 558), (561, 620), (623, 881), (884, 984), (987, 1013),
    (1016, 1047), (1050, 1066), (1069, 1104), (1107, 1172), (1175, 1333),
    (1336, 1389), (1392, 1470), (1473, 1518), (1521, 1562), (1565, 1585),
    (1588, 1591), (1594, 1620), (1623, 1645), (1648, 1749), (1752, 1790),
    (4030, 4036), (4039, 4067), (4070, 4074), (4077, 4083), (4086, 4106), (4109, 4113),
]

GROUPS = {
    "main_window_build": [
        "_build_ui", "_build_header", "_header_button", "_window_control_button",
        "toggle_maximized", "restore_default_window", "_show_header_hint",
        "_clear_header_hint", "_update_theme_button", "_theme_icon", "_open_theme_menu",
        "_set_theme", "_launch_icon", "_build_content_bar", "_build_footer_legacy",
        "_make_mod_count_button", "_build_footer", "_apply_style",
    ],
    "main_window_cards": [
        "_rebuild_conflict_index", "_refresh_conflicts_from_index",
        "_update_conflicts_for_toggle", "_refresh_card_states", "refresh_tree",
        "_make_tree_item", "on_category_mode_switch_changed", "refresh_cards",
        "_populate_cards_batch", "_update_pagination", "change_page", "on_search_changed",
        "_change_card_size", "_release_card_size_alignment", "_columns_for_card_size",
        "card_columns", "card_width", "_card_viewport_width", "_show_cards_loading",
        "_hide_cards_loading", "_advance_cards_loading_spinner", "_schedule_cards_refresh",
        "_refresh_cards_after_layout", "_reflow_cards", "_sync_content_right_edges",
        "_schedule_window_state_alignment", "_align_window_state",
        "_schedule_content_alignment", "_set_status_selection", "_update_mod_filter_title",
        "_simple_categories_for", "_time_sort_key",
    ],
    "main_window_mods": [
        "choose_directory", "find_steam_game_executable", "addon_directories",
        "configured_addon_directories", "scan_mods", "reset_mods", "on_scan_finished",
        "launch_game", "steam_is_installed", "fetch_steam_info",
        "set_all_mods_active", "toggle_all_mods", "toggle_mod", "toggle_favorite",
    ],
    "main_window_collections": [
        "collection_names_for", "add_mod_to_collection", "sync_collection_in_background",
        "_on_collection_sync_failed", "filtered_mods", "refresh_collection_combo",
        "_update_collection_combo_label", "on_collection_selection_changed",
        "restore_selected_collections_in_background", "on_collection_restore_finished",
        "on_collection_restore_failed", "_set_collection_restore_status",
        "_apply_pending_collection_selection", "save_selected_collection_names",
        "apply_selected_collections", "delete_collection", "on_category_selected",
        "_run_category_refresh", "save_collection", "save_collection_as_new",
        "confirm_save_collection", "write_addonlist",
    ],
    "main_window_steam": [
        "sync_single_mod_steam", "on_steam_finished", "on_steam_failed",
        "cancel_steam_sync", "on_steam_cancelled", "_reset_steam_sync_controls",
        "_set_steam_sync_status",
    ],
    "main_window_conflicts": [
        "show_conflicts", "_build_conflict_report", "_add_conflict_report_group",
        "_show_completed_conflict_report", "disable_conflict_mod",
    ],
    "main_window_details": [
        "show_card_context_menu", "open_mod_source", "delete_mod",
        "_show_content_widget", "show_mod_details", "show_mod_list",
        "show_active_mods", "show_all_mods",
    ],
    "main_window_events": [
        "on_worker_failed", "set_busy", "closeEvent", "resizeEvent",
        "showEvent", "_apply_native_window_corner",
    ],
}

CORE_METHODS = [
    "__init__", "_reclassify_loaded_mods", "_apply_steam_cache", "_save_steam_cache",
    "show_about", "eventFilter", "refresh_stats",
]

assigned = set(sum(GROUPS.values(), [])) | set(CORE_METHODS)
missing = set(methods) - assigned
assert not missing, f"未分配方法: {missing}"

# theme.py
with open("l4d2_mod_manager/theme.py", "w", encoding="utf-8") as f:
    f.write(block(*THEME_RANGE))

# components.py (with header)
with open("l4d2_mod_manager/components.py", "w", encoding="utf-8") as f:
    f.write(COMMON_HEADER)
    f.write("\n")
    for r in COMP_RANGES:
        f.write(block(*r))

# methods of a group as module-level functions (strip class indent)
def method_block(name):
    a, b = methods[name]
    txt = block(a, b)
    # remove 4-space leading indent from each line
    out = []
    for ln in txt.split("\n"):
        if ln.startswith("    "):
            out.append(ln[4:])
        else:
            out.append(ln)
    return "\n".join(out).rstrip() + "\n"

# write group modules
for mod, names in GROUPS.items():
    with open(f"l4d2_mod_manager/{mod}.py", "w", encoding="utf-8") as f:
        f.write(COMMON_HEADER)
        f.write("\n")
        for n in names:
            f.write(method_block(n))
            f.write("\n")

# write main_window.py: header + core methods as class body + bind the rest
bind_lines = [COMMON_HEADER, ""]
for n in CORE_METHODS:
    bind_lines.append(block(*methods[n]).rstrip())
bind_lines.append("")
bind_lines.append("")
for mod, names in GROUPS.items():
    bind_lines.append(f"from .{mod} import (")
    for n in names:
        bind_lines.append(f"    {n},")
    bind_lines.append(")")
    bind_lines.append("")
    for n in names:
        bind_lines.append(f"MainWindow.{n} = {n}")
    bind_lines.append("")
bind_lines.append("")
with open("l4d2_mod_manager/main_window.py", "w", encoding="utf-8") as f:
    f.write("\n".join(bind_lines))

print("拆分完成")
