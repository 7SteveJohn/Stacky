"""
Stacky (灵叠) - Web Bookmark Creation & Management Dialog
Refined Win11 Fluent / Linear-style dialog with immersive title bar chrome and elevated card inputs.
"""

from typing import Optional, List, Dict, Any
from PySide6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QApplication, QFrame
)
from PySide6.QtCore import Qt

from icon_utils import extract_site_name_from_url, get_ui_chevron_icon_path
from ui_kit import (
    CenteredDialog,
    UI_FONT_CSS,
    ui_font,
    apply_win11_window_chrome,
    get_theme_tokens,
    menu_qss,
)


def _resolve_dialog_theme(parent=None, theme: Optional[str] = None, config_manager=None) -> str:
    if isinstance(theme, str) and theme.strip():
        return theme.strip().lower()
    if config_manager is not None and hasattr(config_manager, "get"):
        return str(config_manager.get("theme", "dark") or "dark").lower()
    cur = parent
    while cur is not None:
        t = getattr(cur, "theme", None) or cur.property("theme")
        if isinstance(t, str) and t.strip():
            return t.strip().lower()
        cm = getattr(cur, "config_manager", None)
        if cm is not None and hasattr(cm, "get"):
            return str(cm.get("theme", "dark") or "dark").lower()
        cur = cur.parentWidget()
    for top in QApplication.topLevelWidgets():
        cm = getattr(top, "config_manager", None)
        if cm is not None and hasattr(cm, "get"):
            return str(cm.get("theme", "dark") or "dark").lower()
    return "dark"


