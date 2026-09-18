QSS = r"""
* { font-family: "Microsoft YaHei UI", "Segoe UI"; color: #202020; }
MainWindow, QMainWindow, QWidget#root,
QWidget#resourceInterface, QWidget#transferInterface, QWidget#settingsInterface,
QWidget#searchInterface, QWidget#backupInterface, QWidget#imageInterface,
QWidget#webdavMappingInterface { background: #f3f3f3; }
QScrollArea#settingsScroll, QScrollArea#settingsScroll > QWidget > QWidget, QWidget#settingsContent,
QScrollArea#statisticsScroll, QScrollArea#statisticsScroll > QWidget > QWidget, QWidget#statisticsContent { background: transparent; border: none; }
QFrame#fluentSettingsPanel { background: transparent; border: none; }
QStatusBar#fluentStatusBar { background: transparent; border-top: 1px solid #e2e2e2; }
QFrame#sidebar { background: rgba(249,249,249,245); border-right: 1px solid #dedede; }
QFrame#navSidebar { background: #f7f7f7; border-right: 1px solid #dedede; }
QLabel#brand { font-size: 19px; font-weight: 650; color: #1f1f1f; padding: 4px 8px; }
QLabel#navFooter { color: #888888; font-size: 11px; padding: 8px; }
QPushButton#navToggle { font-size: 18px; padding: 2px; min-width: 28px; min-height: 28px; }
QPushButton#navButton { background: transparent; border: none; text-align: left; padding: 11px 13px; font-size: 14px; }
QPushButton#navButton:hover { background: #ededed; }
QPushButton#navButton:checked { background: #e5f1fb; color: #005a9e; font-weight: 600; border-left: 3px solid #0067c0; padding-left: 10px; }
QFrame#card { background: #ffffff; border: 1px solid #e5e5e5; border-radius: 10px; }
QFrame#metricCard, QFrame#statsFilterCard, QFrame#statsChartCard { background: #ffffff; border: 1px solid #e1e5e9; border-radius: 12px; }
QLabel#metricCaption { color: #666666; font-size: 12px; }
QLabel#statsTimeCaption { color: #4a5560; font-size: 13px; font-weight: 600; }
QLabel#metricValue { color: #005a9e; background: #eaf4fb; border: 1px solid #c9e4f5; border-radius: 9px; padding: 7px 10px; font-size: 17px; font-weight: 650; }
QLabel#statusVersion { color: #555555; padding: 2px 8px; font-weight: 600; }
QLabel#statusSpeed { color: #005a9e; background: #edf6fc; border: 1px solid #c9e4f5; border-radius: 7px; padding: 2px 7px; font-weight: 600; }
QLabel#statusRiskMode { color: #7a2500; background: #ffd59a; border: 1px solid #e7a646; border-radius: 7px; padding: 2px 8px; font-weight: 700; }
QLabel#speedPill { color: #005a9e; background: #edf6fc; border: 1px solid #c9e4f5; border-radius: 8px; padding: 5px 9px; font-weight: 600; }
QLabel#transferTotalPill { color: #005a9e; background: #edf6fc; border: 1px solid #c9e4f5; border-radius: 9px; padding: 7px 14px; font-weight: 600; }
QLabel#title { font-size: 24px; font-weight: 600; }
QLabel#subtitle { color: #666666; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#pathPill, QFrame#pathPill { color: #555555; background: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 5px; padding: 2px; }
QFrame#pathPill QPushButton#breadcrumbButton { background: transparent; border: none; padding: 4px 7px; min-height: 18px; }
QFrame#pathPill QPushButton#breadcrumbButton:hover { background: #e5e5e5; }
QFrame#pathPill QLabel#breadcrumbSeparator { color: #777777; background: transparent; border: none; }
QLabel#dropHint { color: #466b86; background: #edf6fc; border-radius: 5px; padding: 8px 11px; }
QLabel#success { color: #0f7b0f; font-weight: 600; }
QLabel#error { color: #c42b1c; font-weight: 600; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateTimeEdit { background: #ffffff; border: 1px solid #c7c7c7; border-bottom: 2px solid #8a8a8a; border-radius: 5px; padding: 7px 9px; min-height: 20px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QDateTimeEdit:focus { border-bottom-color: #0067c0; }
QDateTimeEdit#statisticsDateEdit { border: 1px solid #b8c0c8; border-radius: 8px; padding: 7px 9px; min-height: 22px; font-size: 13px; font-weight: 600; }
QDateTimeEdit#statisticsDateEdit:focus { border-color: #0067c0; }
QDateTimeEdit#statisticsDateEdit::drop-down { width: 22px; background: transparent; border: none; border-left: 1px solid #d8dde2; border-top-right-radius: 7px; border-bottom-right-radius: 7px; }
QPushButton { background: #ffffff; border: 1px solid #c7c7c7; border-radius: 5px; padding: 7px 14px; min-height: 20px; }
QPushButton:hover { background: #f6f6f6; }
QPushButton:pressed { background: #eeeeee; }
QPushButton#primary { background: #0067c0; border-color: #0067c0; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #1975c5; }
QPushButton:disabled { color: #999999; background: #eeeeee; border-color: #dddddd; }
QTreeWidget, QListWidget, QTableWidget, QTextEdit { background: #ffffff; border: 1px solid #e2e2e2; border-radius: 7px; selection-background-color: #cce8ff; selection-color: #202020; outline: none; }
QTreeWidget#repositoryTree::item { min-height: 32px; padding: 2px 4px; }
QTreeWidget#repositoryTree[detailList="true"]::item { min-height: 32px; padding: 2px 4px 2px 0; }
QTreeWidget#repositoryTree[detailList="true"]::indicator { margin-left: 7px; margin-right: 7px; }
QTreeWidget#repositoryTree::item:selected { background: #cce8ff; border-radius: 3px; }
QHeaderView::section { background: #fafafa; border: none; border-right: 1px solid #d6d6d6; border-bottom: 1px solid #e5e5e5; padding: 7px; font-weight: 600; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: transparent; padding: 8px 18px; margin-right: 4px; border-bottom: 3px solid transparent; }
QTabBar::tab:hover { background: #f3f3f3; border-radius: 5px; }
QTabBar::tab:selected { color: #0067c0; font-weight: 600; border-bottom-color: #0067c0; }
QMenu { background: #ffffff; border: 1px solid #d8d8d8; border-radius: 6px; padding: 5px; }
QMenu::item { padding: 7px 24px; border-radius: 4px; }
QMenu::item:selected { background: #e8f3fb; }
QFrame#dropArea { background: #fafcff; border: 2px dashed #8bbbe8; border-radius: 10px; }
QFrame#dropArea[dragging="true"] { background: #e8f3fb; border-color: #0067c0; }
QProgressBar { border: 1px solid #d7d7d7; background: #eeeeee; border-radius: 5px; min-height: 18px; max-height: 18px; text-align: center; color: #202020; font-weight: 600; }
QProgressBar::chunk { background: #60aee8; border-radius: 4px; }
QCheckBox { spacing: 7px; padding-left: 4px; }
QCheckBox::indicator { width: 17px; height: 17px; background: #ffffff; border: 1px solid #777777; border-radius: 3px; }
QCheckBox::indicator:hover { border-color: #0067c0; background: #f3f9fd; }
QCheckBox::indicator:checked { background: #0067c0; border-color: #0067c0; image: url(modelscope_manager/assets/check.svg); }
QCheckBox::indicator:checked:hover { background: #1975c5; border-color: #1975c5; }
QCheckBox::indicator:disabled { background: #eeeeee; border-color: #bdbdbd; }
QSplitter::handle { background: #d5d5d5; width: 1px; margin-left: 3px; margin-right: 3px; }
QSplitter::handle:hover { background: #0067c0; }
QToolTip { background: #ffffff; color: #202020; border: 1px solid #cccccc; padding: 6px; }
"""


