"""
Anno 117 Layout Tool - Dialogs
Language selector, save/load, settings.
"""
import tkinter as tk
from tkinter import filedialog
import json
import os

from config import (
    BG_MAIN, BG_SECTION, BG_HOVER, FG_MAIN, FG_DIM, FG_GOLD, FG_SEPARATOR,
    BORDER_GOLD, BORDER_COLOR,
    FONT_TITLE, FONT_BOLD_SMALL, FONT_SMALL,
    SUPPORTED_LANGUAGES, SETTINGS_DIR, SETTINGS_FILE,
)


def make_checkbox(parent, text: str, variable: tk.BooleanVar, bg=BG_SECTION, font=None) -> tk.Frame:
    """
    Custom checkbox using Unicode glyphs so the tick colour is always gold,
    regardless of OS theme (Windows draws native checkmarks in black).
    """
    f = tk.Frame(parent, bg=bg, cursor='hand2')
    _font = font or FONT_SMALL
    glyph = tk.Label(f, text='☐', bg=bg, fg=FG_GOLD, font=_font)
    glyph.pack(side=tk.LEFT, padx=(0, 4))
    tk.Label(f, text=text, bg=bg, fg=FG_GOLD, font=_font, cursor='hand2').pack(side=tk.LEFT)

    def _refresh(*_):
        glyph.config(text='☑' if variable.get() else '☐')

    def _toggle(*_):
        variable.set(not variable.get())

    trace_id = variable.trace_add('write', _refresh)

    def _on_destroy(_e):
        # Without this, the trace keeps firing after the widget is destroyed (e.g. when the panel rebuilds and recreates this checkbox), raising TclError on a stale widget reference.
        try:
            variable.trace_remove('write', trace_id)
        except tk.TclError:
            pass

    f.bind('<Destroy>', _on_destroy)
    for w in f.winfo_children() + [f]:
        w.bind('<Button-1>', _toggle)
    _refresh()
    return f


def load_settings() -> dict:
    """Load application settings from disk."""
    defaults = {
        'language': 'english',
        'first_run': True,
        'export_include_info': False,
        'building_color_overrides': {},
        'category_color_overrides': {},
        'light_mode': False,
        'show_45_grid': True,
        'road_show_outline': True,
        'road_show_icon': True,
        'module_show_icon': True,
        'line_mode': False,
        'module_rect_mode': False,
    }
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, encoding='utf-8') as f:
                data = json.load(f)
            defaults.update(data)
        except Exception:
            pass
    return defaults


def save_settings(settings: dict):
    """Persist application settings to disk."""
    os.makedirs(SETTINGS_DIR, exist_ok=True)
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(settings, f, indent=2)


class LanguageDialog(tk.Toplevel):
    """
    First-run language selection dialog.
    Shown once; stores choice in settings.
    """

    def __init__(self, parent, initial_lang='english', **kwargs):
        super().__init__(parent, **kwargs)
        self.title("Anno 117 Layout Tool – Language")
        self.resizable(False, False)
        self.configure(bg=BG_MAIN)
        self.grab_set()
        self.focus_set()

        self.selected_lang = initial_lang
        self._result = None

        self._build_ui()
        self._center()

    def _build_ui(self):
        # Gold border header
        tk.Frame(self, bg=BORDER_GOLD, height=3).pack(fill=tk.X)

        # Title
        tk.Label(self, text="Anno 117 Layout Tool", bg=BG_MAIN, fg=FG_GOLD, font=FONT_TITLE, pady=10).pack()
        tk.Label(self, text="Select your in-game language:", bg=BG_MAIN, fg=FG_MAIN, font=FONT_SMALL, pady=4).pack()
        tk.Frame(self, height=1, bg=FG_SEPARATOR).pack(fill=tk.X, padx=20)

        # Language list
        list_frame = tk.Frame(self, bg=BG_SECTION, bd=1, relief=tk.FLAT, highlightbackground=BORDER_COLOR, highlightthickness=1)
        list_frame.pack(padx=24, pady=12)

        self._var = tk.StringVar(value=self.selected_lang)

        for lang_key, lang_display in SUPPORTED_LANGUAGES:
            rb = tk.Radiobutton(
                list_frame,
                text=lang_display,
                variable=self._var,
                value=lang_key,
                bg=BG_SECTION,
                fg=FG_MAIN,
                selectcolor=BG_MAIN,
                activebackground=BG_HOVER,
                activeforeground=FG_GOLD,
                font=FONT_SMALL,
                anchor='w',
                width=22,
            )
            rb.pack(anchor='w', padx=12, pady=1)

        # Buttons
        btn_frame = tk.Frame(self, bg=BG_MAIN)
        btn_frame.pack(pady=(4, 16))

        ok_btn = tk.Button(
            btn_frame,
            text="Confirm",
            bg=BG_SECTION,
            fg=FG_GOLD,
            activebackground=BG_HOVER,
            activeforeground=FG_GOLD,
            font=FONT_BOLD_SMALL,
            relief=tk.FLAT,
            padx=24, pady=6,
            bd=1,
            highlightbackground=BORDER_GOLD,
            command=self._on_ok
        )
        ok_btn.pack()

        # Bottom gold border
        tk.Frame(self, bg=BORDER_GOLD, height=3).pack(fill=tk.X, side=tk.BOTTOM)

    def _on_ok(self):
        self._result = self._var.get()
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w  = self.winfo_reqwidth()
        h  = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.geometry(f"+{x}+{y}")

    def get_result(self) -> str:
        return self._result or self.selected_lang

    @classmethod
    def ask(cls, parent, initial_lang='english') -> str:
        """Show dialog and return selected language key."""
        dlg = cls(parent, initial_lang=initial_lang)
        parent.wait_window(dlg)
        return dlg.get_result()


