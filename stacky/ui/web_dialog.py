"""
Stacky (灵叠) - Web Bookmark Creation & Management Dialog
Ultra-crisp system-style squircle translucent acrylic dialog for adding and editing web bookmarks.
"""

import os
from typing import Optional, List, Dict, Any
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QFileDialog, QApplication, QFrame
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QColor, QIcon

from icon_utils import extract_site_name_from_url, draw_bookmark_icon
from ui_kit import CenteredDialog


class AddWebBookmarkDialog(CenteredDialog):
    """
    Sleek dark-acrylic dialog for adding or editing a web URL bookmark.
    Auto-detects URLs from clipboard, extracts domain names, provides subgroup picker.
    """

    def __init__(
        self,
        parent=None,
        initial_url: str = "",
        initial_name: str = "",
        initial_subgroup: str = "网页书签",
        existing_subgroups: Optional[List[str]] = None
    ):
        super().__init__(parent)
        self.setWindowTitle("添加网页快捷方式")
        self.setFixedSize(460, 310)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self.custom_icon_path: str = ""
        self.existing_subgroups = existing_subgroups or ["网页书签"]

        # Check clipboard if initial_url is empty
        if not initial_url:
            cb_text = QApplication.clipboard().text().strip()
            if cb_text.startswith("http://") or cb_text.startswith("https://"):
                initial_url = cb_text
                if not initial_name:
                    initial_name = extract_site_name_from_url(initial_url)

        self._init_ui(initial_url, initial_name, initial_subgroup)

    def _init_ui(self, initial_url: str, initial_name: str, initial_subgroup: str):
        self.setStyleSheet("""
            QDialog {
                background-color: #17181f;
                color: #f8fafc;
                font-family: "Segoe UI Variable Display", "Segoe UI", "PingFang SC", "Microsoft YaHei UI", sans-serif;
                font-size: 13px;
            }
            QLabel {
                color: rgba(255, 255, 255, 210);
                font-size: 12px;
                font-weight: 500;
            }
            QLineEdit {
                background-color: rgba(255, 255, 255, 0.06);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 8px;
                padding: 7px 12px;
                font-size: 13px;
            }
            QLineEdit:hover {
                border: 1px solid rgba(255, 255, 255, 0.28);
                background-color: rgba(255, 255, 255, 0.09);
            }
            QLineEdit:focus {
                border: 1.5px solid #0078d4;
                background-color: rgba(255, 255, 255, 0.12);
            }
            QComboBox {
                background-color: rgba(255, 255, 255, 0.06);
                color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.16);
                border-radius: 8px;
                padding: 6px 12px;
                font-size: 13px;
            }
            QComboBox:hover {
                border: 1px solid rgba(255, 255, 255, 0.28);
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
            }
            QComboBox QAbstractItemView {
                background-color: #1c1d25;
                color: #f8fafc;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 8px;
                selection-background-color: rgba(255, 255, 255, 0.18);
                padding: 4px;
            }
            QPushButton#btnCancel {
                background-color: rgba(255, 255, 255, 0.08);
                color: #e2e8f0;
                border: 1px solid rgba(255, 255, 255, 0.14);
                border-radius: 8px;
                padding: 7px 20px;
                font-weight: 500;
            }
            QPushButton#btnCancel:hover {
                background-color: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.25);
            }
            QPushButton#btnConfirm {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #0078d4, stop:1 #0099ff);
                color: #ffffff;
                border: none;
                border-radius: 8px;
                padding: 7px 24px;
                font-weight: 600;
            }
            QPushButton#btnConfirm:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #1084d9, stop:1 #1fa2ff);
            }
            QPushButton#btnConfirm:pressed {
                background-color: #0060aa;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)

        # 1. URL Line
        lbl_url = QLabel("网址链接 (URL):", self)
        layout.addWidget(lbl_url)

        self.edit_url = QLineEdit(initial_url, self)
        self.edit_url.setPlaceholderText("https://example.com")
        self.edit_url.textChanged.connect(self._on_url_changed)
        layout.addWidget(self.edit_url)

        # 2. Title Line
        lbl_name = QLabel("显示名称 (网站标题):", self)
        layout.addWidget(lbl_name)

        self.edit_name = QLineEdit(initial_name, self)
        self.edit_name.setPlaceholderText("例如: GitHub、哔哩哔哩、Notion...")
        layout.addWidget(self.edit_name)

        # 3. Subgroup Row
        sub_layout = QHBoxLayout()
        sub_layout.setSpacing(10)

        sub_col = QVBoxLayout()
        sub_col.setSpacing(6)
        lbl_sg = QLabel("归属抽屉子分组 (Tab):", self)
        sub_col.addWidget(lbl_sg)

        self.combo_subgroup = QComboBox(self)
        self.combo_subgroup.setEditable(True)
        self.combo_subgroup.lineEdit().setPlaceholderText("选择或输入新子分组")
        all_sgs = list(self.existing_subgroups)
        if "网页书签" not in all_sgs:
            all_sgs.append("网页书签")
        for sg in all_sgs:
            if sg:
                self.combo_subgroup.addItem(sg)
        
        # Set initial subgroup
        idx = self.combo_subgroup.findText(initial_subgroup)
        if idx >= 0:
            self.combo_subgroup.setCurrentIndex(idx)
        else:
            self.combo_subgroup.setEditText(initial_subgroup or "网页书签")
        sub_col.addWidget(self.combo_subgroup)
        sub_layout.addLayout(sub_col)

        layout.addLayout(sub_layout)

        layout.addStretch()

        # 4. Buttons Row
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)
        btn_layout.addStretch()

        btn_cancel = QPushButton("取消", self)
        btn_cancel.setObjectName("btnCancel")
        btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(btn_cancel)

        btn_confirm = QPushButton("添加书签", self)
        btn_confirm.setObjectName("btnConfirm")
        btn_confirm.clicked.connect(self._on_confirm)
        btn_layout.addWidget(btn_confirm)

        layout.addLayout(btn_layout)

    def _on_url_changed(self, text: str):
        clean_url = text.strip()
        # If user hasn't typed a custom name yet, auto-extract site title
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
        if not (url.startswith("http://") or url.startswith("https://")):
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
