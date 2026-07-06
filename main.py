"""
Anno 117 Layout Tool
Standalone tool for creating build layouts for Anno 117 Pax Romana.
"""
import sys
import os
import platform
import tkinter as tk
from tkinter import messagebox
import json
import _version

IS_WINDOWS = platform.system() == "Windows"

# ── resource path helper (must exist before any relative import) ────────────
def resource_path(relative_path: str) -> str:
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

# ── Register fonts before Tk window opens ──────────────────────────────────
FONT_FILES = [
    "data/fonts/PlayfairDisplaySC-Regular.ttf",
    "data/fonts/Marcellus-Regular.ttf",
]

def _load_custom_font(font_path: str) -> bool:
    if not os.path.exists(font_path):
        print(f"Font not found: {font_path}")
        return False
    if not IS_WINDOWS:
        return True
    try:
        import ctypes
        FR_PRIVATE = 0x10
        res = ctypes.windll.gdi32.AddFontResourceExW(font_path, FR_PRIVATE, 0)
        return res > 0
    except Exception as e:
        print(f"Font load error: {e}")
        return False

for _f in FONT_FILES:
    _load_custom_font(resource_path(_f))

# ── Now import the rest of the application ─────────────────────────────────
from config import (
    BG_MAIN, BG_SECTION, FG_MAIN, FG_DIM, FG_GOLD, FG_SEPARATOR, BORDER_GOLD, BORDER_COLOR, FONT_TITLE, FONT_HEADER, FONT_BOLD_SMALL, FONT_SMALL, FONT_XSMALL, SUPPORTED_LANGUAGES)
from data_manager import get_data_manager
from canvas_widget import CanvasWidget
from build_menu import BuildMenu
from panels import LayoutInfoPanel, BuildingInfoPanel
from dialogs import (LanguageDialog, load_settings, save_settings, ask_save_layout, ask_load_layout)