DARK_QSS = r"""
* { font-family: "Microsoft YaHei UI", "Segoe UI"; color: #e8e8e8; }
MainWindow, QMainWindow, QWidget#root,
QWidget#resourceInterface, QWidget#transferInterface, QWidget#settingsInterface,
QWidget#searchInterface, QWidget#backupInterface, QWidget#imageInterface,
QWidget#webdavMappingInterface { background: #202124; }
QScrollArea#settingsScroll, QScrollArea#settingsScroll > QWidget > QWidget, QWidget#settingsContent,
QScrollArea#statisticsScroll, QScrollArea#statisticsScroll > QWidget > QWidget, QWidget#statisticsContent { background: transparent; border: none; }
QFrame#fluentSettingsPanel { background: transparent; border: none; }
QStatusBar#fluentStatusBar { background: transparent; border-top: 1px solid #41454d; }
QFrame#sidebar, QFrame#navSidebar { background: #25272b; border-right: 1px solid #3b3e44; }
QLabel#brand { font-size: 19px; font-weight: 650; color: #f2f2f2; padding: 4px 8px; }
QLabel#navFooter, QLabel#subtitle { color: #b7bac0; }
QPushButton#navToggle { font-size: 18px; padding: 2px; min-width: 28px; min-height: 28px; }
QPushButton#navButton { background: transparent; border: none; text-align: left; padding: 11px 13px; font-size: 14px; }
QPushButton#navButton:hover { background: #34373d; }
QPushButton#navButton:checked { background: #173b58; color: #d9edff; font-weight: 600; border-left: 3px solid #4aa3df; padding-left: 10px; }
QFrame#card { background: #2a2d32; border: 1px solid #41454d; border-radius: 10px; }
QFrame#metricCard, QFrame#statsFilterCard, QFrame#statsChartCard { background: #2a2d32; border: 1px solid #414b55; border-radius: 12px; }
QLabel#metricCaption { color: #b7bac0; font-size: 12px; }
QLabel#statsTimeCaption { color: #c7cbd1; font-size: 13px; font-weight: 600; }
QLabel#metricValue { color: #d9edff; background: #173b58; border: 1px solid #285d82; border-radius: 9px; padding: 7px 10px; font-size: 17px; font-weight: 650; }
QLabel#statusVersion { color: #c7cbd1; padding: 2px 8px; font-weight: 600; }
QLabel#statusSpeed { color: #d9edff; background: #213d52; border: 1px solid #365d78; border-radius: 7px; padding: 2px 7px; font-weight: 600; }
QLabel#statusRiskMode { color: #ffd59a; background: #61300d; border: 1px solid #9c5b20; border-radius: 7px; padding: 2px 8px; font-weight: 700; }
QLabel#speedPill { color: #d9edff; background: #213d52; border: 1px solid #365d78; border-radius: 8px; padding: 5px 9px; font-weight: 600; }
QLabel#transferTotalPill { color: #d9edff; background: #213d52; border: 1px solid #365d78; border-radius: 9px; padding: 7px 14px; font-weight: 600; }
QLabel#title { font-size: 24px; font-weight: 600; }
QLabel#section { font-size: 15px; font-weight: 600; }
QLabel#pathPill, QFrame#pathPill { color: #d6d8dc; background: #30343a; border: 1px solid #4a4f58; border-radius: 5px; padding: 2px; }
QFrame#pathPill QPushButton#breadcrumbButton { color: #d6d8dc; background: transparent; border: none; padding: 4px 7px; min-height: 18px; }
QFrame#pathPill QPushButton#breadcrumbButton:hover { background: #41464e; }
QFrame#pathPill QLabel#breadcrumbSeparator { color: #aeb3bb; background: transparent; border: none; }
QLabel#dropHint { color: #b7dbf4; background: #213647; border-radius: 5px; padding: 8px 11px; }
QLabel#success { color: #74d680; font-weight: 600; }
QLabel#error { color: #ff8e84; font-weight: 600; }
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateTimeEdit { color: #f2f2f2; background: #202226; border: 1px solid #5a5f69; border-bottom: 2px solid #a5aab3; border-radius: 5px; padding: 7px 9px; min-height: 20px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTimeEdit:focus, QDateTimeEdit:focus { border-bottom-color: #4aa3df; }
QDateTimeEdit#statisticsDateEdit { border: 1px solid #5a6470; border-radius: 8px; padding: 7px 9px; min-height: 22px; font-size: 13px; font-weight: 600; }
QDateTimeEdit#statisticsDateEdit:focus { border-color: #4aa3df; }
QDateTimeEdit#statisticsDateEdit::drop-down { width: 22px; background: transparent; border: none; border-left: 1px solid #48515b; border-top-right-radius: 7px; border-bottom-right-radius: 7px; }
QComboBox QAbstractItemView { color: #f2f2f2; background: #2a2d32; selection-background-color: #174d73; }
QPushButton { color: #ededed; background: #30333a; border: 1px solid #5a5f69; border-radius: 5px; padding: 7px 14px; min-height: 20px; }
QPushButton:hover { background: #3a3e46; }
QPushButton:pressed { background: #24272d; }
QPushButton#primary { background: #1676b5; border-color: #1676b5; color: white; font-weight: 600; }
QPushButton#primary:hover { background: #2588c7; }
QPushButton:disabled { color: #858991; background: #292c31; border-color: #42464d; }
QTreeWidget, QListWidget, QTableWidget, QTextEdit { color: #eeeeee; background: #24272c; border: 1px solid #454a53; border-radius: 7px; selection-background-color: #174d73; selection-color: #ffffff; outline: none; }
QTreeWidget#repositoryTree::item { min-height: 32px; padding: 2px 4px; }
QTreeWidget#repositoryTree[detailList="true"]::item { min-height: 32px; padding: 2px 4px 2px 0; }
QTreeWidget#repositoryTree[detailList="true"]::indicator { margin-left: 7px; margin-right: 7px; }
QTreeWidget#repositoryTree::item:selected { background: #174d73; border-radius: 3px; }
QHeaderView::section { color: #e8e8e8; background: #30333a; border: none; border-right: 1px solid #4a4f58; border-bottom: 1px solid #4a4f58; padding: 7px; font-weight: 600; }
QTabWidget::pane { border: none; background: transparent; }
QTabBar::tab { background: transparent; padding: 8px 18px; margin-right: 4px; border-bottom: 3px solid transparent; }
QTabBar::tab:hover { background: #34373d; border-radius: 5px; }
QTabBar::tab:selected { color: #72bbeb; font-weight: 600; border-bottom-color: #4aa3df; }
QMenu { color: #eeeeee; background: #2d3036; border: 1px solid #4b5059; border-radius: 6px; padding: 5px; }
QMenu::item { padding: 7px 24px; border-radius: 4px; }
QMenu::item:selected { background: #174d73; }
QFrame#dropArea { background: #223442; border: 2px dashed #4c8fbe; border-radius: 10px; }
QFrame#dropArea[dragging="true"] { background: #1a465f; border-color: #63b5ed; }
QProgressBar { border: 1px solid #555b65; background: #30333a; border-radius: 5px; min-height: 18px; max-height: 18px; text-align: center; color: #ffffff; font-weight: 600; }
QProgressBar::chunk { background: #277db8; border-radius: 4px; }
QCheckBox { spacing: 7px; padding-left: 4px; }
QCheckBox::indicator { width: 17px; height: 17px; background: #202226; border: 1px solid #a0a5ad; border-radius: 3px; }
QCheckBox::indicator:hover { border-color: #63b5ed; background: #293d4c; }
QCheckBox::indicator:checked { background: #1676b5; border-color: #1676b5; image: url(modelscope_manager/assets/check.svg); }
QCheckBox::indicator:checked:hover { background: #2588c7; border-color: #2588c7; }
QCheckBox::indicator:disabled { background: #292c31; border-color: #545963; }
QSplitter::handle { background: #4a4f58; width: 1px; margin-left: 3px; margin-right: 3px; }
QSplitter::handle:hover { background: #4aa3df; }
QToolTip { background: #30333a; color: #f2f2f2; border: 1px solid #606671; padding: 6px; }
"""


