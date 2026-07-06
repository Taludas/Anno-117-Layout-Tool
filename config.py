"""
Anno 117 Layout Tool - Configuration & Constants
"""
import os
import sys
import platform

IS_WINDOWS = platform.system() == "Windows"

# --- Visual Styles & Fonts ---
BG_MAIN = "#0b192c"
BG_SECTION = "#162a45"
BG_HOVER = "#253b59"
BG_SELECTED = "#1e3a5f"
BG_ACTIVE = "#2a4d70"
FG_MAIN = "#ffffff"
FG_DIM = "#aaaaaa"
FG_GOLD = "#f1c40f"
FG_SEPARATOR = "#2a3b4c"
BORDER_COLOR = "#2a3b4c"
BORDER_GOLD = "#c9a227"
ACCENT_RED = "#c0392b"

FONT_TITLE  = ("Playfair Display SC", 16, "bold")
FONT_DESC   = ("Marcellus", 11, "italic")
FONT_HEADER = ("Playfair Display SC", 13, "bold")
FONT_BODY   = ("Marcellus", 13)
FONT_UI_BOLD = ("Marcellus", 14, "bold")
FONT_TAB_BOLD = ("Marcellus", 13, "bold")
FONT_BOLD_SMALL = ("Marcellus", 11, "bold")
FONT_SMALL  = ("Marcellus", 11)
FONT_XSMALL = ("Marcellus", 10)

FONT_FILES = [
    "data/fonts/PlayfairDisplaySC-Regular.ttf",
    "data/fonts/Marcellus-Regular.ttf"
]

# --- Grid ---
DEFAULT_TILE_SIZE = 19          # pixels per grid tile at zoom=1
ZOOM_FACTOR = 1.15
MAX_TILE_SIZE = 200
MIN_TILE_SIZE = DEFAULT_TILE_SIZE / (ZOOM_FACTOR ** 8)  # exactly 8 scroll-out steps from default
GRID_COLOR_90  = "#2d5272"      # normal 90° grid lines
GRID_COLOR_45  = "#1a3a5c"      # 45° diagonal sub-grid (dimmer than 90°)

# --- Building category colour mapping ---
CATEGORY_COLORS = {
    "Road":                  "#A9A9A9",
    "Ornamental Road":       "#A9A9A9",
    "Infrastructure Building": "#8FBC8F",
    "Public Service":        "#FFDAB9",
    "Amenity":               "#ff0000",
    "Roman Residence":       "#A1EAEA",
    "Celtic Residence":      "#A1EAEA",
    "Arable Farm":           "#FFA500",
    "Cultivation Area":      "#FFA500",
    "Livestock Farm":        "#ADFF2F",
    "Livestock Area":        "#ADFF2F",
    "Plantation":            "#FFA500",
    "Forest Camp":           "#3CB371",
    "Gatherer":              "#3CB371",
    "Fishery":               "#2060a0",
    "Hunting Cabin":         "#3CB371",
    "Extractor":             "#6A5ACD",
    "Pit":                   "#704a30",
    "Quarry":                "#704a30",
    "Mine":                  "#704a30",
    "Refinery":              "#6A5ACD",
    "Smelter":               "#6A5ACD",
    "Kitchen":               "#6A5ACD",
    "Victualler":            "#6A5ACD",
    "Clothier":              "#6A5ACD",
    "Upholsterer":           "#6A5ACD",
    "Artisanal Studio":      "#6A5ACD",
    "Workshop":              "#6A5ACD",
    "Armoury":               "#6A5ACD",
    "Shrine":                "#c0a030",
    "Marvel":                "#FFDAB9",
    "Marvellous Mosaics":    "#c0b828",
    "Monument— Marvel":      "#FFDAB9",  # raw game data uses an em dash here, not a space
    "City Watch":            "#ff0000",
    "Recruitment Building":  "#8B4513",
    "Defensive Building":    "#8B4513",
    "Harbour Building":      "#2060a0",
    "Specialist Building":   "#FF1493",
    "Building Module":       "#8FBC8F",
    "Ground Patterns":       "#404040",
    "Ornament":              "#706040",
}
CATEGORY_COLOR_DEFAULT = "#456080"

# Region display names
REGION_DISPLAY = {
    "Roman": "Latium",
    "Celtic": "Albion",
}

# Tier order
TIER_ORDER = {
    "Roman": ["Liberti", "Plebeians", "Equites", "Patricians"],
    "Celtic": ["Waders", "Smiths", "Aldermen", "Mercators", "Nobles"],
}

# App settings
_APP_FOLDER = "Anno 117 Layout Tool"
if IS_WINDOWS:
    _base = os.environ.get("APPDATA") or os.path.expanduser("~")
else:
    _base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
SETTINGS_DIR  = os.path.join(_base, _APP_FOLDER)
SETTINGS_FILE = os.path.join(SETTINGS_DIR, "settings.json")

SUPPORTED_LANGUAGES = [
    ("english",            "English"),
    ("german",             "Deutsch"),
    ("french",             "Français"),
    ("spanish",            "Español"),
    ("italian",            "Italiano"),
    ("polish",             "Polski"),
    ("russian",            "Русский"),
    ("brazilian",          "Português (BR)"),
    ("japanese",           "日本語"),
    ("korean",             "한국어"),
    ("simplified_chinese", "简体中文"),
    ("traditional_chinese","繁體中文"),
]

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)


def load_custom_font(font_path):
    """Register a font file with the Windows system for the current process."""
    if not os.path.exists(font_path):
        print(f"Font not found: {font_path}")
        return False
    if not IS_WINDOWS:
        return True  # Linux: font files bundled; Tkinter resolves via fontconfig
    try:
        import ctypes
        FR_PRIVATE = 0x10
        res = ctypes.windll.gdi32.AddFontResourceExW(font_path, FR_PRIVATE, 0)
        return res > 0
    except Exception as e:
        print(f"Font load error: {e}")
        return False


def get_category_color(category_english: str) -> str:
    return CATEGORY_COLORS.get(category_english, CATEGORY_COLOR_DEFAULT)

