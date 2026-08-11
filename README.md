# L4D2 Boss

求生之路2（Left 4 Dead 2）Mod 管理工具，使用 Python + PyQt5 开发。

## 功能

- 选择并记住 Mod 存放目录。
- 扫描目录下的 `.vpk` 文件，并读取 VPK 内部文件列表。
- 默认使用 VPK 文件名和同名图片（`.jpg`、`.jpeg`、`.png`、`.webp`）展示 Mod。
- 根据文件名和 VPK 内部路径自动归类到创意工坊风格分类树。
- 武器分类已细化到具体枪械，例如 AK-47、M16、SCAR、SG552、Chrome Shotgun、SPAS-12、Magnum、MP5、AWP 等。
- 点击卡片切换激活状态，并显示“激活”标签。
- 对已激活 Mod 做冲突检测：当多个 VPK 包含相同的关键游戏资源路径时显示“mod冲突”。
- 从 Steam Workshop 拉取名称、作者、订阅量、评分等信息。
- 保存当前激活 Mod 为组合，并写入 `addonlist.txt`。

## 运行

```powershell
python -m pip install -r requirements.txt
python run.py
```

在当前 Codex 环境中也可以直接使用 bundled Python：

```powershell
C:\Users\ADMIN\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe run.py
```

## 数据位置

应用会在项目目录下创建 `data/`：

- `data/settings.json`：保存用户选择的 Mod 目录。
- `data/mods.json`：保存扫描到的 Mod 元数据和激活状态。
- `data/collections.json`：保存用户收藏的 Mod 组合。

保存组合时，会在用户选择的 Mod 目录下写入 `addonlist.txt`。

## Steam 信息

Steam 拉取依赖 VPK 文件名中的创意工坊 ID。例如：

```text
1234567890.vpk
workshop_1234567890.vpk
```

如果文件名中没有数字 ID，应用仍会本地管理该 Mod，但无法自动匹配 Steam Workshop 信息。

## 冲突检测说明

当前版本参考常见 VPK 管理工具思路，以“激活 Mod 内部资源路径重复”为冲突依据。检测范围包括：

- `materials/`
- `models/`
- `scripts/`
- `sound/`
- `soundscape/`
- `particles/`
- `missions/`
- `maps/`

后续可以继续扩展为更细的规则，例如忽略特定 harmless 文件、显示具体冲突文件列表、或按资源类型给出严重程度。

## 高分屏

应用启动时启用 Qt 高分屏缩放，并将默认窗口、字体、卡片、预览图和工具栏尺寸放大。当前 UI 基准缩放在 `l4d2_mod_manager/app.py` 的 `UI_SCALE` 中调整。
