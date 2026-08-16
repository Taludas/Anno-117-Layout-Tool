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
from dialogs import (LanguageDialog, load_settings, save_settings,
                     ask_save_layout, ask_load_layout, IslandPickerDialog)
from tool_manager import ensure_tools
from savegame_parser import parse_savegame, ParseError as SavegameParseError

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
        file_menu.add_command(label="Import Savegame…", command=self._import_savegame, accelerator="Ctrl+G")
        file_menu.add_command(label="Switch Savegame Island…", command=self._switch_savegame_island, state='disabled')
        file_menu.add_separator()
        file_menu.add_command(label="Load Island…", command=self._load_island, accelerator="Ctrl+I")
        file_menu.add_command(label="Clear Island", command=self._clear_island)
        file_menu.add_separator()
        file_menu.add_command(label="Export as PNG…", command=self._export_png)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self._on_exit)
        self._file_menu = file_menu
        menubar.add_cascade(label="File", menu=file_menu)

        # Edit menu
        edit_menu = tk.Menu(menubar, tearoff=0, bg=BG_SECTION, fg=FG_MAIN, activebackground=BG_MAIN, activeforeground=FG_GOLD)
        edit_menu.add_command(label="Undo", command=self.canvas_widget.undo, accelerator="Ctrl+Z")
        edit_menu.add_command(label="Redo", command=self.canvas_widget.redo, accelerator="Ctrl+Y")
        edit_menu.add_separator()
        edit_menu.add_command(label="Select All", command=lambda: self.canvas_widget._on_select_all(None), accelerator="Ctrl+A")
        edit_menu.add_command(label="Delete Selected", command=self.canvas_widget.delete_selected, accelerator="Delete")
        edit_menu.add_command(label="Clear All", command=self.canvas_widget.clear_all)
        edit_menu.add_separator()
        edit_menu.add_command(label="Rotate Layout 90° CW",  command=lambda: self.canvas_widget.rotate_layout(90),  accelerator="PgDn")
        edit_menu.add_command(label="Rotate Layout 90° CCW", command=lambda: self.canvas_widget.rotate_layout(-90), accelerator="PgUp")
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
        self.bind('<Control-g>', lambda e: self._import_savegame())
        self.bind('<Control-i>', lambda e: self._load_island())
        self.bind('<Escape>', lambda e: self.canvas_widget.cancel_build_mode())
        self.bind('<Home>', lambda _: self.canvas_widget.fit_view())
        self.bind('<Next>',  lambda _: self.canvas_widget.rotate_layout(90))
        self.bind('<Prior>', lambda _: self.canvas_widget.rotate_layout(-90))

    # ------------------------------------------------------------------ #
    #  File operations
    # ------------------------------------------------------------------ #
    def _new_layout(self):
        if not messagebox.askyesno("New Layout", "Create a new layout? Unsaved changes will be lost."):
            return
        self.canvas_widget.clear_all()
        self.canvas_widget.clear_island()
        self.canvas_widget._redraw()   # remove island background canvas items
        self._current_path = ''
        self.dirty = False
        self.title(APP_TITLE)

    def _import_savegame(self):
        tools = ensure_tools(self, self.settings)
        if tools is None:
            return
        save_settings(self.settings)

        # Locate the default savegame directory
        user = os.environ.get('USERPROFILE', os.path.expanduser('~'))
        docs = os.path.join(user, 'Documents', 'Anno 117 - Pax Romana', 'accounts')
        init_dir = user
        if os.path.isdir(docs):
            # Each account has its own sub-folder; pick the most-recently-modified one
            try:
                sub = max(
                    (e for e in os.scandir(docs) if e.is_dir()),
                    key=lambda e: e.stat().st_mtime,
                    default=None,
                )
                init_dir = sub.path if sub else docs
            except OSError:
                init_dir = docs

        from tkinter.filedialog import askopenfilename
        path = askopenfilename(
            parent=self,
            title="Open Anno 117 Savegame",
            filetypes=[("Anno 117 Savegame", "*.a8s"), ("All files", "*.*")],
            initialdir=init_dir,
        )
        if not path:
            return

        _SavegameImportDialog(
            self, path, tools, self.canvas_widget.dm,
            on_import=self._load_savegame_island,
            on_parsed=lambda isl: self._on_savegame_parsed(isl, path),
        )

    def _on_savegame_parsed(self, islands: list, path: str):
        """Cache parsed islands and enable the Switch Savegame Island menu item."""
        self._savegame_islands = islands
        self._savegame_file = path
        state = 'normal' if islands else 'disabled'
        self._file_menu.entryconfig("Switch Savegame Island…", state=state)

    def _switch_savegame_island(self):
        islands = getattr(self, '_savegame_islands', None)
        if not islands:
            return
        _IslandSwitcherDialog(
            self, islands, getattr(self, '_savegame_file', ''),
            on_import=self._load_savegame_island,
        )

    def _load_savegame_island(self, island) -> None:
        """Load an IslandImport into the canvas: terrain + placed buildings."""
        import tkinter.ttk as ttk
        from canvas_widget import PlacedBuilding

        # ── Loading overlay ──────────────────────────────────────────────
        dlg = tk.Toplevel(self)
        dlg.title("Loading Island")
        dlg.resizable(False, False)
        dlg.configure(bg=BG_MAIN)
        dlg.transient(self)
        dlg.protocol("WM_DELETE_WINDOW", lambda: None)  # not closeable

        tk.Label(dlg, text="Loading island to canvas…", bg=BG_MAIN, fg=FG_GOLD,
                 font=('Segoe UI', 12, 'bold')).pack(padx=24, pady=(16, 4))
        _phase_lbl = tk.Label(dlg, text="Preparing…", bg=BG_MAIN, fg=FG_MAIN,
                              font=('Segoe UI', 9), width=44, anchor='w')
        _phase_lbl.pack(padx=24, pady=(0, 4))
        _bar = ttk.Progressbar(dlg, mode='indeterminate', length=340)
        _bar.pack(padx=24, pady=(0, 16))
        _bar.start(12)

        dlg.update_idletasks()
        pw = self.winfo_rootx() + self.winfo_width() // 2
        ph = self.winfo_rooty() + self.winfo_height() // 2
        dlg.geometry(f'+{pw - dlg.winfo_reqwidth() // 2}+{ph - dlg.winfo_reqheight() // 2}')
        dlg.update()

        def _phase(msg: str):
            _phase_lbl.config(text=msg)
            dlg.update()

        try:
            # Phase 1: terrain
            _phase("Loading island terrain…")
            names = self.canvas_widget.dm.get_island_names()
            if island.island_key in names:
                self.canvas_widget.load_island(island.island_key)
            else:
                self.canvas_widget.placed_buildings.clear()
                self.canvas_widget._rebuild_collision()

            # Phase 2: place buildings.
            # Non-nibble buildings first so the pos_to_farm lookup is fully
            # populated before nibble tiles are placed.
            dm = self.canvas_widget.dm
            all_buildings = [b for b in island.buildings
                             if not b.is_blueprint
                             and (b.nibble or dm.get_building(b.guid) is not None)]
            non_nibble = [b for b in all_buildings if not b.nibble]
            nibble_blds = [b for b in all_buildings if b.nibble]

            total = len(all_buildings)
            _phase(f"Placing {total} buildings…")

            # Pass 1: regular buildings — track farm positions for parent linking.
            # pos_to_farm: rounded (col, row) → PlacedBuilding for farm-type buildings.
            pos_to_farm: dict = {}
            for i, b in enumerate(non_nibble):
                bd = dm.get_building(b.guid)
                gx, gy = b.col, b.row
                if b.direction % 360 not in (0, 90, 180, 270) and bd is not None:
                    gx, gy = self.canvas_widget.snap_to_grid(gx, gy, b.direction, bd)
                pb = PlacedBuilding(b.guid, gx, gy, b.direction)
                self.canvas_widget.placed_buildings.append(pb)
                if bd and (bd.module_guid or bd.additional_module_guid):
                    pos_to_farm[(round(pb.grid_x), round(pb.grid_y))] = pb
                if i % 250 == 0 and i > 0:
                    _phase_lbl.config(text=f"Placing buildings… {i} / {total}")
                    dlg.update()

            # Pass 2: nibble tiles — assign parent_id from the pre-computed
            # parent_col/parent_row stored by the parser (polygon-entry-level match).
            # Each polygon entry belongs to ONE farm; boundary cells between two
            # farms get two separate nibble tiles with different parent_ids so each
            # portion is coloured in its farm's colour independently.
            if nibble_blds:
                _phase(f"Placing {len(nibble_blds)} farm field tiles…")
            for b in nibble_blds:
                parent_id = None
                if b.parent_col is not None:
                    key = (round(b.parent_col), round(b.parent_row))
                    farm_pb = pos_to_farm.get(key)
                    if farm_pb:
                        parent_id = farm_pb.instance_id
                pb = PlacedBuilding(b.guid, b.col, b.row, b.direction,
                                    nibble=b.nibble, parent_id=parent_id)
                self.canvas_widget.placed_buildings.append(pb)

            # Phase 3: collision map (fast — nibble tiles are skipped)
            _phase("Rebuilding collision map…")
            self.canvas_widget._rebuild_collision()
            self.canvas_widget._notify_layout_change()

            # Phase 4: render — keep the dialog visible so there is no blank gap
            # between the overlay closing and the buildings appearing on canvas.
            _phase("Rendering canvas…")
            self.canvas_widget._redraw()
            self.dirty = True

            dlg.destroy()
            dlg = None
        finally:
            if dlg is not None:
                dlg.destroy()

    def _load_island(self):
        names = self.canvas_widget.dm.get_island_names()
        if not names:
            messagebox.showinfo("Load Island", "No island data found.\nRun extract_islands.py first.")
            return
        name = IslandPickerDialog.ask(self, names)
        if name:
            self.canvas_widget.load_island(name)
            self.dirty = True

    def _clear_island(self):
        self.canvas_widget.clear_island()
        self.canvas_widget._redraw()
        self.dirty = True

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