class AddWebBookmarkDialog(CenteredDialog):
    """
    Sleek dialog for adding or editing a web URL bookmark.
    Auto-detects URLs from clipboard, extracts domain names, provides subgroup picker,
    and adapts seamlessly to dark / light / oled themes with Win11 DWM chrome.
    """

    def __init__(
        self,
        parent=None,
        initial_url: str = "",
        initial_name: str = "",
        initial_subgroup: str = "网页书签",
        existing_subgroups: Optional[List[str]] = None,
        theme: Optional[str] = None,
        config_manager=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("添加网页快捷方式")
        self.setFixedSize(468, 336)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self.setFont(ui_font(13))

        self.config_manager = config_manager
        self.theme: str = _resolve_dialog_theme(parent, theme, config_manager)
        self.setProperty("theme", self.theme)

        self.custom_icon_path: str = ""
        self.existing_subgroups = existing_subgroups or ["网页书签"]

        if not initial_url:
            cb = QApplication.clipboard()
            cb_text = (cb.text() or "").strip() if cb is not None else ""
            if (
                cb_text
                and len(cb_text) < 2048
                and not any(ch in cb_text for ch in ("\n", "\r", " ", "\t"))
                and cb_text.lower().startswith(("http://", "https://"))
            ):
                initial_url = cb_text
                if not initial_name:
                    initial_name = extract_site_name_from_url(initial_url)

        self._init_ui(initial_url, initial_name, initial_subgroup)
        self.set_theme(self.theme)

    def showEvent(self, event):
        super().showEvent(event)
        apply_win11_window_chrome(self, self.theme)

    def set_theme(self, theme: str):
        self.theme = (theme or "dark").strip().lower()
        self.setProperty("theme", self.theme)
        tok = get_theme_tokens(self.theme)
        is_light = self.theme == "light"
        is_oled = self.theme == "oled"
        chev_normal = get_ui_chevron_icon_path(False, light=is_light)
        chev_hover = get_ui_chevron_icon_path(True, light=is_light)

        if is_light:
            dlg_bg = "#f3f5f8"
            card_bg = "#ffffff"
            card_border = "rgba(15, 23, 42, 0.075)"
            label_fg = "#475569"
            field_bg = "#f8fafc"
            field_hover_bg = "#ffffff"
            field_focus_bg = "#ffffff"
            field_border = "rgba(15, 23, 42, 0.13)"
            field_hover_border = "rgba(15, 23, 42, 0.24)"
            popup_bg = "#ffffff"
            popup_border = "rgba(15, 23, 42, 0.12)"
            popup_hover = "rgba(15, 23, 42, 0.05)"
            cancel_bg = "#ffffff"
            cancel_hover_bg = "#f8fafc"
            cancel_border = "rgba(15, 23, 42, 0.14)"
            cancel_hover_border = "rgba(15, 23, 42, 0.24)"
            footer_border = "rgba(15, 23, 42, 0.065)"
        else:
            dlg_bg = "#07080a" if is_oled else "#111318"
            card_bg = "#0e1015" if is_oled else "#171a22"
            card_border = "rgba(255, 255, 255, 0.075)" if is_oled else "rgba(255, 255, 255, 0.065)"
            label_fg = "#94a3b8"
            field_bg = "#14171f" if is_oled else "#1e222d"
            field_hover_bg = "#191d27" if is_oled else "#242936"
            field_focus_bg = "#161a24" if is_oled else "#1e222d"
            field_border = "rgba(255, 255, 255, 0.10)"
            field_hover_border = "rgba(255, 255, 255, 0.20)"
            popup_bg = "#101218" if is_oled else "#181b24"
            popup_border = "rgba(255, 255, 255, 0.12)"
            popup_hover = "rgba(255, 255, 255, 0.08)"
            cancel_bg = "#161922" if is_oled else "#1e222d"
            cancel_hover_bg = "#1d222e" if is_oled else "#262b38"
            cancel_border = "rgba(255, 255, 255, 0.10)"
            cancel_hover_border = "rgba(255, 255, 255, 0.20)"
            footer_border = "rgba(255, 255, 255, 0.06)"

        fg = tok["text_primary"]
        cancel_fg = "#1e293b" if is_light else "#e2e8f0"

        self.setStyleSheet(f"""
            QDialog {{
                background-color: {dlg_bg};
                color: {fg};
                font-family: {UI_FONT_CSS};
                font-size: 13px;
            }}
            QFrame#bookmarkCard {{
                background-color: {card_bg};
                border: 1px solid {card_border};
                border-radius: 12px;
            }}
            QFrame#bookmarkFooter {{
                background-color: {dlg_bg};
                border: none;
                border-top: 1px solid {footer_border};
            }}
            QLabel {{
                color: {label_fg};
                font-family: {UI_FONT_CSS};
                font-size: 12px;
                font-weight: 600;
                background: transparent;
            }}
            QLineEdit {{
                background-color: {field_bg};
                color: {fg};
                border: 1px solid {field_border};
                border-radius: 8px;
                padding: 7px 12px;
                font-family: {UI_FONT_CSS};
                font-size: 13px;
                selection-background-color: #3b82f6;
            }}
            QLineEdit:hover {{
                border: 1px solid {field_hover_border};
                background-color: {field_hover_bg};
            }}
            QLineEdit:focus {{
                border: 1.5px solid #3b82f6;
                background-color: {field_focus_bg};
            }}
            QComboBox {{
                background-color: {field_bg};
                color: {fg};
                border: 1px solid {field_border};
                border-radius: 8px;
                padding: 6px 30px 6px 12px;
                font-family: {UI_FONT_CSS};
                font-size: 13px;
                selection-background-color: #3b82f6;
            }}
            QComboBox:hover {{
                border: 1px solid {field_hover_border};
                background-color: {field_hover_bg};
            }}
            QComboBox:focus {{
                border: 1.5px solid #3b82f6;
                background-color: {field_focus_bg};
            }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: top right;
                border: none;
                width: 26px;
                background: transparent;
            }}
            QComboBox::down-arrow {{
                image: url("{chev_normal}");
                width: 12px;
                height: 12px;
                margin-right: 8px;
            }}
            QComboBox::down-arrow:hover {{
                image: url("{chev_hover}");
            }}
            QComboBox QAbstractItemView {{
                background-color: {popup_bg};
                color: {fg};
                border: 1px solid {popup_border};
                border-radius: 8px;
                selection-background-color: #3b82f6;
                selection-color: #ffffff;
                padding: 4px;
                outline: none;
            }}
            QComboBox QAbstractItemView::item {{
                height: 28px;
                padding: 0 8px;
                border-radius: 5px;
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: {popup_hover};
            }}
            QPushButton#btnCancel {{
                background-color: {cancel_bg};
                color: {cancel_fg};
                border: 1px solid {cancel_border};
                border-radius: 7px;
                min-width: 84px;
                height: 32px;
                padding: 0px 16px;
                font-family: {UI_FONT_CSS};
                font-size: 13px;
                font-weight: 500;
            }}
            QPushButton#btnCancel:hover {{
                background-color: {cancel_hover_bg};
                border-color: {cancel_hover_border};
            }}
            QPushButton#btnConfirm {{
                background-color: #3b82f6;
                color: #ffffff;
                border: none;
                border-radius: 7px;
                min-width: 96px;
                height: 32px;
                padding: 0px 18px;
                font-family: {UI_FONT_CSS};
                font-size: 13px;
                font-weight: 600;
            }}
            QPushButton#btnConfirm:hover {{
                background-color: #2563eb;
            }}
            QPushButton#btnConfirm:pressed {{
                background-color: #1d4ed8;
            }}
            {menu_qss(self.theme)}
        """)
        if self.isVisible() or bool(self.internalWinId()):
            apply_win11_window_chrome(self, self.theme)

    def _init_ui(self, initial_url: str, initial_name: str, initial_subgroup: str):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        body = QVBoxLayout()
        body.setContentsMargins(20, 18, 20, 14)
        body.setSpacing(12)

        card = QFrame(self)
        card.setObjectName("bookmarkCard")
        form = QVBoxLayout(card)
        form.setContentsMargins(18, 16, 18, 16)
        form.setSpacing(10)

        # 1. URL Line
        lbl_url = QLabel("网址链接 (URL)", card)
        form.addWidget(lbl_url)

        self.edit_url = QLineEdit(initial_url, card)
        self.edit_url.setPlaceholderText("https://example.com")
        self.edit_url.textChanged.connect(self._on_url_changed)
        form.addWidget(self.edit_url)

        # 2. Title Line
        lbl_name = QLabel("显示名称", card)
        form.addWidget(lbl_name)

        self.edit_name = QLineEdit(initial_name, card)
        self.edit_name.setPlaceholderText("例如：GitHub、哔哩哔哩、Notion…")
        form.addWidget(self.edit_name)

        # 3. Subgroup Row
        lbl_sg = QLabel("归属子分组", card)
        form.addWidget(lbl_sg)

        self.combo_subgroup = QComboBox(card)
        self.combo_subgroup.setEditable(True)
        le = self.combo_subgroup.lineEdit()
        if le is not None:
            le.setPlaceholderText("选择或输入新子分组")
        all_sgs = list(self.existing_subgroups)
        if "网页书签" not in all_sgs:
            all_sgs.append("网页书签")
        for sg in all_sgs:
            if sg:
                self.combo_subgroup.addItem(sg)

        idx = self.combo_subgroup.findText(initial_subgroup)
        if idx >= 0:
            self.combo_subgroup.setCurrentIndex(idx)
        else:
            self.combo_subgroup.setEditText(initial_subgroup or "网页书签")
        form.addWidget(self.combo_subgroup)

        body.addWidget(card)
        body.addStretch()
        outer.addLayout(body, stretch=1)

        # 4. Clean Footer Row
        footer = QFrame(self)
        footer.setObjectName("bookmarkFooter")
        btn_layout = QHBoxLayout(footer)
        btn_layout.setContentsMargins(20, 12, 20, 12)
        btn_layout.setSpacing(10)
        btn_layout.addStretch()

        btn_cancel = QPushButton("取消", footer)
        btn_cancel.setObjectName("btnCancel")
        btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_confirm = QPushButton("添加书签", footer)
        btn_confirm.setObjectName("btnConfirm")
        btn_confirm.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_confirm.clicked.connect(self._on_confirm)
        btn_layout.addWidget(btn_confirm)

        outer.addWidget(footer)

    def _on_url_changed(self, text: str):
        clean_url = text.strip()
        curr_name = self.edit_name.text().strip()
        if clean_url and (not curr_name or curr_name == "网页书签"):
            extracted = extract_site_name_from_url(clean_url)
            if extracted and extracted != "网页书签":
                self.edit_name.setText(extracted)

    def _on_confirm(self):
        url = self.edit_url.text().strip()
        if not url:
            self.edit_url.setFocus()
            return
        if not url.lower().startswith(("http://", "https://", "steam://")):
            url = "https://" + url

        name = self.edit_name.text().strip()
        if not name:
            name = extract_site_name_from_url(url)

        self.result_item = {
            "name": name,
            "path": url,
            "args": "",
            "type": "web",
            "subgroup": self.combo_subgroup.currentText().strip() or "网页书签",
        }
        if self.custom_icon_path:
            self.result_item["custom_icon"] = self.custom_icon_path
        self.accept()

    def get_bookmark_data(self) -> Optional[Dict[str, Any]]:
        return getattr(self, "result_item", None)


import sys as _sys  # noqa: E402
_sys.modules.setdefault("web_dialog", _sys.modules[__name__])
_sys.modules.setdefault("stacky.ui.web_dialog", _sys.modules[__name__])
