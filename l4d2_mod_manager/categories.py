from __future__ import annotations

import re

CATEGORIES = [
    {"id": "all", "label": "所有 (All)", "children": []},
    {
        "id": "survivors",
        "label": "幸存者 (Survivors)",
        "children": [
            ("bill", "比尔 Bill"),
            ("francis", "弗朗西斯 Francis"),
            ("louis", "路易斯 Louis"),
            ("zoey", "佐伊 Zoey"),
            ("coach", "教练 Coach"),
            ("ellis", "艾利斯 Ellis"),
            ("nick", "尼克 Nick"),
            ("rochelle", "罗谢尔 Rochelle"),
        ],
    },
    {
        "id": "infected",
        "label": "感染者 (Infected)",
        "children": [
            ("common_infected", "普通感染者 Common Infected"),
            {
                "id": "special_infected",
                "label": "特殊感染者 Special Infected",
                "children": [
                    ("boomer", "胖子 Boomer"),
                    ("charger", "冲撞者 Charger"),
                    ("hunter", "猎人 Hunter"),
                    ("jockey", "骑师 Jockey"),
                    ("smoker", "舌头 Smoker"),
                    ("spitter", "喷吐者 Spitter"),
                    ("tank", "坦克 Tank"),
                    ("witch", "女巫 Witch"),
                ],
            },
        ],
    },
    {
        "id": "game_content",
        "label": "游戏内容 (Game Content)",
        "children": [
            ("campaigns", "战役 Campaigns"),
            ("weapons", "武器 Weapons"),
            ("items", "物品 Items"),
            ("sounds", "音效 Sounds"),
            ("scripts", "脚本 Scripts"),
            ("ui", "界面 UI"),
            ("miscellaneous", "杂项 Miscellaneous"),
            ("models", "模型 Models"),
            ("textures", "贴图 Textures"),
        ],
    },
    {
        "id": "game_modes",
        "label": "游戏模式 (Game Modes)",
        "children": [
            ("single_player", "单人 Single Player"),
            ("coop", "合作 Co-op"),
            ("versus", "对抗 Versus"),
            ("scavenge", "清道夫 Scavenge"),
            ("survival", "生存 Survival"),
            ("realism", "写实 Realism"),
            ("realism_versus", "写实对抗 Realism Versus"),
            ("mutations", "突变 Mutations"),
        ],
    },
    {
        "id": "weapon_types",
        "label": "武器 (Weapons)",
        "children": [
            ("grenade_launcher", "榴弹发射器 Grenade Launcher"),
            ("m60", "M60"),
            ("melee", "近战 Melee"),
            {
                "id": "pistol",
                "label": "手枪 Pistol",
                "children": [
                    ("pistol_p220", "P220 手枪 P220 Pistol"),
                    ("pistol_dual", "双持手枪 Dual Pistols"),
                    ("pistol_magnum", "马格南手枪 Magnum Pistol"),
                ],
            },
            {
                "id": "rifle",
                "label": "步枪 Rifle",
                "children": [
                    ("rifle_m16", "M16 突击步枪 M16 Assault Rifle"),
                    ("rifle_ak47", "AK-47"),
                    ("rifle_desert", "战斗步枪 / SCAR Combat Rifle"),
                    ("rifle_sg552", "SG552"),
                ],
            },
            {
                "id": "shotgun",
                "label": "霰弹枪 Shotgun",
                "children": [
                    ("shotgun_pump", "泵动霰弹枪 Pump Shotgun"),
                    ("shotgun_chrome", "铬合金霰弹枪 Chrome Shotgun"),
                    ("shotgun_auto", "战术霰弹枪 Tactical / Auto Shotgun"),
                    ("shotgun_spas", "SPAS-12 Combat Shotgun"),
                ],
            },
            {
                "id": "smg",
                "label": "冲锋枪 SMG",
                "children": [
                    ("smg_uzi", "Uzi 冲锋枪 Submachine Gun"),
                    ("smg_silenced", "消音冲锋枪 Silenced SMG"),
                    ("smg_mp5", "MP5"),
                ],
            },
            {
                "id": "sniper",
                "label": "狙击枪 Sniper",
                "children": [
                    ("sniper_hunting", "猎枪 Hunting Rifle"),
                    ("sniper_military", "军用狙击枪 Military Sniper"),
                    ("sniper_awp", "AWP"),
                    ("sniper_scout", "Scout"),
                ],
            },
            {
                "id": "throwable",
                "label": "投掷物 Throwable",
                "children": [
                    ("throwable_molotov", "燃烧瓶 Molotov"),
                    ("throwable_pipe_bomb", "土制炸弹 Pipe Bomb"),
                    ("throwable_vomitjar", "胆汁瓶 Boomer Bile"),
                ],
            },
        ],
    },
    {
        "id": "item_types",
        "label": "物品 (Items)",
        "children": [
            ("adrenaline", "肾上腺素 Adrenaline"),
            ("defibrillator", "除颤器 Defibrillator"),
            ("medkit", "医疗包 Medkit"),
            ("pills", "止痛药 Pills"),
            ("other", "其他 Other"),
        ],
    },
]