# ── Island switcher dialog (reopens picker for already-parsed savegame) ──────

class _IslandSwitcherDialog(tk.Toplevel):
    """Lightweight island picker for a savegame that was already parsed."""

    def __init__(self, parent: tk.Misc, islands: list, a8s_path: str, on_import=None):
        super().__init__(parent)
        self._parent    = parent
        self._islands   = islands
        self._on_import = on_import

        self.title("Switch Savegame Island")
        self.resizable(False, False)
        self.configure(bg=BG_MAIN)
        self.grab_set()
        self.transient(parent)

        self._build_ui(a8s_path)
        self.after(50, self._centre)

    def _build_ui(self, a8s_path: str):
        pad = dict(padx=16, pady=6)

        tk.Label(self, text="Switch Savegame Island", bg=BG_MAIN, fg=FG_GOLD,
                 font=('Segoe UI', 12, 'bold')).pack(anchor='w', **pad)
        tk.Label(self, text=os.path.basename(a8s_path), bg=BG_MAIN, fg=FG_DIM,
                 font=('Segoe UI', 9)).pack(anchor='w', padx=16, pady=(0, 8))

        frame = tk.Frame(self, bg=BG_SECTION)
        frame.pack(fill='x', padx=16, pady=(0, 4))
        sb = tk.Scrollbar(frame, orient='vertical')
        self._lb = tk.Listbox(
            frame,
            bg=BG_SECTION, fg=FG_MAIN,
            selectbackground=BG_MAIN, selectforeground=FG_GOLD,
            font=('Segoe UI', 9), relief='flat',
            height=min(len(self._islands), 8), width=56,
            activestyle='none',
            yscrollcommand=sb.set,
        )
        sb.config(command=self._lb.yview)
        self._lb.pack(side='left', fill='both', padx=(8, 0), pady=8)
        sb.pack(side='right', fill='y', pady=8)

        for isl in self._islands:
            bd_count = len(isl.buildings)
            bp_count = sum(1 for b in isl.buildings if b.is_blueprint)
            note = f"  ({bp_count} bp)" if bp_count else ""
            self._lb.insert('end', f"{isl.island_key}  –  {bd_count} buildings{note}")
        self._lb.selection_set(0)

        btn_frame = tk.Frame(self, bg=BG_MAIN)
        btn_frame.pack(fill='x', padx=16, pady=(4, 14))
        tk.Button(
            btn_frame, text='Load to Canvas',
            command=self._do_load,
            bg=BG_SECTION, fg=FG_GOLD, relief='flat',
            font=('Segoe UI', 10, 'bold'), padx=10, pady=4,
        ).pack(side='left')
        tk.Button(
            btn_frame, text='Close',
            command=self.destroy,
            bg=BG_SECTION, fg=FG_GOLD, relief='flat',
            font=('Segoe UI', 10, 'bold'), padx=10, pady=4,
        ).pack(side='right')

    def _centre(self):
        self.update_idletasks()
        pw = self._parent.winfo_rootx() + self._parent.winfo_width() // 2
        ph = self._parent.winfo_rooty() + self._parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f'+{pw - w // 2}+{ph - h // 2}')

    def _do_load(self):
        if not self._islands or self._on_import is None:
            return
        sel = self._lb.curselection()
        idx = sel[0] if sel else 0
        island = self._islands[idx]
        self.destroy()
        self._on_import(island)


