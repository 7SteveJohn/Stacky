# Stacky 灵叠

Windows 10/11 桌面视窗矩阵与应用启动台。用超椭圆卡片把常用应用、文件夹和小组件收纳到桌面，支持物理目录映射、网格吸附、拖拽排序与全局热键唤起。

## 功能

- **桌面卡片矩阵**：2×2 / 2×1 / 无极缩放的超椭圆卡片，16px 网格吸附与碰撞避让
- **物理目录映射**：卡片直接挂载真实文件夹，增删文件与资源管理器双向同步
- **应用启动台**：单击 / 双击打开，支持图标提取、自动分类与自定义排序
- **桌面小组件**：时钟日历、系统监控、便签待办、天气、媒体播放、文件中转站
- **全局热键**：默认 `Alt+Space` 唤起，托盘常驻
- **本地优先**：配置与图标缓存都在本机，不上传云端

## 系统要求

- Windows 10 / 11 x64
- 源码运行需要 Python 3.10+
- 安装版与便携版无需 Python

## 安装

从 [Releases](../../releases) 下载：

- `Stacky-灵叠-安装程序.exe` —— 安装版
- `Stacky-灵叠-绿色便携版.zip` —— 解压即用

## 从源码运行

```bash
pip install -r requirements.txt
python main.py
```

## 项目结构

```
main.py                  入口
stacky/
  core/                  窗口、配置、存储、桌面图层、网格
  widgets/               桌面卡片与小组件
  media/                 系统媒体会话、播放位置跳转
  services/              天气、文件索引
  ui/                    通用控件、图标、对话框
  utils/                 快捷方式、自启、迁移、搜索
BigFolderLauncher.spec   PyInstaller 配置
installer.iss            Inno Setup 安装脚本
```

模块按功能分目录。入口通过 `stacky/__init__.py` 把各子目录加入 `sys.path`，因此历史代码仍可使用扁平导入；新代码建议用 `from stacky.core import ...`。

## 构建

```bash
pip install -r requirements.txt pyinstaller
pyinstaller BigFolderLauncher.spec
```

生成安装包需要 Inno Setup 6：

```bash
iscc installer.iss
```

## 说明

- 不注入资源管理器、不修改系统文件；窗口以壁纸层停靠，免疫 `Win+D`
- 用户数据保存在 `%APPDATA%\Stacky` 与程序目录
- 媒体控制依赖 Windows 系统媒体会话（SMTC），仅对支持该接口的播放器生效
