import ast
import sys

CARDS = r"d:\dev\L4DBoss\l4d2_mod_manager\main_window_cards.py"
MAIN = r"d:\dev\L4DBoss\l4d2_mod_manager\main_window.py"
COMP = r"d:\dev\L4DBoss\l4d2_mod_manager\components.py"


def defined_names(path):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    funcs = set()
    attrs_assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            funcs.add(node.name)
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "self":
                    attrs_assigned.add(t.attr)
    return funcs, attrs_assigned, src


# MainWindow 挂接的方法名（main_window.py 末尾 MainWindow.X = ...）
main_src = open(MAIN, encoding="utf-8").read()
main_aliases = set()
for node in ast.walk(ast.parse(main_src)):
    if isinstance(node, ast.Assign):
        t = node.targets[0]
        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == "MainWindow":
            main_aliases.add(t.attr)
# MainWindow.__init__ 里 self.X = ... 的属性
main_funcs, main_attrs, _ = defined_names(MAIN)

# 模块级函数（会成为 MainWindow 方法）也加入
cards_funcs, cards_attrs, _ = defined_names(CARDS)
comp_funcs, comp_attrs, _ = defined_names(COMP)

# 所有"MainWindow 可能拥有"的名字
owned = main_aliases | main_funcs | main_attrs | cards_funcs | cards_attrs

# Qt 基类常见方法（粗略白名单，避免误报）
QT_METHODS = {
    "setObjectName", "show", "exec_", "exec", "accept", "reject", "close",
    "setModal", "resize", "setMinimumSize", "setWindowFlags", "setAttribute",
    "connect", "emit", "blockSignals", "setLayout", "layout", "addWidget",
    "findChild", "setToolTip", "setVisible", "setEnabled", "setText",
    "setChecked", "setCheckState", "checkState", "text", "setData", "data",
    "clear", "addTopLevelItem", "topLevelItem", "topLevelItemCount",
    "childCount", "child", "setFlags", "setExpanded", "itemChanged",
    "setPlaceholderText", "returnPressed", "clicked", "setStyleSheet",
    "move", "setFixedWidth", "setGeometry", "raise_", "activateWindow",
    "setFocus", "clearFocus", "update", "repaint", "setCursor", "unsetCursor",
    "grab", "lower", "showMaximized", "showNormal", "isVisible", "deleteLater",
    "setAcceptDrops", "setContextMenuPolicy", "customContextMenuRequested",
    "setWindowTitle", "setMinimumWidth", "setMaximumWidth", "setWordWrap",
    "setAlignment", "addItem", "itemData", "currentText", "currentIndex",
    "setCurrentText", "setCurrentIndex", "addItems", "clearItems",
    "setColumnCount", "setRowCount", "setItem", "item", "setSpan",
    "horizontalHeader", "verticalHeader", "setSectionResizeMode",
    "setSelectionBehavior", "setSelectionMode", "setEditTriggers",
    "setShowGrid", "resizeColumnsToContents", "resizeRowsToContents",
    "setHorizontalHeaderLabels", "setVerticalHeaderLabels", "selectRow",
    "selectedItems", "setSortingEnabled", "sortByColumn", "setColumnHidden",
    "setRowHidden", "setCellWidget", "cellWidget", "setItemDelegate",
    "viewport", "setViewport", "scrollTo", "scrollToItem", "currentItem",
    "setCurrentItem", "selectedIndexes", "setModel", "model", "setView",
    "setDragEnabled", "setAcceptDrops", "setDropIndicatorShown",
    "setDragDropMode", "setDefaultDropAction", "setAlternatingRowColors",
    "setUniformRowHeights", "setHeaderHidden", "setRootIsDecorated",
    "setItemsExpandable", "setExpandsOnDoubleClick", "setAnimated",
    "setAutoExpandDelay", "setIndentation", "setColumnWidth",
    "setHeaderLabels", "invisibleRootItem", "takeTopLevelItem",
    "insertTopLevelItem", "indexOfTopLevelItem", "topLevelItemCount",
    "collapseItem", "expandItem", "setItemWidget", "itemWidget",
    "openPersistentEditor", "closePersistentEditor", "isPersistentEditorOpen",
    "setEditTriggers", "setSelectionModel", "selectionModel",
    "setHorizontalScrollMode", "setVerticalScrollMode",
    "setAutoScroll", "setTabKeyNavigation", "setCornerButtonEnabled",
    "setAllColumnsShowFocus", "setWordWrap", "setAlternatingRowColors",
    "setRootIsDecorated", "setUniformRowHeights", "setItemsExpandable",
    "setExpandsOnDoubleClick", "setAnimated", "setAutoExpandDelay",
    "setIndentation", "setColumnWidth", "setHeaderLabels",
    "setSortingEnabled", "sortByColumn", "setColumnHidden", "setRowHidden",
}


def check_self_calls(path, func_names):
    src = open(path, encoding="utf-8").read()
    tree = ast.parse(src)
    problems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
            name = node.attr
            # 只检查在函数体里、且不是 Qt 基类方法、且不在 owned 集合里的
            # 粗略：跳过明确 Qt 方法
            if name in QT_METHODS:
                continue
            if name in owned:
                continue
            # 可能属于 self.mod / self.card 等成员对象，无法精确判断，记录
            problems.append(name)
    return sorted(set(problems))


print("=== main_window_cards.py self.* 可能未定义 ===")
for p in check_self_calls(CARDS, cards_funcs):
    print("  ", p)
print("=== components.py self.* 可能未定义 (非Qt基类) ===")
for p in check_self_calls(COMP, comp_funcs):
    print("  ", p)
print("=== owned 集合大小:", len(owned))