# ── Savegame import dialog ───────────────────────────────────────────────────

class _SavegameImportDialog(tk.Toplevel):
    """
    Progress + result dialog for savegame import.
    Runs parse_savegame() on a background thread, then shows a summary.
    """

    def __init__(self, parent: tk.Misc, a8s_path: str,
                 tool_paths: dict, data_manager, on_import=None, on_parsed=None):
        super().__init__(parent)
        self._parent     = parent
        self._a8s_path   = a8s_path
        self._tool_paths = tool_paths
        self._dm         = data_manager
        self._islands    = []
        self._on_import  = on_import   # callable(island: IslandImport)
        self._on_parsed  = on_parsed   # callable(islands: list)

        self.title("Import Savegame")
        self.resizable(False, False)
        self.configure(bg=BG_MAIN)
        self.grab_set()
        self.transient(parent)

        self._build_ui()
        self.after(100, self._centre)
        self.after(200, self._start)

    def _build_ui(self):
        import tkinter.ttk as ttk
        pad = dict(padx=16, pady=6)

        tk.Label(self, text="Import Savegame", bg=BG_MAIN, fg=FG_GOLD,
                 font=('Segoe UI', 12, 'bold')).pack(anchor='w', **pad)

        fname = os.path.basename(self._a8s_path)
        tk.Label(self, text=fname, bg=BG_MAIN, fg=FG_DIM,
                 font=('Segoe UI', 9)).pack(anchor='w', padx=16, pady=(0, 8))

        self._status = tk.Label(self, text="Starting…", bg=BG_MAIN, fg=FG_MAIN,
                                font=('Segoe UI', 10), justify='left')
        self._status.pack(anchor='w', **pad)

        self._bar = ttk.Progressbar(self, mode='indeterminate', length=420)
        self._bar.pack(fill='x', padx=16, pady=(0, 8))
        self._bar.start(12)

        # Result area (hidden until parsing finishes)
        self._result_frame = tk.Frame(self, bg=BG_SECTION)
        sb = tk.Scrollbar(self._result_frame, orient='vertical')
        self._island_lb = tk.Listbox(
            self._result_frame,
            bg=BG_SECTION, fg=FG_MAIN,
            selectbackground=BG_MAIN, selectforeground=FG_GOLD,
            font=('Segoe UI', 9), relief='flat',
            height=6, width=56,
            activestyle='none',
            yscrollcommand=sb.set, state='disabled',
        )
        sb.config(command=self._island_lb.yview)
        self._island_lb.pack(side='left', fill='both', padx=(8, 0), pady=8)
        sb.pack(side='right', fill='y', pady=8)

        btn_frame = tk.Frame(self, bg=BG_MAIN)
        btn_frame.pack(fill='x', padx=16, pady=(4, 14))

        self._import_btn = tk.Button(
            btn_frame, text='Import to Canvas',
            command=self._do_import,
            bg=BG_SECTION, fg=FG_GOLD, relief='flat',
            font=('Segoe UI', 10, 'bold'), padx=10, pady=4,
            state='disabled',
        )
        self._import_btn.pack(side='left')

        self._close_btn = tk.Button(
            btn_frame, text='Close',
            command=self.destroy,
            bg=BG_SECTION, fg=FG_GOLD, relief='flat',
            font=('Segoe UI', 10, 'bold'), padx=10, pady=4,
            state='disabled',
        )
        self._close_btn.pack(side='right')

    def _centre(self):
        self.update_idletasks()
        pw = self._parent.winfo_rootx() + self._parent.winfo_width() // 2
        ph = self._parent.winfo_rooty() + self._parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f'+{pw - w // 2}+{ph - h // 2}')

    def _start(self):
        import threading
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self):
        from pathlib import Path
        try:
            islands = parse_savegame(
                Path(self._a8s_path),
                self._tool_paths,
                self._dm,
                progress_cb=lambda msg: self.after(0, lambda m=msg: self._set_status(m)),
            )
            self.after(0, lambda: self._on_success(islands))
        except SavegameParseError as exc:
            msg = str(exc)
            self.after(0, lambda m=msg: self._on_error(m))
        except Exception as exc:
            msg = f"Unexpected error:\n{exc}"
            self.after(0, lambda m=msg: self._on_error(m))

    def _set_status(self, msg: str):
        self._status.config(text=msg)

    def _on_success(self, islands: list):
        self._islands = islands
        self._bar.stop()
        self._bar.config(mode='determinate', value=100)

        if self._on_parsed:
            self._on_parsed(islands)

        if not islands:
            self._set_status("No player-owned islands found in this savegame.")
        else:
            self._set_status(
                f"Found {len(islands)} island(s). "
                f"Select one and click 'Import to Canvas'."
            )
            # Populate the listbox
            self._island_lb.config(state='normal')
            for isl in islands:
                bd_count = len(isl.buildings)
                bp_count = sum(1 for b in isl.buildings if b.is_blueprint)
                note = f"  ({bp_count} bp)" if bp_count else ""
                label = (
                    f"{isl.island_key}"
                    f"  –  {bd_count} buildings{note}"
                )
                self._island_lb.insert('end', label)
            self._island_lb.selection_set(0)   # pre-select first island
            self._result_frame.pack(fill='x', padx=16, pady=(0, 4))
            if self._on_import:
                self._import_btn.config(state='normal')

        self._close_btn.config(state='normal')
        self.after(50, self._centre)

    def _do_import(self):
        if not self._islands or self._on_import is None:
            return
        sel = self._island_lb.curselection()
        idx = sel[0] if sel else 0
        island = self._islands[idx]
        self.destroy()
        self._on_import(island)

    def _on_error(self, msg: str):
        self._bar.stop()
        self._bar.config(mode='determinate', value=0)
        self._set_status(f"Error: {msg[:300]}")
        self._close_btn.config(state='normal')


# ── Entry point ─────────────────────────────────────────────────────────────
def main():
    app = App()
    app.mainloop()


if __name__ == '__main__':
    main()