APP_TITLE = ("Anno 117 Layout Tool" + f' v{_version.__VERSION__}')
WINDOW_MIN_W = 1440
WINDOW_MIN_H = 900
INFO_PANEL_W = 220
BUILD_MENU_H = 250
BLDG_INFO_W = 220
BLDG_INFO_H = 450


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self._set_window_icon()
        self.title(APP_TITLE)
        self.minsize(WINDOW_MIN_W, WINDOW_MIN_H)
        self.configure(bg=BG_MAIN)

        # Load / init settings
        self.settings = load_settings()
        self.language: str = self.settings.get('language', 'english')
        self.export_include_info = tk.BooleanVar(value=self.settings.get('export_include_info', False))

        # Pre-load data
        self.dm = get_data_manager()
        self.dm.building_color_overrides = {
            int(g): c for g, c in self.settings.get('building_color_overrides', {}).items()
        }
        self.dm.category_color_overrides = dict(self.settings.get('category_color_overrides', {}))

        # Hide the main window while the language dialog (if needed) and UI build complete
        self.withdraw()

        # First-run language selection - shown while main window is still hidden
        if self.settings.get('first_run', True):
            self._show_language_dialog_first_run()

        self.dirty = False

        self._build_ui()
        self._build_menu_bar()
        self._setup_bindings()
        self.protocol("WM_DELETE_WINDOW", self._on_exit)

        # Set initial window size, then reveal the fully-built window
        self.geometry("1280x800")
        self.deiconify()
        self.after(100, self._on_startup)

    def _set_window_icon(self):
        if IS_WINDOWS:
            ico = resource_path('data/ui/app_icon.ico')
            if os.path.exists(ico):
                try:
                    self.iconbitmap(ico)
                    return
                except Exception:
                    pass
        png = resource_path('data/ui/app_icon.png')
        if os.path.exists(png):
            try:
                self._icon_img = tk.PhotoImage(file=png)
                self.iconphoto(True, self._icon_img)
            except Exception:
                pass

    def mark_dirty(self):
        self.dirty = True

    def _confirm_discard_unsaved(self) -> bool:
        """Return True if it's OK to proceed (no unsaved changes, or user confirmed discard)."""
        if not self.dirty:
            return True
        return messagebox.askyesno(
            "Unsaved Changes",
            "The layout has unsaved changes. Discard them?")

    def _on_exit(self):
        if self._confirm_discard_unsaved():
            self.quit()

    # ------------------------------------------------------------------ #
    #  First run
    # ------------------------------------------------------------------ #
    def _show_language_dialog_first_run(self):
        lang = LanguageDialog.ask(self, self.language)
        self.language = lang
        self.settings['language'] = lang
        self.settings['first_run'] = False
        save_settings(self.settings)

    # ------------------------------------------------------------------ #
    #  UI layout
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        # Top-level layout:
        #  ┌──────────────────────────────┬──────────┐
        #  │  Canvas area                 │ Info     │
        #  │  ┌────────────────────────┐  │ Panel    │
        #  │  │  Building info overlay │  │          │
        #  │  └────────────────────────┘  │          │
        #  ├──────────────────────────────┤          │
        #  │  Build Menu                  │          │
        #  └──────────────────────────────┴──────────┘

        # Main horizontal pane
        self._main_frame = tk.Frame(self, bg=BG_MAIN)
        self._main_frame.pack(fill=tk.BOTH, expand=True)

        # ── Right info panel ─────────────────────────────────────────────
        self.info_panel = LayoutInfoPanel(self._main_frame, self, width=INFO_PANEL_W)
        self.info_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(1, 0), pady=0)

        # Gold separator line between canvas and info panel
        tk.Frame(self._main_frame, width=1, bg=BORDER_GOLD).pack(side=tk.RIGHT, fill=tk.Y)

        # ── Canvas + build menu vertical stack ───────────────────────────
        self._canvas_area = tk.Frame(self._main_frame, bg=BG_MAIN)
        self._canvas_area.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Build menu (bottom)
        self.build_menu = BuildMenu(self._canvas_area, self, height=BUILD_MENU_H)
        self.build_menu.pack(side=tk.BOTTOM, fill=tk.X)

        # Gold separator above build menu
        tk.Frame(self._canvas_area, height=1, bg=BORDER_GOLD).pack(side=tk.BOTTOM, fill=tk.X)

        # Canvas widget
        self.canvas_widget = CanvasWidget(self._canvas_area, self)
        self.canvas_widget.pack(fill=tk.BOTH, expand=True)

        # ── Building info overlay (top-right of canvas) ───────────────────
        self.building_info_panel = BuildingInfoPanel(self.canvas_widget, self, width=BLDG_INFO_W)
        self.building_info_panel.place(relx=1.0, rely=0.0, anchor='ne', x=-4, y=4, width=BLDG_INFO_W, height=BLDG_INFO_H)

    # ------------------------------------------------------------------ #
    #  Menu bar
    # ------------------------------------------------------------------ #
    def _build_menu_bar(self):
        menubar = tk.Menu(self, bg=BG_SECTION, fg=FG_MAIN, activebackground=BG_MAIN, activeforeground=FG_GOLD, relief=tk.FLAT)

        # File menu
        file_menu = tk.Menu(menubar, tearoff=0, bg=BG_SECTION, fg=FG_MAIN, activebackground=BG_MAIN, activeforeground=FG_GOLD)
        file_menu.add_command(label="New Layout", command=self._new_layout, accelerator="Ctrl+N")
        file_menu.add_command(label="Open Layout…", command=self._open_layout, accelerator="Ctrl+O")
        file_menu.add_command(label="Save Layout", command=self._save_layout, accelerator="Ctrl+S")
        file_menu.add_command(label="Save Layout As…", command=self._save_layout_as)
        file_menu.add_separator()
        file_menu.add_command(label="Export as PNG…", command=self._export_png)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_exit)
        menubar.add_cascade(label="File", menu=file_menu)

        # Edit menu
        edit_menu = tk.Menu(menubar, tearoff=0, bg=BG_SECTION, fg=FG_MAIN, activebackground=BG_MAIN, activeforeground=FG_GOLD)
        edit_menu.add_command(label="Undo", command=self.canvas_widget.undo, accelerator="Ctrl+Z")
        edit_menu.add_command(label="Redo", command=self.canvas_widget.redo, accelerator="Ctrl+Y")
        edit_menu.add_separator()
        edit_menu.add_command(label="Select All", command=lambda: self.canvas_widget._on_select_all(None), accelerator="Ctrl+A")
        edit_menu.add_command(label="Delete Selected", command=self.canvas_widget.delete_selected, accelerator="Delete")
        edit_menu.add_command(label="Clear All", command=self.canvas_widget.clear_all)
        menubar.add_cascade(label="Edit", menu=edit_menu)

        # View menu
        view_menu = tk.Menu(menubar, tearoff=0, bg=BG_SECTION, fg=FG_MAIN, activebackground=BG_MAIN, activeforeground=FG_GOLD, selectcolor=FG_GOLD)
        view_menu.add_command(
            label="Fit Layout to View",
            command=self.canvas_widget.fit_view,
            accelerator="Home")
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="Light Mode",
            variable=self.canvas_widget.light_mode,
            command=self.canvas_widget._redraw)
        view_menu.add_checkbutton(
            label="Show 45° Grid",
            variable=self.canvas_widget.show_45_grid,
            command=self.canvas_widget._redraw)
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="Roads/Channels: Show Outline",
            variable=self.canvas_widget.road_show_outline,
            command=self.canvas_widget._redraw)
        view_menu.add_checkbutton(
            label="Roads/Channels: Show Icons",
            variable=self.canvas_widget.road_show_icon,
            command=self.canvas_widget._redraw)
        view_menu.add_checkbutton(
            label="Modules: Show Icons",
            variable=self.canvas_widget.module_show_icon,
            command=self.canvas_widget._redraw)
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="Roads/Channels: Straight Line Tool",
            variable=self.canvas_widget.line_mode)
        view_menu.add_checkbutton(
            label="Modules/Fields: Rectangle Fill Tool",
            variable=self.canvas_widget.module_rect_mode)
        view_menu.add_separator()
        view_menu.add_checkbutton(
            label="PNG Export: Include Info Panel",
            variable=self.export_include_info,
            command=self._save_export_info_setting)
        menubar.add_cascade(label="View", menu=view_menu)

        # Settings menu
        settings_menu = tk.Menu(menubar, tearoff=0, bg=BG_SECTION, fg=FG_MAIN, activebackground=BG_MAIN, activeforeground=FG_GOLD)
        settings_menu.add_command(label="Change Language…", command=self._change_language)
        settings_menu.add_separator()
        settings_menu.add_command(label="Reset Building Colours…", command=self._reset_building_colors)
        menubar.add_cascade(label="Settings", menu=settings_menu)

        self.config(menu=menubar)
        self._current_path: str = ''

    # ------------------------------------------------------------------ #
    #  Key bindings
    # ------------------------------------------------------------------ #
    def _setup_bindings(self):
        self.bind('<Control-n>', lambda e: self._new_layout())
        self.bind('<Control-o>', lambda e: self._open_layout())
        self.bind('<Control-s>', lambda e: self._save_layout())
        self.bind('<Escape>', lambda e: self.canvas_widget.cancel_build_mode())
        self.bind('<Home>', lambda _: self.canvas_widget.fit_view())

    # ------------------------------------------------------------------ #
    #  File operations
    # ------------------------------------------------------------------ #
    def _new_layout(self):
        if not messagebox.askyesno("New Layout", "Create a new layout? Unsaved changes will be lost."):
            return
        self.canvas_widget.clear_all()
        self._current_path = ''
        self.dirty = False
        self.title(APP_TITLE)

    def _open_layout(self):
        path = ask_load_layout(self)
        if not path:
            return
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
            self.canvas_widget.load_layout_dict(data)
            self._current_path = path
            self.dirty = False
            self.title(f"{APP_TITLE} – {os.path.basename(path)}")
        except Exception as e:
            messagebox.showerror("Load Error", f"Failed to load layout:\n{e}")

    def _save_layout(self):
        if not self._current_path:
            self._save_layout_as()
            return
        self._do_save(self._current_path)

    def _save_layout_as(self):
        path = ask_save_layout(self)
        if path:
            self._do_save(path)
            self._current_path = path
            self.title(f"{APP_TITLE} – {os.path.basename(path)}")

    def _do_save(self, path: str):
        try:
            data = self.canvas_widget.get_layout_dict()
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)
            self.dirty = False
        except Exception as e:
            messagebox.showerror("Save Error", f"Failed to save layout:\n{e}")

    def _save_export_info_setting(self):
        self.settings['export_include_info'] = self.export_include_info.get()
        save_settings(self.settings)

    def _export_png(self):
        from tkinter import filedialog
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Export Layout as PNG",
            defaultextension=".png",
            filetypes=[("PNG Image", "*.png"), ("All files", "*.*")],
        )
        if path:
            include_info = self.export_include_info.get()
            stats = self.canvas_widget.get_layout_stats() if include_info else None
            self.canvas_widget.export_png(path, include_info_stats=stats)

    # ------------------------------------------------------------------ #
    #  Language
    # ------------------------------------------------------------------ #
    def _change_language(self):
        lang = LanguageDialog.ask(self, self.language)
        if lang != self.language:
            self.language = lang
            self.settings['language'] = lang
            save_settings(self.settings)
            self.build_menu.update_language()
            # Refresh info panel if a building is selected
            self.canvas_widget._notify_selection()

    def _reset_building_colors(self):
        if not (self.dm.building_color_overrides or self.dm.category_color_overrides):
            messagebox.showinfo("Reset Building Colours",
                                "No custom building colours are set.")
            return
        if not messagebox.askyesno(
                "Reset Building Colours",
                "Reset all custom building colours to their defaults?"):
            return
        self.dm.building_color_overrides.clear()
        self.dm.category_color_overrides.clear()
        self.settings['building_color_overrides'] = {}
        self.settings['category_color_overrides'] = {}
        save_settings(self.settings)
        self.canvas_widget._redraw()
        self.canvas_widget._notify_selection()

    # ------------------------------------------------------------------ #
    #  Startup
    # ------------------------------------------------------------------ #
    def _on_startup(self):
        self.canvas_widget._center_view()
        self.canvas_widget._redraw()
        # Update title bar
        self.title(APP_TITLE)


# ── Entry point ─────────────────────────────────────────────────────────────
def main():
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
