"""Цвета, шрифты и размеры — в т.ч. под эталонный макет окна «Анализ»."""

import sys

# --- Экран как на макете (светлая схема + тёмная пара) ---
WINDOW_BG = ("#f2f2f2", "#1e1e1e")
TOPBAR_BG = ("#f2f2f2", "#1e1e1e")

# Акцент как на референсе (~#3A8DCC)
ACCENT = "#3a8dcc"
ACCENT_LIGHT = "#5dade2"
ACCENT_DARK = "#2e7aaf"
ACCENT_HOVER = "#4a9fd4"
ACCENT_ON_BORDER = ("#2e7aaf", "#5dade2")

# Вкладки верхнего ряда (скругление как на макете)
TAB_CORNER_RADIUS = 8
TAB_INACTIVE_FG = ("#e4e4e4", "#383838")
TAB_INACTIVE_TEXT = ("#1f1f1f", "#e8e8e8")
TAB_HOVER = ("#d8d8d8", "#454545")

# Кнопки ← →
NAV_BTN_FG = ("#e4e4e4", "#404040")
NAV_BTN_HOVER = ("#d4d4d4", "#505050")
NAV_BTN_TEXT = ("#333333", "#e0e0e0")
NAV_BTN_DISABLED = ("#c8c8c8", "#353535")

# Панель ленты под вкладками (светло-серая, скруглённая)
RIBBON_PANEL_BG = ("#e8e8e8", "#2c2c2c")
RIBBON_PANEL_BORDER = ("#c4c4c4", "#454545")
RIBBON_CORNER_RADIUS = 10
RIBBON_OUTER_PADX = 14
RIBBON_HEIGHT_DEFAULT = 118
# Лента «Анализ»: заголовок + 4 чекбокса + кнопка (с запасом под масштаб виджетов CTk)
RIBBON_HEIGHT_ANALYSIS = 168

# Совместимость со старыми именами
RIBBON_BORDER = RIBBON_PANEL_BORDER
RIBBON_FG_MAIN = RIBBON_PANEL_BG

GRAY_BORDER_IDLE = ("#8a9aaa", "#6b6b6b")
GRAY_ROW = ("#e0e0e0", "#3d3d3d")
GRAY_ROW_ALT = ("#ececec", "#2a2a2a")
GRAY_TEXT_MUTED = ("#5a5a5a", "#a8a8a8")
GRAY_TEXT = ("#1a1a1a", "#e4e4e4")
GRAY_LABEL = ("#2a2a2a", "#d0d0d0")

UPLOAD_BORDER_IDLE = ("gray62", "gray36")
UPLOAD_FG_IDLE = ("#f4f7fb", "#2c2c2c")
UPLOAD_ACTIVE_BORDER = ACCENT
UPLOAD_ACTIVE_FG = ("#ddeefb", "#1f3344")

STATUS_OK = "#2ecc71"
STATUS_WARN = "#e67e22"
STATUS_PENDING = ("gray40", "gray60")

STATUS_BAR_BG = ("#ebebeb", "#252525")
STATUS_BAR_BORDER = ("#d4d4d4", "#383838")
STATUS_BAR_TEXT = ("#303030", "#c8c8c8")

TOOL_FG = ("#eeeeee", "#333333")
TOOL_TEXT = ("#1a1a1a", "#ffffff")
TOOL_HOVER = ("#dddddd", "#444444")
TOOL_BORDER = ("#cccccc", "#444444")

PIPELINE_CARD_FG = ("#ffffff", "#2c2c2c")
PIPELINE_CARD_BORDER = ("#c8c8c8", "#454545")
SEPARATOR_LINE = ("#d0d0d0", "#505050")

ANALYSIS_WORKSPACE_BG = ("#e0e0e0", "#333333")
ANALYSIS_WORKSPACE_BORDER = ("#c0c0c0", "#404040")
ANALYSIS_WORKSPACE_INNER = ("#d8d8d8", "#383838")

DROP_PREVIEW_BORDER = "#5dade2"
GHOST_BORDER = ACCENT

# Шрифты: Segoe UI на Windows как в макете
if sys.platform == "win32":
    _F = "Segoe UI"
else:
    _F = "Arial"

FONT_TITLE = (_F, 26, "bold")
FONT_HEAD = (_F, 15, "bold")
FONT_SUB = (_F, 14)
FONT_BODY = (_F, 11)
FONT_SMALL = (_F, 10)
FONT_RIBBON = (_F, 12, "bold")
FONT_RIBBON_SECTION = (_F, 13, "bold")
FONT_LOGO = (_F, 22, "bold")
FONT_GRIP = ("Segoe UI", 14) if sys.platform == "win32" else ("Arial", 14)
FONT_ICON_LARGE = ("Segoe UI Symbol", 44)

PIPELINE_SCROLL_HEIGHT = 280
PIPELINE_ROW_HEIGHT = 30
UPLOAD_BOX_W = 700
UPLOAD_BOX_H = 450
LEFT_COL_W = 288
PIPELINE_OUTER_W = 268

ANALYSIS_METHODS = (
    ("interp", "Интерполяция данных", "Интерполяция\nданных"),
    ("denoise", "Подавление шумов", "Подавление\nшумов"),
    ("spectrum", "Расширение амплитудного спектра", "Расширение\nамплитудного\nспектра"),
    ("resolution", "Повышение разрешающей способности", "Повышение\nразрешающей\nспособности"),
)
ANALYSIS_LABELS = {mid: full for mid, full, _ in ANALYSIS_METHODS}

TAB_STATUS_HINTS = {
    "Файл": "Перетащите .sgy / .segy или двойной клик по области.",
    "Главная": "Слева — исходные данные после загрузки; справа — после обработки (в разработке).",
    "Данные": "Укажите диапазон трасс (От / До / Шаг) и нажмите «Прочитать в память».",
    "Анализ": "Для запуска анализа выберите метод из ленты и нажмите 'Обработка'.",
    "Вид": "Тема и масштаб применяются ко всему окну.",
}

STATUS_KEYS_DEFAULT = "Ctrl+1…5 — вкладки  ·  Ctrl+O — файл"
STATUS_KEYS_ANALYSIS = "Ctrl+1...5"