ACRYLIC_LIGHT_QSS = r"""
MainWindow, QMainWindow, QWidget#root { background: #f3f3f3; }
QFrame#navSidebar { background: rgba(247,247,247,205); }
QFrame#card { background: rgba(255,255,255,225); }
"""

ACRYLIC_DARK_QSS = r"""
MainWindow, QMainWindow, QWidget#root { background: #202124; }
QFrame#navSidebar { background: rgba(37,39,43,205); }
QFrame#card { background: rgba(42,45,50,225); }
"""


def theme_qss(
    dark: bool,
    acrylic: bool = False,
    font_families: tuple[str, str] = ("Segoe UI", "Microsoft YaHei UI"),
    accent: str = "#0078D4",
    background_image: str = "",
) -> str:
    base = DARK_QSS if dark else QSS
    if acrylic:
        base += ACRYLIC_DARK_QSS if dark else ACRYLIC_LIGHT_QSS
    western, chinese = (value.replace('"', '') for value in font_families)
    base = base.replace('* { font-family: "Microsoft YaHei UI", "Segoe UI";', f'* {{ font-family: "{western}", "{chinese}";')
    custom = f"\nQPushButton#primary {{ background: {accent}; border-color: {accent}; }}\n"
    custom += f"QProgressBar::chunk {{ background: {accent}; }}\n"
    if background_image:
        nav = "rgba(18,19,23,188)" if dark else "rgba(248,249,252,240)"
        status = "rgba(18,19,23,178)" if dark else "rgba(248,249,252,236)"
        card = "rgba(28,29,34,204)" if dark else "rgba(255,255,255,207)"
        table = "rgba(25,26,31,207)" if dark else "rgba(255,255,255,214)"
        field = "rgba(27,28,33,220)" if dark else "rgba(255,255,255,226)"
        button = "rgba(46,48,55,220)" if dark else "rgba(250,250,252,226)"
        header = "rgba(44,46,53,225)" if dark else "rgba(246,247,250,232)"
        custom += (
            'QMainWindow, MainWindow, QWidget#root, FluentWindowBase { background: transparent; }\n'
            'StackedWidget, StackedWidget[isTransparent="true"] { background: transparent; }\n'
            'QWidget#resourceInterface, QWidget#transferInterface, QWidget#settingsInterface, '
            'QWidget#searchInterface, QWidget#backupInterface, QWidget#imageInterface, QWidget#webdavMappingInterface '
            f'{{ background-color: {"#202124" if dark else "#f3f3f3"}; }}\n'
            'QWidget#resourceInterface[skinBackground="true"], '
            'QWidget#transferInterface[skinBackground="true"], '
            'QWidget#settingsInterface[skinBackground="true"], '
            'QWidget#searchInterface[skinBackground="true"], '
            'QWidget#backupInterface[skinBackground="true"], '
            'QWidget#imageInterface[skinBackground="true"], '
            'QWidget#webdavMappingInterface[skinBackground="true"] { background: transparent; }\n'
            'QScrollArea, QScrollArea > QWidget > QWidget, QWidget#settingsContent, '
            'QWidget#statisticsContent { background: transparent; }\n'
            'NavigationInterface, QFrame#navSidebar, QFrame#sidebar '
            f'{{ background-color: {nav}; }}\n'
            f'QStatusBar#fluentStatusBar {{ background-color: {status}; }}\n'
            'QFrame#card, QFrame#metricCard, QFrame#statsFilterCard, QFrame#statsChartCard '
            f'{{ background-color: {card}; }}\n'
            'QTreeWidget, QListWidget, QTableWidget, QTextEdit '
            f'{{ background-color: {table}; }}\n'
            'QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTimeEdit, QDateTimeEdit '
            f'{{ background-color: {field}; }}\n'
            f'QPushButton {{ background-color: {button}; }}\n'
            f'QHeaderView::section {{ background-color: {header}; }}\n'
        )
    return base + custom
