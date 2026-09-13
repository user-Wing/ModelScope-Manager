import unittest

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from qfluentwidgets import ComboBox, FluentIcon, FluentWindow, MenuAnimationType, SettingCard, SettingCardGroup, SpinBox

from modelscope_manager.app import MainWindow
from modelscope_manager.fluent_ui import (
    CleanComboBox, ControlSettingCard, FluentSwitchButton, PanelSettingCard,
)


class FluentUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_main_window_and_custom_controls_use_pyside6_fluent_widgets(self):
        self.assertTrue(issubclass(MainWindow, FluentWindow))
        self.assertTrue(issubclass(CleanComboBox, ComboBox))
        self.assertTrue(issubclass(ControlSettingCard, SettingCard))

    def test_control_setting_card_accepts_arbitrary_control(self):
        control = SpinBox()
        card = ControlSettingCard(
            FluentIcon.FONT_SIZE, "字号", "立即应用", control
        )
        self.assertIs(card.control, control)
        self.assertIs(control.parent(), card)

    def test_control_setting_card_can_reserve_trailing_space(self):
        card = ControlSettingCard(
            FluentIcon.FONT_SIZE, "字号", "立即应用", SpinBox(), trailing_margin=40
        )
        trailing = card.hBoxLayout.itemAt(card.hBoxLayout.count() - 1).spacerItem()
        self.assertEqual(trailing.sizeHint().width(), 40)

    def test_panel_setting_card_uses_aligned_non_animated_collapsible_header(self):
        group = SettingCardGroup("复杂设置")
        card = PanelSettingCard(FluentIcon.SETTING, "详细设置", "按需展开", SpinBox(), group)
        group.addSettingCard(card)
        group.show()
        self.app.processEvents()
        collapsed_group_height = group.height()
        self.assertFalse(card.isExpanded())
        self.assertEqual(card.height(), 70)
        self.assertEqual(card.headerWidget.height(), 70)
        card.expandButton.click()
        self.assertTrue(card.isExpanded())
        self.app.processEvents()
        self.assertGreater(card.minimumHeight(), 70)
        self.assertGreater(group.height(), collapsed_group_height)
        card.expandButton.click()
        self.assertFalse(card.isExpanded())
        self.app.processEvents()
        self.assertEqual(card.height(), 70)
        self.assertTrue(card.panel.isHidden())

    def test_clean_combo_preserves_transparent_rounded_menu_without_shadow(self):
        combo = CleanComboBox()
        combo.addItem("浅色", userData="light")
        menu = combo._createComboMenu()
        combo._createComboMenu = lambda: menu
        calls = []
        menu.exec = lambda *args, **kwargs: calls.append((args, kwargs))
        combo._showComboMenu()
        self.assertTrue(menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground))
        self.assertTrue(menu.windowFlags() & Qt.WindowType.NoDropShadowWindowHint)
        self.assertFalse(calls[0][1]["ani"])
        self.assertEqual(calls[0][1]["aniType"], MenuAnimationType.NONE)

    def test_fluent_switch_exposes_checkbox_compatible_signal(self):
        switch = FluentSwitchButton()
        states = []
        switch.toggled.connect(states.append)
        switch.checkedChanged.emit(True)
        self.assertEqual(states, [True])

if __name__ == "__main__":
    unittest.main()