KEYWORD_CATEGORY_RULES: list[tuple[str, str]] = [
    ("bill", "bill"), ("francis", "francis"), ("louis", "louis"), ("zoey", "zoey"),
    ("coach", "coach"), ("ellis", "ellis"), ("nick", "nick"), ("rochelle", "rochelle"),
    ("boomer", "boomer"), ("charger", "charger"), ("hunter", "hunter"), ("jockey", "jockey"),
    ("smoker", "smoker"), ("spitter", "spitter"), ("tank", "tank"), ("witch", "witch"),
    ("common infected", "common_infected"), ("common_infected", "common_infected"),
    ("campaign", "campaigns"), ("map", "campaigns"), ("mission", "campaigns"),
    ("sound", "sounds"), ("music", "sounds"), ("script", "scripts"), ("hud", "ui"),
    ("ui", "ui"), ("model", "models"), ("materials", "textures"), ("texture", "textures"),
    ("single player", "single_player"), ("coop", "coop"), ("co-op", "coop"), ("versus", "versus"),
    ("scavenge", "scavenge"), ("survival", "survival"), ("realism", "realism"), ("mutation", "mutations"),
]

# Steam tags are the primary classification signal.  Only official Workshop
# sections are mapped; a tag must never be guessed from the description.
STEAM_TAG_CATEGORY_RULES = {
    "survivors": "survivors", "survivor": "survivors",
    "infected": "infected", "common infected": "common_infected",
    "special infected": "special_infected", "campaign": "campaigns",
    "campaigns": "campaigns", "map": "campaigns", "maps": "campaigns",
    "weapons": "weapons", "weapon": "weapons", "melee": "melee",
    "pistol": "pistol", "pistols": "pistol", "rifle": "rifle",
    "rifles": "rifle", "shotgun": "shotgun", "shotguns": "shotgun",
    "smg": "smg", "sniper rifle": "sniper", "sniper": "sniper",
    "grenade launcher": "grenade_launcher", "m60": "m60",
    "throwables": "throwable", "throwable": "throwable",
    "items": "items", "item": "items", "sounds": "sounds", "sound": "sounds",
    "music": "sounds", "scripts": "scripts", "script": "scripts",
    "user interface": "ui", "ui": "ui", "models": "models",
    "model": "models", "textures": "textures", "texture": "textures",
    "single player": "single_player", "coop": "coop", "co op": "coop",
    "versus": "versus", "scavenge": "scavenge", "survival": "survival",
    "realism": "realism", "mutation": "mutations", "mutations": "mutations",
}

PARENT_CATEGORIES = {
    "bill": ["survivors"], "francis": ["survivors"], "louis": ["survivors"], "zoey": ["survivors"],
    "coach": ["survivors"], "ellis": ["survivors"], "nick": ["survivors"], "rochelle": ["survivors"],
    "common_infected": ["infected"], "boomer": ["infected", "special_infected"],
    "charger": ["infected", "special_infected"], "hunter": ["infected", "special_infected"],
    "jockey": ["infected", "special_infected"], "smoker": ["infected", "special_infected"],
    "spitter": ["infected", "special_infected"], "tank": ["infected", "special_infected"],
    "witch": ["infected", "special_infected"],
    "pistol_p220": ["pistol"], "pistol_dual": ["pistol"], "pistol_magnum": ["pistol"],
    "rifle_m16": ["rifle"], "rifle_ak47": ["rifle"], "rifle_desert": ["rifle"], "rifle_sg552": ["rifle"],
    "shotgun_pump": ["shotgun"], "shotgun_chrome": ["shotgun"], "shotgun_auto": ["shotgun"], "shotgun_spas": ["shotgun"],
    "smg_uzi": ["smg"], "smg_silenced": ["smg"], "smg_mp5": ["smg"],
    "sniper_hunting": ["sniper"], "sniper_military": ["sniper"], "sniper_awp": ["sniper"], "sniper_scout": ["sniper"],
    "throwable_molotov": ["throwable"], "throwable_pipe_bomb": ["throwable"], "throwable_vomitjar": ["throwable"],
}

