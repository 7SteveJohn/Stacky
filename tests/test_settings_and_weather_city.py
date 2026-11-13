"""
tests/test_settings_and_weather_city.py - 自动化验证设置系统重构与天气地区切换
覆盖内容:
1. 城市定位与离线/在线解析 (POPULAR_CITIES 与 resolve_city_coordinates)
2. WeatherService 自定义地区设置与即时生效
3. ConfigManager 天气位置持久化与读取
4. SettingsDialog 4大设置标签页控件与逻辑 (文件夹管理、打开方式、外观规格、天气地区)
5. 桌面大文件夹右键菜单精简降噪与 "大文件夹设置..." 入口
6. 界面与菜单 Emoji 清除验证 (全文字高质感)
"""

import os
import sys
import re
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from config_manager import ConfigManager
from weather_service import get_weather_service, POPULAR_CITIES, resolve_city_coordinates
from settings_dialog import SettingsDialog
from main_window import BigFolderWindow, BigFolderWindowManager
from weather_widget import WeatherWidgetWindow, CityPickerDialog


def test_city_resolution():
    print("[TEST 1] 测试热门城市坐标离线解析与 WeatherService 地区绑定...")
    # 1. 验证 POPULAR_CITIES 包含核心城市
    assert "成都" in POPULAR_CITIES
    assert "北京" in POPULAR_CITIES
    assert "上海" in POPULAR_CITIES
    assert "深圳" in POPULAR_CITIES
    assert "广州" in POPULAR_CITIES

    # 2. 验证离线解析
    res_cd = resolve_city_coordinates("成都")
    assert res_cd is not None
    assert abs(res_cd["lat"] - 30.6586) < 0.01
    assert abs(res_cd["lon"] - 104.0648) < 0.01
    assert res_cd["region"] == "四川"

    res_bj = resolve_city_coordinates("北京")
    assert res_bj is not None
    assert abs(res_bj["lat"] - 39.9042) < 0.01

    # 3. 验证 weather_service 切换自定义城市
    ws = get_weather_service()
    ws.set_custom_location(city="成都", region="四川", lat=30.6586, lon=104.0648, auto_location=False)
    data = ws.get_cached_data()
    assert data["city"] == "成都"
    assert data["region"] == "四川"
    print("         热门城市坐标离线解析与 WeatherService 地区绑定: 100% OK")


def test_config_weather_location():
    print("[TEST 2] 测试 ConfigManager 天气地区配置持久化...")
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_test_config.json")
    cfg = ConfigManager(tmp_path)
    try:
        # 初始默认值
        w_cfg = cfg.get_weather_config()
        assert "auto_location" in w_cfg
        assert "city" in w_cfg

        # 写入自定义地区
        cfg.set_weather_location(auto_location=False, city="杭州", region="浙江", lat=30.2741, lon=120.1551)
        
        # 重新加载配置验证持久化
        cfg2 = ConfigManager(tmp_path)
        w_cfg2 = cfg2.get_weather_config()
        assert w_cfg2["auto_location"] is False
        assert w_cfg2["city"] == "杭州"
        assert w_cfg2["region"] == "浙江"
        assert abs(w_cfg2["lat"] - 30.2741) < 0.01
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("         ConfigManager 天气地区配置持久化: 100% OK")


def test_settings_dialog():
    print("[TEST 3] 测试全局设置控制中心 (SettingsDialog) 4大标签页与控件联动...")
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_test_settings.json")
    cfg = ConfigManager(tmp_path)
    try:
        dlg = SettingsDialog(cfg, window_manager=None, initial_tab=0)
        
        # 1. 验证 5 大标签页
        assert dlg.nav_list.count() == 5
        tab_names = [dlg.nav_list.item(i).text() for i in range(5)]
        assert "文件夹" in tab_names
        assert "交互" in tab_names
        assert "外观" in tab_names
        assert any(n in ("小组件", "桌面天气卡片") for n in tab_names)
        assert "备份" in tab_names

        # 2. 验证 Tab 0: 文件夹分类列表已绑定
        assert dlg.folder_list.count() > 0

        # 3. 验证 Tab 1: 打开方式单选框存在并正确初始化
        assert dlg.rb_double is not None
        assert dlg.rb_hybrid is not None
        assert dlg.rb_single is not None

        # 4. 验证 Tab 3: 天气设置城市下拉列表
        assert dlg.combo_city.count() > 10
        dlg.rb_manual_loc.setChecked(True)
        dlg.combo_city.setCurrentText("成都")
        dlg.edit_city.setText("成都")
        
        # 5. 模拟保存并应用
        dlg.save_and_apply()

        # 验证配置已更新
        assert cfg.get_weather_config()["city"] == "成都"
        dlg.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("         SettingsDialog 4大标签页与控件联动: 100% OK")


