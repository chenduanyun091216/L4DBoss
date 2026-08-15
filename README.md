# L4D2 Boss

一个面向《求生之路 2》（Left 4 Dead 2）的 VPK Mod 管理工具。项目使用 Python 和 PyQt5 开发，提供图形化的 Mod 扫描、分类、启用/禁用、Steam 创意工坊信息同步、冲突检测、Mod 组合管理以及游戏启动功能。

## 📥 立即下载

[![Download L4DBoss.exe](https://img.shields.io/badge/Download-L4DBoss.exe-blue?style=for-the-badge&logo=github)](https://github.com/chenduanyun091216/L4DBoss/releases/download/L4DModManager/L4DBoss.exe)

> 💡 **使用提示**：点击上方按钮即可下载最新版 `L4DBoss.exe`。若浏览器拦截，请选择“保留文件”。

## 功能概览

- 自动查找或手动选择《求生之路 2》游戏程序。
- 扫描 `left4dead2/addons` 和 `left4dead2/addons/workshop` 下的 `.vpk` 文件。
- 读取 VPK 内部资源路径，不解包、不修改 VPK 内容。
- 使用同名图片作为 Mod 预览图，支持 `.jpg`、`.jpeg`、`.png`、`.webp`。
- 根据 Steam Workshop 标签、Mod 文件名和 VPK 内部资源路径自动分类。
- 支持简单分类和 Steam 风格详细分类两种分类树。
- 武器分类细化到 AK-47、M16、SCAR、SG552、SPAS-12、MP5、AWP 等具体目标。
- 搜索、分页、仅查看已启用 Mod，以及按分类筛选。
- 点击卡片或卡片按钮快速启用/禁用 Mod。
- 点击卡片上的星标收藏 Mod，收藏状态会随卡片高亮保存。
- 调整卡片显示尺寸以适应不同数量的 Mod。
- 对已启用 Mod 检查资源路径重复，并显示冲突组和冲突原因。
- 从 Steam Workshop 获取名称、作者、订阅数、评分、描述和标签。
- 保存、切换和另存为 Mod 组合，组合下拉框支持同时选择多个组合。
- 将组合中的 VPK 和预览图同步到同名组合目录，便于备份和恢复。
- 写入 `addonlist.txt`，并通过“启动游戏”按钮启动 Steam 或游戏程序。
- 内置多种界面主题，可点击主题按钮随时切换。
- 无边框窗口设计，支持最小化、最大化/还原和关闭，以及高分屏显示。
- 支持后台扫描/同步任务，操作过程中界面仍可正常浏览。

## 界面说明

![img_1.png](files/img_1.png)

![img_2.png](files/img_2.png)

### 顶部工具栏

- **选择游戏**：选择 `left4dead2.exe`。程序会据此定位游戏的 `addons` 目录，并记住选择结果。
- **重新扫描**：增量扫描当前游戏目录中的 Mod。文件没有变化时会复用已有缓存。
- **重新扫描全部**：清除当前 Mod 元数据后重新读取所有 VPK。适合文件被大量替换、分类缓存异常或需要重新建立索引时使用。
- **同步 Steam**：批量获取可识别的 Workshop Mod 信息。已经同步并缓存过的 Mod 默认不会重复请求。
- **分类模式开关**：在简单分类和 Steam 风格详细分类之间切换。
- **主题按钮**：在暗色、亮色等多种界面主题之间切换，选择结果会被记住。
- **最小化 / 最大化 / 关闭**：窗口控制按钮（程序使用无边框窗口，由这几个按钮控制窗口状态）。
- **关于**：打开关于对话框，查看版本与项目信息。

### 左侧分类栏

简单分类更适合日常浏览，主要包含：

- 枪械
- 近战武器
- 物品/工具
- 生还者
- 感染者
- 地图
- 其他

Steam 风格分类会进一步展示武器、角色、感染者、地图、脚本、声音、模型、材质等具体分类。分类结果来自多种证据，优先使用 Workshop 标签，其次参考 VPK 内部路径和文件名。

### Mod 卡片

卡片通常包含预览图、名称、Workshop ID、订阅数、评分、分类标签和来源标签。

- 点击卡片：切换启用状态。
- **启用 Mod/禁用 Mod**：只改变当前管理器中的启用状态，并立即保存。
- **★ 收藏**：点击卡片上的星标收藏/取消收藏 Mod，收藏的卡片会有高亮边框，状态会保存。
- **STEAM** 标签：打开对应的 Steam Workshop 页面。
- **本地** 标签：打开 Mod 所在文件夹。
- 鼠标悬停预览图：查看放大的预览图。
- 右键卡片：查看源文件、详细信息、同步当前 Mod 的 Steam 信息、删除 Mod，或将 Mod 加入已保存组合。
- 卡片尺寸：可通过界面控件调整卡片大小，以适配大批量 Mod 的浏览。

卡片边框颜色含义如下：

- 绿色：Mod 已启用。
- 红色：Mod 已启用且存在资源冲突。
- 黄色星标高亮：Mod 已被收藏。
- 普通深色：Mod 未启用。

## 安装与运行

### 运行环境

- Windows 10/11 建议环境。
- Python 3.10 或更高版本。
- 可访问 Steam Workshop 页面或 Steam Web API（只有同步 Steam 信息时需要网络）。

### 从源码运行

在项目根目录打开 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

如果 PowerShell 禁止执行虚拟环境脚本，也可以直接使用当前 Python：

```powershell
python -m pip install -r requirements.txt
python run.py
```

依赖包括：

| 依赖 | 用途 |
| --- | --- |
| `PyQt5` | 图形界面 |
| `requests` | 请求 Steam Workshop API 和页面 |
| `vpk` | 读取 VPK 包及内部文件路径 |

### 首次启动

1. 启动程序。
2. 点击 **选择游戏**。
3. 选择游戏目录中的 `left4dead2.exe`。
4. 程序检查对应的 `addons` 目录并自动开始扫描。
5. 扫描完成后即可筛选、启用和管理 Mod。

常见 Steam 安装路径示例：

```text
C:\Program Files (x86)\Steam\steamapps\common\Left 4 Dead 2\left4dead2\left4dead2.exe
```

程序会尝试读取 Steam 注册表和 `libraryfolders.vdf`，自动查找多个 Steam 库中的游戏；自动查找失败时，仍可通过文件选择框手动定位。

## 推荐使用流程

### 1. 扫描 Mod

将 VPK 放入游戏的以下任意目录：

```text
<游戏目录>\left4dead2\addons\
<游戏目录>\left4dead2\addons\workshop\
```

然后点击 **重新扫描**。程序只扫描这两个目录的第一层 `.vpk` 文件，不会递归扫描更深层的自定义目录。

如果同一个 Mod 同时出现在 `addons` 和 `addons\workshop`，程序优先使用 `workshop` 目录中的文件。

### 2. 筛选和查找

可以使用以下方式定位 Mod：

- 在搜索框输入名称、文件名或 Workshop ID。
- 点击左侧分类树。
- 点击底部的 Mod 总数查看全部 Mod。
- 点击底部的已启用数量只查看当前启用的 Mod。
- 使用分页按钮浏览大量 Mod。

### 3. 启用和禁用

点击卡片即可切换状态。启用状态会保存在本地，程序关闭后不会丢失。

底部的 **全部启动/全部禁用** 可以批量切换所有已扫描 Mod。批量操作后建议查看冲突数量。

需要注意：启用状态首先保存在管理器数据中；只有点击 **启动游戏** 时，程序才会把当前状态写入游戏目录旁的 `addonlist.txt`。

### 4. 检查冲突

底部的 **冲突** 按钮会显示已启用 Mod 之间的资源冲突。当前判断逻辑是：两个或多个已启用 VPK 包含相同的关键嵌套资源路径时，认为存在冲突。

重点检查的资源类型包括：

```text
materials/
models/
scripts/
sound/
soundscape/
particles/
missions/
maps/
```

根目录元数据文件不会作为冲突依据。部分可并行的 VScript 入口文件也会被排除，以减少误报。

冲突报告会尽量显示共同替换的目标，例如某个具体枪械；如果无法识别目标，则显示共享资源文件数量。双击冲突卡片可以直接禁用对应 Mod。

冲突只是资源覆盖关系提示，不代表程序一定会崩溃。某些 Mod 本来就需要覆盖其他 Mod，最终启用顺序和实际游戏表现仍需要根据需求判断。

### 5. 同步 Steam 信息

文件名中包含数字时，程序会将其中的数字作为 Workshop ID。例如：

```text
1234567890.vpk
workshop_1234567890.vpk
my_mod_1234567890.vpk
```

点击 **同步 Steam** 后，程序会在后台请求可识别 Mod 的 Workshop 信息。同步过程中可以继续浏览界面，也可以点击同步按钮取消后续任务。

同步失败时，Mod 仍可继续作为本地 Mod 管理。没有 Workshop ID 的文件无法自动匹配 Steam 信息，但不影响扫描、分类、启用、冲突检测和组合管理。

### 6. 保存 Mod 组合

1. 启用需要保存的一组 Mod。
2. 点击底部 **保存**。
3. 输入组合名称并确认。
4. 程序保存组合中的 Mod ID，并复制相关 VPK 和预览图片到 `addons` 下的同名文件夹。

如果当前已经选中一个组合，点击 **保存** 会更新该组合；点击 **另存为** 可以创建一个新的组合。

组合下拉框支持同时选中多个组合。切换组合时，程序会尝试从组合文件夹恢复缺失的 VPK 和预览图，然后重新扫描并启用所选组合中记录的 Mod；多个组合叠加时，取并集。

组合名称中的 Windows 非法文件名字符会被替换为下划线；名称不能是 `workshop`、`.` 或 `..`。

### 7. 启动游戏

点击 **启动游戏** 时，程序会：

1. 保存当前 Mod 状态。
2. 根据当前启用状态写入 `addonlist.txt`。
3. 如果检测到 Steam，则通过 `steam://rungameid/550` 启动游戏；否则直接启动已选择的 `left4dead2.exe`。

`addonlist.txt` 位于所选 `addons` 目录的上一级目录，内容使用相对路径记录每个 Mod 的启用值：`1` 表示启用，`0` 表示禁用。

## 数据和文件位置

### 游戏目录中的文件

```text
left4dead2\addons\                 普通 Mod
left4dead2\addons\workshop\        Steam Workshop Mod
left4dead2\addons\.mods\<组合名>\   保存组合时复制的 VPK 和预览图（`.mods` 默认隐藏）
left4dead2\addonlist.txt            启动游戏前生成的启用列表
```

### 程序数据

默认保存在：

```text
%LOCALAPPDATA%\L4DBoss\data\
```

主要文件如下：

| 文件 | 内容 |
| --- | --- |
| `settings.json` | 游戏路径、addons 路径和已选组合等设置 |
| `mods.json` | 扫描到的 Mod 元数据、分类、文件列表和启用状态 |
| `steam_cache.json` | Steam Workshop 信息缓存 |
| `collections.json` | 已保存的 Mod 组合及其 Mod ID |

如果 `%LOCALAPPDATA%\L4DBoss` 无法创建或没有写入权限，程序会回退到项目目录下的 `.l4d2_user_data\data\`。

删除或移动这些 JSON 文件会清除对应的本地索引/缓存，但不会自动删除游戏 `addons` 目录中的 VPK 文件。删除 Mod 卡片时，程序会请求确认，并只删除该 Mod 文件及其关联预览图。

## 项目结构

```text
L4DBoss/
├─ l4d2_mod_manager/
│  ├─ app.py               应用入口，仅负责创建 QApplication 与主窗口
│  ├─ main_window.py       MainWindow 基类、状态初始化与各子模块方法挂载
│  ├─ main_window_build.py 界面构建、标题栏、主题菜单、窗口控制、底部状态栏
│  ├─ main_window_cards.py 分类树、卡片渲染、搜索/分页/筛选、冲突索引
│  ├─ main_window_mods.py  选择/查找游戏、扫描、启用禁用、启动游戏
│  ├─ main_window_collections.py  Mod 组合保存/切换/恢复、addonlist 写入
│  ├─ main_window_steam.py Steam 信息同步（批量与单个）及取消
│  ├─ main_window_conflicts.py    冲突报告构建与展示
│  ├─ main_window_details.py      卡片右键菜单、详情、删除、Mod 列表视图
│  ├─ main_window_events.py       后台任务失败、窗口事件与高分屏处理
│  ├─ components.py        基础组件，含 ModCard 卡片与收藏星标
│  ├─ theme.py             主题调色板、样式与图标常量
│  ├─ categories.py        分类树和自动分类规则
│  ├─ collection_sync.py   组合文件复制与恢复
│  ├─ models.py            Mod、ModCollection 数据模型
│  ├─ steam_client.py      Steam Workshop API/页面请求
│  ├─ storage.py           JSON 数据持久化
│  └─ vpk_scanner.py       VPK 扫描、解析和冲突检测
├─ files/                  界面背景、标题图、图标与截图
├─ tests/                  单元测试
├─ docs/                   需求和补充文档
├─ requirements.txt        Python 依赖
├─ run.py                  程序入口
├─ 打包程序.bat            一键打包脚本（自动检查环境并生成 exe）
└─ pack                    Nuitka 打包命令
```

> 说明：原 `app.py` 中的主窗口实现已拆分为 `main_window.py` 与若干 `main_window_*.py` 子模块，便于维护；`app.py` 现仅作为启动入口。

## 开发和测试

运行现有单元测试：

```powershell
python -m unittest discover -s tests -v
```

测试重点覆盖冲突检测规则，包括根目录元数据排除、并行 VScript 排除和嵌套资源重复判断。

## 打包为 Windows 可执行文件

项目提供了 `pack` 文件和 `打包程序.bat`，使用 Nuitka 将程序打包为无控制台窗口的单文件程序。

**一键打包（推荐）**：在项目根目录双击 `打包程序.bat`，脚本会自动检查 Python、安装项目依赖和 Nuitka（如缺失），然后执行打包并显示输出文件位置。

**手动打包**：先安装 Nuitka：

```powershell
python -m pip install nuitka
```

然后在项目根目录执行：

```powershell
./pack
```

打包命令会：

- 启用 PyQt5 插件。
- 关闭控制台窗口。
- 使用 `files/title.ico` 作为程序图标。
- 将 `files` 目录作为资源打包。
- 输出 `build/L4DBoss.exe`。

打包后的程序仍会把可变数据保存到 `%LOCALAPPDATA%\L4DBoss`，不会把用户数据写入 Nuitka 的临时解压目录。

## 常见问题

### 扫描不到 Mod

请确认：

1. 选择的是 `left4dead2.exe`，不是 Steam.exe 或其他程序。
2. VPK 文件位于 `left4dead2\addons` 或 `left4dead2\addons\workshop` 的第一层。
3. 文件扩展名确实是 `.vpk`。
4. 选择游戏后重新点击 **重新扫描全部**。

### Steam 信息没有显示

请确认 VPK 文件名包含 Workshop 数字 ID，并检查网络是否可以访问 Steam。没有数字 ID 的本地 Mod 无法自动匹配 Workshop 页面。也可以右键单个 Mod，选择 **同步当前 Mod Steam 信息** 进行重试。

### 组合切换后没有恢复 Mod

组合恢复只会从 `addons\.mods\<组合名>` 读取 `.vpk`、`.jpg`、`.jpeg`、`.png` 和 `.webp` 文件；`.mods` 会在首次使用时自动创建并在 Windows 中设置为隐藏。如果组合目录不存在或文件被移除，程序无法恢复。恢复文件后重新扫描即可建立索引。

### 启动游戏后 Mod 状态不符合预期

请先回到管理器确认卡片上的启用状态和冲突提示，再点击 **启动游戏**。程序只会在启动游戏前生成 `addonlist.txt`；手动启动游戏不会自动触发这个写入过程。

### Steam 同步失败是否影响 Mod 使用

不会。Steam 信息只是补充元数据，扫描、分类、启用/禁用、冲突检测和组合功能均可离线使用。

## 注意事项

- 本工具管理的是 VPK 文件及其启用状态，不负责下载 Workshop Mod。
- 启用多个会替换同一游戏资源的 Mod 时，通常只有一个 Mod 的资源会生效，请根据冲突报告调整组合。
- 删除 Mod 是对本地文件的实际删除操作，请在确认文件不再需要后执行。
- 修改 `categories.py` 或 `vpk_scanner.py` 中的规则后，建议使用 **重新扫描全部** 验证分类和冲突结果。

## 许可证

当前仓库未声明开源许可证。如需公开发布，请根据实际情况补充 `LICENSE` 文件和版权说明。