WEAPON_CATEGORIES = {
    "grenade_launcher", "m60", "melee", "pistol", "pistol_p220", "pistol_dual", "pistol_magnum",
    "rifle", "rifle_m16", "rifle_ak47", "rifle_desert", "rifle_sg552",
    "shotgun", "shotgun_pump", "shotgun_chrome", "shotgun_auto", "shotgun_spas",
    "smg", "smg_uzi", "smg_silenced", "smg_mp5",
    "sniper", "sniper_hunting", "sniper_military", "sniper_awp", "sniper_scout",
    "throwable", "throwable_molotov", "throwable_pipe_bomb", "throwable_vomitjar",
}

ITEM_CATEGORIES = {"adrenaline", "defibrillator", "medkit", "pills", "other"}


def infer_categories(
    title: str,
    paths: list[str],
    steam_tags: list[str] | None = None,
    description: str = "",
    file_name: str = "",
) -> list[str]:
    """Classify a Workshop addon with ranked, source-aware evidence.

    Precedence: Workshop tags > VPK target assets > explicit replacement text >
    title/file-name fallback.  A word occurring only in the description is not
    a category signal unless it is part of an explicit replacement declaration.
    """
    title_haystack = normalize_search_text(" ".join([title, file_name]))
    replacement_haystack = normalize_search_text(" ".join([title, file_name, description]))
    paths_haystack = normalize_search_text(" ".join(paths))
    found = categories_from_steam_tags(steam_tags or [])
    found.update(category for keyword, category in KEYWORD_CATEGORY_RULES if normalize_search_text(keyword) in title_haystack)
    found.update(categories_from_content_paths(paths_haystack))
    found.update(categories_from_weapon_assets(paths_haystack))
    found.update(categories_from_explicit_replacements(replacement_haystack))

    for category in list(found):
        found.update(PARENT_CATEGORIES.get(category, []))
    if found & WEAPON_CATEGORIES:
        found.add("weapons")
    if found & ITEM_CATEGORIES:
        found.add("items")
    if not found:
        found.add("miscellaneous")
    return sorted(found)


def normalize_search_text(value: str) -> str:
    value = value.lower().replace("\\", "/")
    return re.sub(r"[\-_]+", " ", value)


def categories_from_steam_tags(tags: list[str]) -> set[str]:
    return {
        category for tag in tags
        if (category := STEAM_TAG_CATEGORY_RULES.get(normalize_search_text(tag).strip()))
    }


def categories_from_content_paths(paths_haystack: str) -> set[str]:
    """Use file roots only for general content type, never for weapon guesses."""
    rules = {
        "maps/": "campaigns", "missions/": "campaigns", "sound/": "sounds",
        "soundscape/": "sounds", "scripts/": "scripts", "particles/": "scripts",
        "materials/vgui/": "ui", "resource/": "ui", "models/": "models",
        "materials/": "textures",
    }
    return {category for marker, category in rules.items() if marker in paths_haystack}


