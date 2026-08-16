"""依赖关系工具：解析 Mod 之间的依赖、被依赖与缺失状态。

Steam 创意工坊并没有为 L4D2 提供结构化的"依赖"元数据：
'GetPublishedFileDetails' 只返回 'requiredtags'（创作者上传时选择的
“必需标签”，L4D2 里几乎不用于标注 Mod 依赖），公开 API 也不含
"required items" 之类的字段。依赖信息只能来自创作者在简介里给出的链接
或玩家自己的记录，因此本模块负责：

1. 在本地 Mod 之间解析依赖闭包（支持多级与环状依赖、区分未安装项）；
2. 从 Steam 简介文本中提取创意工坊链接，供“从简介识别依赖”使用。
"""

from __future__ import annotations

import re

from .models import Mod

# 简介中常见的创意工坊引用形式：
#   https://steamcommunity.com/sharedfiles/filedetails/?id=123456789
#   steam://url/CommunityFilePage/123456789
#   （有时带额外参数：?searchtext=...&id=...）
#   id=123456789
_WORKSHOP_PAGE_RE = re.compile(r"sharedfiles/filedetails/[^\"'\s<>]*?id=(\d{5,})", re.IGNORECASE)
_COMMUNITY_FILE_RE = re.compile(r"CommunityFilePage/(\d{5,})", re.IGNORECASE)
_BARE_ID_RE = re.compile(r"(?<![\w])id=(\d{5,})", re.IGNORECASE)


def extract_workshop_ids(text: str | None) -> list[str]:
    """Collect Workshop item ids referenced in a description text (deduplicated)."""
    if not text:
        return []
    ids: list[str] = []
    ids.extend(_WORKSHOP_PAGE_RE.findall(text))
    ids.extend(_COMMUNITY_FILE_RE.findall(text))
    ids.extend(_BARE_ID_RE.findall(text))
    return list(dict.fromkeys(ids))


def resolve_dependencies(mods: dict[str, Mod], mod_id: str) -> list[str]:
    """Return every Mod id the given one depends on, transitively (BFS order).

    The result excludes ``mod_id`` itself and tolerates dependency cycles.
    Dependencies pointing at Mods that are not installed are still included.
    """
    current = mods.get(mod_id)
    if current is None:
        return []
    result: list[str] = []
    seen: set[str] = set()
    queue = [dep for dep in current.dependencies if dep and dep != mod_id]
    while queue:
        dep_id = queue.pop(0)
        if dep_id in seen:
            continue
        seen.add(dep_id)
        result.append(dep_id)
        dep_mod = mods.get(dep_id)
        if dep_mod is not None:
            for nested in dep_mod.dependencies:
                if nested and nested != mod_id and nested not in seen:
                    queue.append(nested)
    return result


def dependency_status(mods: dict[str, Mod], mod_id: str) -> tuple[list[str], list[str]]:
    """Split the transitive dependency set into (inactive_ids, missing_ids).

    - inactive_ids: installed locally but currently disabled;
    - missing_ids: not installed (Workshop ids that may appear after download).
    """
    inactive: list[str] = []
    missing: list[str] = []
    for dep_id in resolve_dependencies(mods, mod_id):
        target = mods.get(dep_id)
        if target is None:
            missing.append(dep_id)
        elif not target.active:
            inactive.append(dep_id)
    return inactive, missing


def resolve_dependents(mods: dict[str, Mod], mod_id: str) -> list[str]:
    """Ids of Mods whose transitive dependency set includes ``mod_id``.

    Excludes ``mod_id`` itself; returned in stable mods-iteration order.
    """
    dependents: list[str] = []
    for mod in mods.values():
        if mod.id != mod_id and mod_id in resolve_dependencies(mods, mod.id):
            dependents.append(mod.id)
    return dependents


def dependency_label(mods: dict[str, Mod], dep_id: str) -> str:
    """Human-readable name for a dependency id (handles missing Mods)."""
    target = mods.get(dep_id)
    if target is not None:
        return target.title or target.file_name
    return f"Workshop {dep_id}（未安装）"
