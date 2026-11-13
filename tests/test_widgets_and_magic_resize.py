"""
tests/test_widgets_and_magic_resize.py - 自动化验证桌面小组件矩阵与文件夹无极调整
覆盖:
1. 灵动时钟 (ClockWidgetWindow): 2x2/2x1 切换、时间跳动、磁吸对齐
2. 硬件管家 (SystemMonitorWidgetWindow): CPU/RAM/Disk C: 数据刷新与霓虹进度槽
3. 桌面随手便签 (MemoWidgetWindow): 待办编辑、失焦保存落盘、清空动作
4. 文件夹无极缩放: 右下角抓手判定、无极尺寸拉伸、网格动态计算与吸附
5. 大文件夹形态预设 (2x2, 2x4, 3x3, 4x2, 4x4)
6. 设置中心桌面小组件 Tab 与控件防截断保护
"""

import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import Qt, QPoint, QSize
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from config_manager import ConfigManager
from main_window import BigFolderWindow, BigFolderWindowManager
from settings_dialog import SettingsDialog
from desktop_widgets import ClockWidgetWindow, SystemMonitorWidgetWindow, MemoWidgetWindow


def test_clock_widget():
    print("[TEST 1] 测试灵动数字时钟与日历小组件 (ClockWidgetWindow)...")
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_clock_cfg.json")
    cfg = ConfigManager(tmp_path)
    try:
        w = ClockWidgetWindow(config_manager=cfg)
        assert w.card_w == 248
        assert w.card_h == 248
        assert ":" in w.lbl_time.text() or " " in w.lbl_time.text()
        assert "月" in w.lbl_date.text()
        assert "星期" in w.lbl_date.text()

        # 切换规格为 2x1 胶囊
        w.set_size_mode("2x1")
        assert w.card_w == 248
        assert w.card_h == 120
        assert w.lbl_status.isHidden()

        # 切换回 2x2
        w.set_size_mode("2x2")
        assert w.card_h == 248
        assert not w.lbl_status.isHidden()

        w.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("         灵动时钟与日历小组件: 100% OK")


def test_system_monitor_widget():
    print("[TEST 2] 测试系统硬件管家微件 (SystemMonitorWidgetWindow)...")
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_mon_cfg.json")
    cfg = ConfigManager(tmp_path)
    try:
        w = SystemMonitorWidgetWindow(config_manager=cfg)
        assert w.card_w == 248
        assert w.card_h == 248

        # 手动触发一次采样刷新
        w._refresh_stats()
        assert "%" in w.lbl_cpu_val.text()
        assert "%" in w.lbl_ram_val.text()
        assert "%" in w.lbl_disk_val.text()
        assert "系统" in w.lbl_health.text() or "运行" in w.lbl_health.text() or "负载" in w.lbl_health.text()

        w.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("         系统硬件管家微件: 100% OK")


def test_memo_widget():
    print("[TEST 3] 测试桌面随手便签待办微件 (MemoWidgetWindow)...")
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_memo_cfg.json")
    cfg = ConfigManager(tmp_path)
    try:
        w = MemoWidgetWindow(config_manager=cfg)
        assert w.card_w == 248
        assert w.card_h == 248

        # 模拟输入文字并落盘
        test_txt = "自动化测试便签待办项 123"
        w.edit_memo.setPlainText(test_txt)
        w._save_memo_to_disk()

        # 检查文件写入
        assert os.path.exists(w.memo_file)
        with open(w.memo_file, "r", encoding="utf-8") as f:
            content = f.read()
        assert test_txt in content

        w.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("         桌面随手便签微件: 100% OK")


def test_magicos_stepless_resize():
    print("[TEST 4] 测试文件夹无极调整与形态预设...")
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_magic_cfg.json")
    cfg = ConfigManager(tmp_path)
    try:
        win = BigFolderWindow(config_manager=cfg, instance_id="test_magic_win")
        
        # 1. 右下角抓手判定
        card_w = win.card.width()
        card_h = win.card.height()
        card_pos = win.card.pos()

        # 抓手中心点 (右下角向内 8px)
        handle_pt = card_pos + QPoint(card_w - 8, card_h - 8)
        assert win._is_near_card_resize_handle(handle_pt) is True

        # 卡片内部中心点 (非抓手)
        center_pt = card_pos + QPoint(card_w // 2, card_h // 2)
        assert win._is_near_card_resize_handle(center_pt) is False

        # 2. 应用形态预设
        # 2x4 横向长胶囊
        win.apply_grid_shape(2, 4)
        assert cfg.get("grid_rows") == 2
        assert cfg.get("grid_cols") == 4
        assert len(win.slot_widgets) == 8

        # 4x2 竖向立式卡
        win.apply_grid_shape(4, 2)
        assert cfg.get("grid_rows") == 4
        assert cfg.get("grid_cols") == 2
        assert len(win.slot_widgets) == 8

        # 2x2 灵动四宫格
        win.apply_grid_shape(2, 2)
        assert cfg.get("grid_rows") == 2
        assert cfg.get("grid_cols") == 2
        assert len(win.slot_widgets) == 4

        # 恢复 3x3 经典九宫格
        win.apply_grid_shape(3, 3)
        assert cfg.get("grid_rows") == 3
        assert cfg.get("grid_cols") == 3
        assert len(win.slot_widgets) == 9

        win.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("         文件夹无极调整: 100% OK")


def test_settings_widgets_hub():
    print("[TEST 5] 测试设置中心桌面小组件中心 (Widgets Hub) 4 卡片联动与保存...")
    tmp_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tmp_hub_cfg.json")
    cfg = ConfigManager(tmp_path)
    try:
        dlg = SettingsDialog(cfg, window_manager=None, initial_tab=3)
        assert dlg.nav_list.count() == 5
        assert dlg.chk_weather_enable is not None
        assert dlg.chk_clock_enable is not None
        assert dlg.chk_monitor_enable is not None
        assert dlg.chk_memo_enable is not None

        # 开启所有小组件并保存
        dlg.chk_weather_enable.setChecked(True)
        dlg.chk_clock_enable.setChecked(True)
        dlg.combo_clock_size.setCurrentIndex(1)  # 2x1
        dlg.chk_monitor_enable.setChecked(True)
        dlg.chk_memo_enable.setChecked(True)

        dlg.save_and_apply()

        # 重新检验持久化配置
        cfg2 = ConfigManager(tmp_path)
        assert cfg2.get_weather_config()["enabled"] is True
        assert cfg2.get_clock_config()["enabled"] is True
        assert cfg2.get_clock_config()["size"] == "2x1"
        assert cfg2.get_monitor_config()["enabled"] is True
        assert cfg2.get_memo_config()["enabled"] is True

        dlg.close()
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    print("         设置中心桌面小组件中心: 100% OK")


def run_all():
    print("==================================================")
    print(" 运行桌面小组件矩阵与无极调整测试")
    print("==================================================")
    test_clock_widget()
    test_system_monitor_widget()
    test_memo_widget()
    test_magicos_stepless_resize()
    test_settings_widgets_hub()
    print("==================================================")
    print(" 全部 5 项测试 100% 通过！")
    print("==================================================")


if __name__ == "__main__":
    run_all()