def categories_from_weapon_assets(paths_haystack: str) -> set[str]:
    """Identify a replacement slot from actual VPK asset paths only."""
    found: set[str] = set()
    asset_patterns = {
        r"(?:^|/)(?:v|w) rifle(?:[./]|$)|/weapons/rifle/(?!ak47/|desert/|sg552/)|icon rifle(?:[./]|$)": "rifle_m16",
        r"(?:^|/)(?:v|w) rifle ak47(?:[./]|$)|/weapons/rifle/ak47/|icon rifle ak47": "rifle_ak47",
        r"(?:^|/)(?:v|w) rifle desert(?:[./]|$)|/weapons/rifle/desert/|icon rifle desert": "rifle_desert",
        r"(?:^|/)(?:v|w) rifle sg552(?:[./]|$)|/weapons/rifle/sg552/|icon rifle sg552": "rifle_sg552",
        r"(?:^|/)(?:v|w) pistol(?:[./]|$)|/weapons/pistol/|icon pistol(?:[./]|$)": "pistol_p220",
        r"(?:^|/)(?:v|w) pistol magnum(?:[./]|$)|/weapons/pistol/magnum/|icon pistol magnum": "pistol_magnum",
        r"(?:^|/)(?:v|w) smg(?:[./]|$)|/weapons/smg/(?!silenced/|mp5/)|icon smg(?:[./]|$)": "smg_uzi",
        r"(?:^|/)(?:v|w) smg silenced(?:[./]|$)|/weapons/smg/silenced/|icon smg silenced": "smg_silenced",
        r"(?:^|/)(?:v|w) smg mp5(?:[./]|$)|/weapons/smg/mp5/|icon smg mp5": "smg_mp5",
        r"(?:^|/)(?:v|w) pumpshotgun(?:[./]|$)|/weapons/pumpshotgun/|icon pumpshotgun": "shotgun_pump",
        r"(?:^|/)(?:v|w) shotgun chrome(?:[./]|$)|/weapons/shotgun/chrome/|icon shotgun chrome": "shotgun_chrome",
        r"(?:^|/)(?:v|w) autoshotgun(?:[./]|$)|/weapons/autoshotgun/|icon autoshotgun": "shotgun_auto",
        r"(?:^|/)(?:v|w) shotgun spas(?:[./]|$)|/weapons/shotgun/spas/|icon shotgun spas": "shotgun_spas",
        r"(?:^|/)(?:v|w) hunting rifle(?:[./]|$)|/weapons/hunting rifle/|icon hunting rifle": "sniper_hunting",
        r"(?:^|/)(?:v|w) sniper military(?:[./]|$)|/weapons/sniper/military/|icon sniper military": "sniper_military",
        r"(?:^|/)(?:v|w) sniper awp(?:[./]|$)|/weapons/sniper/awp/|icon sniper awp": "sniper_awp",
        r"(?:^|/)(?:v|w) sniper scout(?:[./]|$)|/weapons/sniper/scout/|icon sniper scout": "sniper_scout",
    }
    for pattern, category in asset_patterns.items():
        if re.search(pattern, paths_haystack):
            found.add(category)
    return found


def categories_from_explicit_replacements(text_haystack: str) -> set[str]:
    """Use author text only when it explicitly states the replacement target."""
    found: set[str] = set()
    targets = {
        "rifle_m16": ("m16", "assault rifle"), "rifle_ak47": ("ak47", "ak 47"),
        "rifle_desert": ("scar", "combat rifle", "desert rifle"), "rifle_sg552": ("sg552",),
        "pistol_p220": ("pistol", "p220"), "pistol_magnum": ("magnum", "desert eagle"),
        "smg_uzi": ("smg", "uzi", "submachine gun"), "smg_silenced": ("silenced smg",),
        "smg_mp5": ("mp5",), "shotgun_pump": ("pump shotgun",),
        "shotgun_chrome": ("chrome shotgun",), "shotgun_auto": ("autoshotgun", "tactical shotgun"),
        "shotgun_spas": ("spas", "spas 12", "combat shotgun"),
        "sniper_hunting": ("hunting rifle",), "sniper_military": ("military sniper",),
        "sniper_awp": ("awp",), "sniper_scout": ("scout",),
        "bill": ("bill",), "francis": ("francis",), "louis": ("louis",), "zoey": ("zoey",),
        "coach": ("coach",), "ellis": ("ellis",), "nick": ("nick",), "rochelle": ("rochelle",),
        "boomer": ("boomer",), "charger": ("charger",), "hunter": ("hunter",), "jockey": ("jockey",),
        "smoker": ("smoker",), "spitter": ("spitter",), "tank": ("tank",), "witch": ("witch",),
    }
    for category, aliases in targets.items():
        for alias in aliases:
            target = r"\\s+".join(re.escape(part) for part in alias.split())
            if re.search(
                rf"(?:\b(?:replace|replaces|replaced|replacement(?:\s+for)?)\b.{{0,48}}\b{target}\b|"
                rf"\b{target}\b.{{0,24}}\b(?:replacement|replace)\b|替换.{{0,20}}{target}|{target}.{{0,12}}替换)",
                text_haystack,
            ):
                found.add(category)
                break
    return found