class IslandPickerDialog(tk.Toplevel):
    """Scrollable island selector. Returns a cleaned display name mapped back to the raw key."""

    def __init__(self, parent, island_names: list, **kwargs):
        super().__init__(parent, **kwargs)
        self.title("Load Island")
        self.resizable(False, False)
        self.configure(bg=BG_MAIN)
        self.grab_set()
        self.focus_set()

        self._names = island_names   # raw keys from data_manager
        self._result: str | None = None

        self._build_ui()
        self._center()

    # ── name formatting ────────────────────────────────────────────────────────
    @staticmethod
    def _fmt(raw: str) -> str:
        """'l_anno117_colony01_moderate_01' → 'Colony01 Moderate 01'"""
        name = raw
        for prefix in ('l_anno117_', 'l_anno1800_', 'l_'):
            if name.lower().startswith(prefix):
                name = name[len(prefix):]
                break
        return name.replace('_', ' ').title()

    # ── UI ─────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        tk.Frame(self, bg=BORDER_GOLD, height=3).pack(fill=tk.X)

        tk.Label(self, text="Select Island", bg=BG_MAIN, fg=FG_GOLD,
                 font=FONT_TITLE, pady=8).pack()
        tk.Frame(self, height=1, bg=BORDER_COLOR).pack(fill=tk.X, padx=20)

        # Search bar
        search_frame = tk.Frame(self, bg=BG_MAIN)
        search_frame.pack(fill=tk.X, padx=16, pady=(8, 4))
        tk.Label(search_frame, text="Filter:", bg=BG_MAIN, fg=FG_DIM,
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(0, 6))
        self._search_var = tk.StringVar()
        entry = tk.Entry(search_frame, textvariable=self._search_var,
                         bg=BG_SECTION, fg=FG_MAIN, insertbackground=FG_MAIN,
                         relief=tk.FLAT, font=FONT_SMALL, width=28)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        entry.focus_set()
        self._search_var.trace_add('write', lambda *_: self._refresh_list())

        # List + scrollbar
        list_frame = tk.Frame(self, bg=BG_SECTION,
                              highlightbackground=BORDER_COLOR, highlightthickness=1)
        list_frame.pack(padx=16, pady=4)
        sb = tk.Scrollbar(list_frame, orient=tk.VERTICAL)
        self._listbox = tk.Listbox(
            list_frame, yscrollcommand=sb.set,
            bg=BG_SECTION, fg=FG_MAIN, selectbackground=BG_HOVER,
            selectforeground=FG_GOLD, activestyle='none',
            font=FONT_SMALL, width=40, height=18,
            relief=tk.FLAT, highlightthickness=0,
        )
        sb.config(command=self._listbox.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self._listbox.pack(side=tk.LEFT, fill=tk.BOTH)
        self._listbox.bind('<Double-Button-1>', lambda _e: self._on_ok())
        self._listbox.bind('<Return>', lambda _e: self._on_ok())
        self._refresh_list()

        # Buttons
        btn_row = tk.Frame(self, bg=BG_MAIN)
        btn_row.pack(pady=(6, 14))
        tk.Button(btn_row, text="Load", command=self._on_ok,
                  bg=BG_SECTION, fg=FG_GOLD, activebackground=BG_HOVER,
                  activeforeground=FG_GOLD, font=FONT_BOLD_SMALL,
                  relief=tk.FLAT, padx=20, pady=5,
                  highlightbackground=BORDER_GOLD).pack(side=tk.LEFT, padx=6)
        tk.Button(btn_row, text="Cancel", command=self.destroy,
                  bg=BG_SECTION, fg=FG_DIM, activebackground=BG_HOVER,
                  font=FONT_BOLD_SMALL, relief=tk.FLAT, padx=20, pady=5,
                  highlightbackground=BORDER_COLOR).pack(side=tk.LEFT, padx=6)

        tk.Frame(self, bg=BORDER_GOLD, height=3).pack(fill=tk.X, side=tk.BOTTOM)

    def _refresh_list(self):
        q = self._search_var.get().lower()
        self._filtered = [n for n in self._names
                          if not q or q in n.lower() or q in self._fmt(n).lower()]
        self._listbox.delete(0, tk.END)
        for n in self._filtered:
            self._listbox.insert(tk.END, '  ' + self._fmt(n))
        if self._filtered:
            self._listbox.selection_set(0)

    def _on_ok(self):
        sel = self._listbox.curselection()
        if not sel:
            return
        self._result = self._filtered[sel[0]]
        self.destroy()

    def _center(self):
        self.update_idletasks()
        w  = self.winfo_reqwidth()
        h  = self.winfo_reqheight()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def get_result(self) -> str | None:
        return self._result

    @classmethod
    def ask(cls, parent, island_names: list) -> str | None:
        dlg = cls(parent, island_names)
        parent.wait_window(dlg)
        return dlg.get_result()


def ask_save_layout(parent) -> str:
    """Show save file dialog; return path or empty string."""
    path = filedialog.asksaveasfilename(
        parent=parent,
        title="Save Layout",
        defaultextension=".a117l",
        filetypes=[
            ("Anno 117 Layout", "*.a117l"),
            ("JSON", "*.json"),
            ("All files", "*.*"),
        ],
    )
    return path or ''


def ask_load_layout(parent) -> str:
    """Show open file dialog; return path or empty string."""
    path = filedialog.askopenfilename(
        parent=parent,
        title="Load Layout",
        filetypes=[
            ("Anno 117 Layout", "*.a117l"),
            ("JSON", "*.json"),
            ("All files", "*.*"),
        ],
    )
    return path or ''


def ask_export_png(parent, default_include_info: bool = False) -> tuple:
    """
    Show export-options dialog then a save-as dialog.
    Returns (path, include_info) or ('', False) if cancelled.
    """
    # Options dialog
    dlg = tk.Toplevel(parent)
    dlg.title("Export Options")
    dlg.resizable(False, False)
    dlg.configure(bg=BG_SECTION)
    dlg.grab_set()

    tk.Label(dlg, text="PNG Export Options", bg=BG_SECTION, fg=FG_GOLD, font=FONT_BOLD_SMALL).pack(padx=16, pady=(12, 6))
    tk.Frame(dlg, height=1, bg=BORDER_GOLD).pack(fill=tk.X, padx=8)

    include_info_var = tk.BooleanVar(value=default_include_info)
    make_checkbox(dlg, "Include Layout Info panel on the right", include_info_var).pack(anchor='w', padx=16, pady=8)

    result = {'go': False}

    def _ok():
        result['go'] = True
        dlg.destroy()

    def _cancel():
        dlg.destroy()

    btn_row = tk.Frame(dlg, bg=BG_SECTION)
    btn_row.pack(pady=(4, 12))
    tk.Button(btn_row, text="Next →", command=_ok, bg=BG_HOVER, fg=FG_GOLD, relief=tk.FLAT, padx=10).pack(side=tk.LEFT, padx=4)
    tk.Button(btn_row, text="Cancel", command=_cancel, bg=BG_SECTION, fg=FG_DIM, relief=tk.FLAT, padx=10).pack(side=tk.LEFT, padx=4)

    dlg.bind('<Return>', lambda _: _ok())
    dlg.bind('<Escape>', lambda _: _cancel())
    parent.wait_window(dlg)

    if not result['go']:
        return '', False

    path = filedialog.asksaveasfilename(
        parent=parent,
        title="Export Layout as PNG",
        defaultextension=".png",
        filetypes=[("PNG Image", "*.png"), ("All files", "*.*")],
    )
    return (path or ''), include_info_var.get()