def test_decluttered_context_menu():
    print("[TEST 4] 测试大文件夹右键菜单精简降噪与设置入口...")
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_test_menu.json")
    cfg = ConfigManager(tmp_path)
    try:
        win = BigFolderWindow(config_manager=cfg, instance_id="test_menu_win")
        menu = win.create_global_menu()

        # 验证菜单项总数精简为约 8 项核心功能 + 分割线，而非此前的 17+ 项杂乱菜单
        actions = menu.actions()
        action_texts = [act.text() for act in actions if act.text()]
        
        print(f"         精简后右键菜单项: {action_texts}")
        # 必须包含核心操作与全局设置
        assert any("设置" in t for t in action_texts), "必须包含设置入口"
        assert any("展开" in t for t in action_texts)
        assert any("切换分类" in t for t in action_texts)
        assert any("对齐" in t for t in action_texts)
        assert any("锁定" in t for t in action_texts)
        assert any("关闭此卡片" in t for t in action_texts)
        assert any("退出" in t for t in action_texts)

        # 验证移除了原先杂乱堆砌的子菜单 (如网格规格、工作模式、快捷键呼出目标等均移至设置对话框)
        assert not any("网格规格" in t for t in action_texts), "网格规格子菜单应移至设置"
        assert not any("工作模式" in t for t in action_texts), "工作模式子菜单应移至设置"
        assert not any("热键呼出动作" in t for t in action_texts), "呼出动作子菜单应移至设置"

        win.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("         大文件夹右键菜单精简降噪: 100% OK")


def test_emoji_elimination():
    print("[TEST 5] 验证核心界面与菜单中消除 Emoji (严控质感)...")
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_test_emoji.json")
    cfg = ConfigManager(tmp_path)
    try:
        # 1. 验证天气卡片右键菜单无 Emoji
        weather_win = WeatherWidgetWindow(x=100, y=100, theme="dark", widget_size="2x2", config_manager=cfg)
        w_menu = weather_win.create_context_menu()
        for act in w_menu.actions():
            txt = act.text()
            if txt:
                # 检查没有常见的杂乱表情符号
                for em in ["📍", "💧", "💨", "🧲", "🔄", "📐", "🔓", "🔒", "🎨", "❌", "🎯"]:
                    assert em not in txt, f"Weather menu item '{txt}' contains emoji '{em}'"

        # 2. 验证主卡片右键菜单无 Emoji
        win = BigFolderWindow(config_manager=cfg, instance_id="test_emoji_win")
        m_menu = win.create_global_menu()
        for act in m_menu.actions():
            txt = act.text()
            if txt:
                for em in ["📍", "💧", "💨", "🧲", "🔄", "📐", "🔓", "🔒", "🎨", "❌", "🎯"]:
                    assert em not in txt, f"Main window menu item '{txt}' contains emoji '{em}'"

        weather_win.close()
        win.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("         核心界面与菜单 Emoji 清除: 100% OK")


def test_district_survives_restart():
    print("[TEST 6] 测试区县在重启后自动生效（不被 IP 自动定位覆盖）...")
    import tempfile
    tmp_path = os.path.join(tempfile.mkdtemp(prefix="weather_district_"), "config.json")
    cfg = ConfigManager(tmp_path)
    cfg.set_weather_location(city="成都", region="四川", district="双流区",
                             lat=30.57324, lon=103.92271, auto_location=True)

    # 模拟重启：重新读配置并注入服务，不依赖任何网络
    w = ConfigManager(tmp_path).get_weather_config()
    ws = get_weather_service()
    ws.set_custom_location(city=w["city"], region=w["region"], lat=w["lat"], lon=w["lon"],
                           auto_location=w["auto_location"], district=w["district"])
    assert ws.custom_location is not None, "区县被自动定位冲掉了"
    assert ws.custom_location["district"] == "双流区"
    assert abs(ws.custom_location["lat"] - 30.57324) < 0.001
    assert ws.get_cached_data().get("district") == "双流区", "冷启动缓存仍显示市级"

    # 没选区县时，自动定位仍保持原行为
    ws.set_custom_location(city="成都", region="四川", lat=30.6586, lon=104.0648,
                           auto_location=True, district="")
    assert ws.custom_location is None
    print("         区县重启自动恢复: 100% OK")


def run_all():
    print("==================================================")
    print(" 运行设置系统重构与天气地区切换测试套件")
    print("==================================================")
    test_city_resolution()
    test_config_weather_location()
    test_settings_dialog()
    test_decluttered_context_menu()
    test_emoji_elimination()
    test_district_survives_restart()
    print("==================================================")
    print(" 全部 6 项测试 100% 通过！")
    print("==================================================")


if __name__ == "__main__":
    run_all()
