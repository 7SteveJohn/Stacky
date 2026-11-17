# Stacky 灵叠

Windows 10 / 11 桌面视窗矩阵与应用启动台。把常用应用、文件夹和小组件收进桌面上的超椭圆卡片，保持桌面整洁，同时保留真实文件系统的访问方式。

<p>
  <a href="#系统要求"><img alt="Windows 10 / 11 x64" src="https://img.shields.io/badge/Windows-10%20%7C%2011%20x64-blue?style=flat-square"></a>
  <a href="https://www.python.org/downloads/"><img alt="Python 3.10+" src="https://img.shields.io/badge/Python-3.10%2B-blueviolet?style=flat-square"></a>
  <a href="https://doc.qt.io/qtforpython-6/"><img alt="PySide6 / Qt" src="https://img.shields.io/badge/GUI-PySide6%20%2F%20Qt-green?style=flat-square"></a>
  <a href="#配置与数据"><img alt="Local-First" src="https://img.shields.io/badge/Storage-Local--First-orange?style=flat-square"></a>
</p>

## 目录

- [功能](#功能)
- [界面与操作](#界面与操作)
- [快捷键](#快捷键)
- [系统要求](#系统要求)
- [安装](#安装)
- [从源码运行](#从源码运行)
- [项目结构](#项目结构)
- [配置与数据](#配置与数据)
- [构建](#构建)
- [常见问题](#常见问题)
- [设计约束](#设计约束)

## 功能

### 桌面卡片矩阵

- 2×2 / 2×1 两种基础规格，右下角把手可无级缩放（限制在基准尺寸的 0.9～1.25 倍，避免内容排崩）
- 拖动时按 16px 网格吸附，自动与相邻卡片保持间距，落点用轮廓预览；多显示器环境下各屏幕独立边界钳位
- 多张卡片可同时存在，各自绑定不同分类，支持边缘平齐与中心线找正
- 卡片内容超过一页时自动分页，底部显示胶囊分页指示器，鼠标滚轮平滑滑动翻页

### 物理目录托管

- 每个分类直接映射为硬盘上的真实目录，默认根目录为 `%USERPROFILE%\Stacky`
- 从桌面拖入卡片 = 文件真实移动到该目录，桌面立即腾空；文件始终可用资源管理器直接访问
- 外部对目录的增删改通过增量对比同步回卡片（文件监听 + 80ms 防抖），保留网页书签、子分组、自定义图标与手动排序
- 卸载程序不会删除这些文件，它们只是普通文件夹

### 启动台与检索

- 三种打开模式：全双击（默认，防误触）、全单击、混合（外层单击 / 抽屉双击）
- 抽屉内输入即搜：支持中文、全拼、拼音首字母和子串匹配（如 `jsq` → 计算器）
- 支持数学表达式，安全求值后回车复制结果（如 `1024*768`、`(12+8)/4`）
- 支持系统命令与网址直达：输入 `cmd`、`taskmgr`、`calc` 或合法 URL 可直接执行 / 打开
- 可选接入本机文件索引服务，对全盘文件做毫秒级检索

### 桌面小组件

| 小组件 | 说明 |
|---|---|
| 时钟日历 | 时间、日期、星期与农历节气 |
| 系统监控 | CPU、内存、各磁盘分区占用，彩色进度槽实时刷新 |
| 便签待办 | 随手记录、待办清单，本地持久化 |
| 天气 | 当前天气、逐时与多日预报，内置多色阶矢量天气图标，支持区县定位 |
| 媒体播放 | 读取系统媒体会话，显示封面、曲目、歌词与进度，可控制播放 |
| 桌面搜索 | 桌面常驻搜索栏，接入 Everything 做全盘文件即时检索 |
| 文件中转站 | 拖入临时收集，拖出到其他应用；移除记录不删除原文件 |

小组件与卡片共用同一套网格、吸附、多屏钳位、缩放和右键菜单。

### 外观

- 三套主题可视预览切换：深色磨砂、浅色云母、纯黑 OLED；浅色主题完整覆盖设置中心、右键菜单、弹窗与文件中转站
- 设置中心适配 Windows 11 同色沉浸圆角标题栏、分组卡片与胶囊开关控件
- 可调卡片透明度、模糊强度、圆角半径、网格行列数
- 从可执行文件或快捷方式提取 256×256 高清图标，统一为连续曲率超椭圆，支持 120Hz 亚像素平滑悬停缩放
- 卡片外的透明留白区域不拦截鼠标，桌面框选不受影响

### 安装与升级

- 按用户安装，无需管理员权限，不触发 UAC
- 覆盖安装先发送 `--quit` 指令通知旧实例优雅退出，若底层运行时 DLL 仍被外部进程占用则通过 NTFS 原位重命名避让，无需强行关闭其他软件
- 覆盖安装保留 `config.json` 与所有物理分类；配置写入采用原子替换并自动维护 `.bak` 副本，异常断电可自动恢复
- 提供安装版与绿色便携版两种分发形式；便携版内置 `portable.flag`，配置与缓存自包含在解压目录内
- **配置导入 / 导出**：设置 → 备份，可把卡片、布局、主题与组件位置导出为 `config.json` 备份；导入前自动保存当前配置

## 界面与操作

### 桌面卡片

| 操作 | 行为 |
|---|---|
| 单击 / 双击图标 | 打开对应应用（取决于「打开模式」设置） |
| 单击空白处或标题 | 原位展开二级抽屉 |
| 按住并拖动（位移 ≥ 7px） | 移动卡片，显示网格吸附预览 |
| 滚轮在卡片上滚动 | 平滑切换九宫格分页 |
| 右键空白处 | 卡片管理菜单（添加应用 / 网页、展开、锁定、对齐、打开设置等） |
| 拖动右下角把手 | 无级缩放卡片 |
| 双击桌面空白处（含副屏） | 切换禅模式，一键隐藏 / 恢复桌面卡片与小组件 |

### 二级抽屉

| 操作 | 行为 |
|---|---|
| 直接键入 | 唤出搜索横幅并即时过滤 |
| `↑` `↓` `←` `→` / `Tab` | 移动焦点 |
| `Enter` | 打开选中项 / 复制计算结果 |
| `Esc` 或点击外部 | 收起抽屉，回到桌面 |

### 全局热键

默认 `Alt+Space` 呼出 / 收起居中的大视界；若被其他程序占用，自动回退到 `Alt+F`。可在设置中修改。

## 快捷键

| 位置 | 按键 | 作用 |
|---|---|---|
| 全局 | `Alt+Space`（回退 `Alt+F`） | 呼出 / 收起启动台 |
| 抽屉 | 任意字符 | 搜索 |
| 抽屉 | 方向键 / `Tab` | 切换选中项 |
| 抽屉 | `Enter` | 打开或执行 |
| 抽屉 | `Esc` | 关闭 |
| 卡片 | 滚轮 | 翻页 |

## 系统要求

- Windows 10 / 11 x64
- 安装版与便携版：无需 Python
- 源码运行：Python 3.10 及以上

## 安装

从 [Releases](../../releases) 下载：

| 文件 | 说明 |
|---|---|
| `Stacky-1.0.3-Setup-x64.exe` | 安装版，按用户安装，自动创建开始菜单与桌面快捷方式 |
| `Stacky-1.0.3-Portable-x64.zip` | 绿色便携版（内置 `portable.flag`），解压后运行 `BigFolderLauncher.exe`，配置与缓存保存在程序同级目录 |

安装版首次启动会在 `%APPDATA%\Stacky` 生成默认配置，在 `%LOCALAPPDATA%\Stacky` 建立图标缓存；启用物理分类后，会在 `%USERPROFILE%\Stacky` 下按需建立目录。

支持常用命令行参数：`BigFolderLauncher.exe --quit`（优雅退出运行实例）、`--toggle`（切换抽屉）、`--zen`（切换禅模式）。

## 从源码运行

```bash
git clone https://github.com/7SteveJohn/Stacky.git
cd Stacky
pip install -r requirements.txt
python main.py
```

源码模式使用仓库根目录的 `config.json`，不会写入 `%APPDATA%`。

## 项目结构

```
main.py                  入口
stacky/
  __init__.py            把各子目录加入 sys.path，兼容历史扁平导入
  paths.py               项目根 / 打包根目录
  core/                  主窗口、配置、存储、桌面图层、网格、分页
  widgets/               桌面卡片、抽屉、文件中转站与各类小组件
  media/                 系统媒体会话、播放位置控制
  services/              天气、文件索引 IPC
  ui/                    通用控件、图标提取、亚克力、设置与网页对话框
  utils/                 搜索、快捷方式、自启、资产迁移
BigFolderLauncher.spec   PyInstaller 打包配置
installer.iss            Inno Setup 安装脚本
requirements.txt         运行依赖
```

新代码建议使用显式包路径：

```python
from stacky.core import config_manager
from stacky.ui.icon_utils import get_app_icon_pixmap
```

历史代码仍使用 `import config_manager` 这类扁平导入，由 `stacky/__init__.py` 统一挂载路径。

## 配置与数据

| 路径 | 内容 |
|---|---|
| `%APPDATA%\Stacky\config.json`（便携 / 源码模式为程序目录） | 用户配置，写入时自动维护 `config.json.bak` 备份 |
| `%USERPROFILE%\Stacky\` | 物理分类目录，卡片映射的真实文件夹 |
| `%LOCALAPPDATA%\Stacky\.cache_icons\`（便携 / 源码模式为程序目录） | 图标缓存 |
| `%APPDATA%\Stacky\.desktop_memo.txt` / `.desktop_todos.json`（便携 / 源码模式为程序目录） | 便签与待办 |
| `%APPDATA%\Stacky\.weather_*.json`（便携 / 源码模式为程序目录） | 天气城市与预报缓存 |

常用配置项：

| 键 | 说明 |
|---|---|
| `hotkey` | 全局热键，默认 `Alt+Space` |
| `theme` | `dark` / `light` / `oled` |
| `open_app_mode` | `double` / `single` / `hybrid` |
| `grid_rows` / `grid_cols` | 卡片网格行列数 |
| `opacity` / `blur_enabled` | 卡片透明度与模糊 |
| `managed_storage_root` | 物理分类根目录，留空则用默认值 |
| `desktop_pinned_enabled` | 是否停靠到桌面壁纸层 |

## 构建

```bash
pip install -r requirements.txt pyinstaller
pyinstaller BigFolderLauncher.spec
```

产物在 `dist/BigFolderLauncher/`。生成安装包还需要 Inno Setup 6：

```bash
iscc installer.iss
```

安装包默认输出到 `dist/`，脚本使用相对路径，仓库放在任何目录都能构建。

## 常见问题

<details>
<summary><b>按 Win+D 或「显示桌面」，卡片会消失吗？</b></summary>
<p>不会。窗口以桌面壁纸层停靠，并通过 Win32 消息把桌面视图设为所有者；同时在资源管理器重启或前台切换后自动重新贴回桌面层。</p>
</details>

<details>
<summary><b>卸载后，卡片里的文件会丢吗？</b></summary>
<p>不会。卡片映射的是真实文件夹，卸载只删除程序本身，文件仍在 <code>%USERPROFILE%\Stacky</code> 或你指定的目录里。</p>
</details>

<details>
<summary><b>覆盖安装会重置我的分类和布局吗？</b></summary>
<p>不会。安装脚本会先通过 <code>--quit</code> 通知运行实例优雅退出，并对可能被外部进程占用的底层文件做原位重命名避让；<code>config.json</code> 使用 <code>onlyifdoesntexist</code> 保护，安装路径也会从注册表读取上次的选择。</p>
</details>

<details>
<summary><b>为什么卡片外的透明区域不挡桌面框选？</b></summary>
<p>卡片窗口只在实际内容区域响应鼠标，四周留白通过 <code>WM_NCHITTEST</code> 返回穿透，点击会直接落到桌面。</p>
</details>

<details>
<summary><b>媒体小组件的进度条为什么有时拖不动？</b></summary>
<p>进度控制依赖播放器是否开放系统媒体会话的位置接口。支持的播放器可直接跳转；不支持的会给出提示并回滚到真实位置，不会显示假进度。</p>
</details>

<details>
<summary><b>换电脑怎么迁移？</b></summary>
<p>安装版可在「设置 → 备份」导出配置，或复制 <code>%APPDATA%\Stacky</code> 与 <code>%USERPROFILE%\Stacky</code> 目录到新机器对应位置；绿色便携版直接复制解压目录与物理分类目录即可。</p>
</details>

## 设计约束

- 不注入资源管理器、不修改系统文件、不安装驱动
- 卡片窗口停靠在壁纸层，不占用普通窗口层级
- 配置、图标缓存、分类目录全部保存在本机，不上传云端
- 安装与卸载均在当前用户权限内完成，不请求管理员权限

## 反馈

遇到问题或有建议，请在 [Issues](../../issues) 中反馈，并附上 Windows 版本、Stacky 版本和复现步骤。
