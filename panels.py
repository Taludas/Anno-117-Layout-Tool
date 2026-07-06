"""
Anno 117 Layout Tool - Info Panels
Right-side layout info panel + top-right selected building info panel.
"""
import tkinter as tk
from tkinter import ttk, colorchooser
import math
from typing import Optional

from config import (
    BG_MAIN, BG_SECTION, BG_HOVER, FG_MAIN, FG_DIM, FG_GOLD, FG_SEPARATOR,
    BORDER_COLOR, BORDER_GOLD, ACCENT_RED,
    FONT_TITLE, FONT_HEADER, FONT_BODY, FONT_BOLD_SMALL, FONT_SMALL, FONT_XSMALL,
    REGION_DISPLAY, resource_path,
)
from data_manager import get_data_manager, BuildingData
from build_menu import MenuButton
from dialogs import make_checkbox, save_settings

try:
    from PIL import Image, ImageTk
    _PIL_OK = True
except ImportError:
    _PIL_OK = False

_product_icon_cache: dict = {}
_building_icon_cache: dict = {}

def _load_building_icon_small(icon_path: str, size: int = 16):
    if not _PIL_OK or not icon_path:
        return None
    key = (icon_path, size)
    if key in _building_icon_cache:
        return _building_icon_cache[key]
    full = resource_path(icon_path)
    try:
        import os
        if os.path.exists(full):
            img = Image.open(full).convert('RGBA').resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            _building_icon_cache[key] = photo
            return photo
    except Exception:
        pass
    return None

def _load_product_icon(icon_path: str, size: int = 14):
    if not _PIL_OK or not icon_path:
        return None
    key = (icon_path, size)
    if key in _product_icon_cache:
        return _product_icon_cache[key]
    full = resource_path(icon_path)
    try:
        import os
        if os.path.exists(full):
            img = Image.open(full).convert('RGBA').resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            _product_icon_cache[key] = photo
            return photo
    except Exception:
        pass
    return None

SQRT2 = math.sqrt(2)
PANEL_WIDTH = 220


def _sep(parent, color=FG_SEPARATOR):
    tk.Frame(parent, height=1, bg=color).pack(fill=tk.X, pady=4)


def _lbl(parent, text, fg=FG_MAIN, font=FONT_SMALL, anchor='w', **kw):
    return tk.Label(parent, text=text, bg=BG_SECTION, fg=fg, font=font, anchor=anchor, **kw)


def _scrolled_frame(parent):
    """Return (outer_frame, inner_scrollable_frame)."""
    outer = tk.Frame(parent, bg=BG_SECTION)
    canvas = tk.Canvas(outer, bg=BG_SECTION, highlightthickness=0)
    vsb = tk.Scrollbar(outer, orient=tk.VERTICAL, command=canvas.yview)
    canvas.config(yscrollcommand=vsb.set)
    vsb.pack(side=tk.RIGHT, fill=tk.Y)
    canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    inner = tk.Frame(canvas, bg=BG_SECTION)
    win_id = canvas.create_window((0, 0), window=inner, anchor='nw')

    def on_configure(e):
        canvas.config(scrollregion=canvas.bbox('all'))
        canvas.itemconfig(win_id, width=canvas.winfo_width())

    inner.bind('<Configure>', on_configure)
    canvas.bind('<Configure>', on_configure)

    def on_mousewheel(e):
        if e.num == 4 or e.delta > 0:
            canvas.yview_scroll(-1, 'units')
        else:
            canvas.yview_scroll(1, 'units')

    canvas.bind('<MouseWheel>', on_mousewheel)
    canvas.bind('<Button-4>', on_mousewheel)
    canvas.bind('<Button-5>', on_mousewheel)
    return outer, inner


# --------------------------------------------------------------------------- #
#  Layout Info Panel (right side)
# --------------------------------------------------------------------------- #
class LayoutInfoPanel(tk.Frame):
    """
    Right-side panel showing layout statistics:
    - Bounding box size
    - Tile area
    - Space efficiency
    - Building list with counts
    - Total construction and maintenance costs
    """

    def __init__(self, master, app, **kwargs):
        kwargs.setdefault('width', PANEL_WIDTH)
        super().__init__(master, bg=BG_SECTION, **kwargs)
        self.pack_propagate(False)
        self.app = app
        self.dm = get_data_manager()
        self._build_ui()

    def _build_ui(self):
        # Header
        hdr = tk.Frame(self, bg=BG_SECTION)
        hdr.pack(fill=tk.X, padx=8, pady=(8, 4))
        tk.Label(hdr, text="Layout Info", bg=BG_SECTION, fg=FG_GOLD, font=FONT_HEADER).pack(anchor='w')
        tk.Frame(self, height=1, bg=BORDER_GOLD).pack(fill=tk.X)

        # Delete / Clear All buttons pinned to the bottom
        tk.Frame(self, height=1, bg=BORDER_GOLD).pack(fill=tk.X, side=tk.BOTTOM)
        btn_row = tk.Frame(self, bg=BG_SECTION)
        btn_row.pack(fill=tk.X, side=tk.BOTTOM, padx=8, pady=6)
        self._del_btn = MenuButton(btn_row, text="Delete", command=self._toggle_delete)
        self._del_btn.pack(side=tk.LEFT, padx=2)
        MenuButton(btn_row, text="Clear All", command=self._clear_all).pack(side=tk.LEFT, padx=2)

        outer, self._scroll_inner = _scrolled_frame(self)
        outer.pack(fill=tk.BOTH, expand=True)
        self._content = self._scroll_inner
        self._render_empty()

    def _toggle_delete(self):
        dm_var = self.app.canvas_widget.delete_mode
        dm_var.set(not dm_var.get())
        self._del_btn.set_selected(dm_var.get())
        if dm_var.get():
            self.app.canvas_widget.cancel_build_mode()

    def _clear_all(self):
        from tkinter import messagebox
        if messagebox.askyesno("Clear Layout", "Remove all placed buildings?", parent=self.app):
            self.app.canvas_widget.clear_all()

    def _render_empty(self):
        for w in self._content.winfo_children():
            w.destroy()
        _lbl(self._content, "No buildings placed.", fg=FG_DIM, font=FONT_SMALL).pack(anchor='w', padx=8, pady=8)

    def update_stats(self, stats: dict):
        for w in self._content.winfo_children():
            w.destroy()
        inner = self._content
        pad = dict(padx=8, pady=1)

        if stats['bbox_area'] == 0:
            self._render_empty()
            return

        # --- Bounding box ---
        _lbl(inner, "Bounding Box", fg=FG_GOLD, font=FONT_BOLD_SMALL).pack(anchor='w', **pad)
        _lbl(inner, f"  {stats['bbox_w']} × {stats['bbox_h']} tiles", fg=FG_MAIN, font=FONT_SMALL).pack(anchor='w', **pad)
        _lbl(inner, f"  Area: {stats['bbox_area']} tiles", fg=FG_DIM, font=FONT_XSMALL).pack(anchor='w', **pad)

        _sep(inner)

        # --- Compact area ---
        _lbl(inner, "Footprint", fg=FG_GOLD, font=FONT_BOLD_SMALL).pack(anchor='w', **pad)
        _lbl(inner, f"  {stats['compact_area']} tiles", fg=FG_MAIN, font=FONT_SMALL).pack(anchor='w', **pad)

        eff_color = FG_GOLD if stats['efficiency'] >= 70 else FG_DIM
        _lbl(inner, f"  Efficiency: {stats['efficiency']:.1f}%", fg=eff_color, font=FONT_SMALL).pack(anchor='w', **pad)

        # Efficiency bar
        bar_outer = tk.Frame(inner, bg=BG_MAIN, height=8)
        bar_outer.pack(fill=tk.X, padx=10, pady=2)
        bar_inner = tk.Frame(bar_outer, bg=FG_GOLD, height=8)
        pct = min(100.0, stats['efficiency'])
        bar_inner.place(relwidth=pct / 100, relheight=1.0)

        _sep(inner)

        # --- Buildings list ---
        _lbl(inner, "Buildings", fg=FG_GOLD, font=FONT_BOLD_SMALL).pack(anchor='w', **pad)
        counts = stats['building_counts']
        guids  = stats.get('building_guids', {})
        dm     = get_data_manager()
        if counts:
            for name, cnt in sorted(counts.items()):
                row = tk.Frame(inner, bg=BG_SECTION)
                row.pack(fill=tk.X, padx=8)
                # Icon
                bd = dm.get_building(guids.get(name, -1))
                icon = _load_building_icon_small(bd.icon_path if bd else '', 16)
                if icon:
                    lbl_icon = tk.Label(row, image=icon, bg=BG_SECTION)
                    lbl_icon.image = icon  # prevent GC
                    lbl_icon.pack(side=tk.LEFT, padx=(0, 2))
                tk.Label(row, text=f"× {cnt}", bg=BG_SECTION, fg=FG_GOLD, font=FONT_XSMALL, width=7, anchor='e').pack(side=tk.LEFT)
                tk.Label(row, text=name, bg=BG_SECTION, fg=FG_MAIN, font=FONT_XSMALL, wraplength=100, justify='left', anchor='w').pack(side=tk.LEFT, padx=2)
        else:
            _lbl(inner, "  -", fg=FG_DIM, font=FONT_XSMALL).pack(anchor='w', **pad)

        _sep(inner)

        # --- Construction costs ---
        _lbl(inner, "Construction Cost", fg=FG_GOLD, font=FONT_BOLD_SMALL).pack(anchor='w', **pad)
        const = stats['total_construction']
        if const:
            lang = getattr(self.app, 'language', 'english')
            for pid, amt in sorted(const.items()):
                pd = self.dm.get_product(pid)
                pname = pd.get_name(lang) if pd else f'#{pid}'
                icon  = _load_product_icon(pd.icon_path if pd else '', 14)
                row = tk.Frame(inner, bg=BG_SECTION)
                row.pack(fill=tk.X, padx=8)
                if icon:
                    lbl = tk.Label(row, image=icon, bg=BG_SECTION)
                    lbl.image = icon
                    lbl.pack(side=tk.LEFT, padx=(0, 2))
                tk.Label(row, text=f"{amt}", bg=BG_SECTION, fg=FG_MAIN, font=FONT_XSMALL).pack(side=tk.LEFT)
                tk.Label(row, text=pname, bg=BG_SECTION, fg=FG_DIM, font=FONT_XSMALL).pack(side=tk.LEFT, padx=4)
        else:
            _lbl(inner, "  -", fg=FG_DIM, font=FONT_XSMALL).pack(anchor='w', **pad)

        _sep(inner)

        # --- Maintenance costs ---
        _lbl(inner, "Maintenance", fg=FG_GOLD, font=FONT_BOLD_SMALL).pack(anchor='w', **pad)
        maint = stats['total_maintenance']
        if maint:
            lang = getattr(self.app, 'language', 'english')
            for pid, amt in sorted(maint.items()):
                pd = self.dm.get_product(pid)
                pname = pd.get_name(lang) if pd else f'#{pid}'
                icon  = _load_product_icon(pd.icon_path if pd else '', 14)
                row = tk.Frame(inner, bg=BG_SECTION)
                row.pack(fill=tk.X, padx=8)
                if icon:
                    lbl = tk.Label(row, image=icon, bg=BG_SECTION)
                    lbl.image = icon
                    lbl.pack(side=tk.LEFT, padx=(0, 2))
                tk.Label(row, text=f"{amt}", bg=BG_SECTION, fg=FG_MAIN, font=FONT_XSMALL).pack(side=tk.LEFT)
                tk.Label(row, text=pname, bg=BG_SECTION, fg=FG_DIM, font=FONT_XSMALL).pack(side=tk.LEFT, padx=4)
        else:
            _lbl(inner, "  -", fg=FG_DIM, font=FONT_XSMALL).pack(anchor='w', **pad)

        # --- Effect bonuses ---
        bonuses = stats.get('effect_bonuses', [])
        if bonuses:
            _sep(inner)
            _lbl(inner, "Effect Bonuses", fg=FG_GOLD, font=FONT_BOLD_SMALL).pack(anchor='w', **pad)
            for b in bonuses:
                total = b['total']
                val_str = f"{total:+.0f}" if total == int(total) else f"{total:+.2f}"
                icon = _load_product_icon(b['icon'], 14)
                row = tk.Frame(inner, bg=BG_SECTION)
                row.pack(fill=tk.X, padx=8)
                if icon:
                    lbl = tk.Label(row, image=icon, bg=BG_SECTION)
                    lbl.image = icon
                    lbl.pack(side=tk.LEFT, padx=(0, 2))
                tk.Label(row, text=val_str, bg=BG_SECTION, fg=FG_GOLD if total >= 0 else '#e57373', font=FONT_XSMALL).pack(side=tk.LEFT)
                tk.Label(row, text=b['attr'], bg=BG_SECTION, fg=FG_DIM, font=FONT_XSMALL).pack(side=tk.LEFT, padx=4)


# --------------------------------------------------------------------------- #
#  Tech Effects popup (opened from BuildingInfoPanel)
# --------------------------------------------------------------------------- #
class _TechEffectsWindow:
    def __init__(self, parent_panel, app, tech_effects, placed_building):
        cw = getattr(app, 'canvas_widget', None)
        if not cw:
            return
        self._cw = cw
        self._app = app
        self._pb = placed_building
        lang = getattr(app, 'language', 'english')

        win = tk.Toplevel(parent_panel)
        win.title("Tech Effects")
        win.configure(bg=BG_SECTION)
        win.resizable(False, False)
        win.grab_set()

        active = cw._active_tech_effects.setdefault(placed_building.instance_id, set())
        self._vars = {}

        hdr = tk.Label(win, text="Available Tech Effects", bg=BG_SECTION, fg=FG_GOLD, font=FONT_BOLD_SMALL)
        hdr.pack(anchor='w', padx=10, pady=(8, 4))
        tk.Frame(win, height=1, bg=BORDER_GOLD).pack(fill=tk.X, padx=6)

        for effect in tech_effects:
            guid = effect.get('guid')
            name_d = effect.get('name') or {}
            name = name_d.get(lang) or name_d.get('english', f'#{guid}')
            desc_d = effect.get('infoDescription') or {}
            desc = desc_d.get(lang) or desc_d.get('english', '')
            icon_path = effect.get('icon', '')

            row = tk.Frame(win, bg=BG_SECTION)
            row.pack(fill=tk.X, padx=10, pady=(6, 0))
            icon = _load_product_icon(icon_path, 16)
            if icon:
                lbl = tk.Label(row, image=icon, bg=BG_SECTION)
                lbl.image = icon
                lbl.pack(side=tk.LEFT, padx=(0, 4))

            var = tk.BooleanVar(value=guid in active)
            self._vars[guid] = var
            make_checkbox(row, name, var, bg=BG_SECTION, font=FONT_SMALL).pack(side=tk.LEFT)

            if desc:
                tk.Label(win, text=desc, bg=BG_SECTION, fg=FG_DIM, font=FONT_XSMALL, wraplength=320, justify='left', anchor='w').pack(fill=tk.X, padx=28, pady=(0, 4))

            var.trace_add('write', lambda *_, g=guid, v=var: self._toggle(g, v))

        tk.Frame(win, height=1, bg=BORDER_GOLD).pack(fill=tk.X, padx=6, pady=(8, 0))
        tk.Button(win, text="Close", command=win.destroy, bg=BG_SECTION, fg=FG_MAIN, font=FONT_SMALL, relief=tk.FLAT, padx=10, pady=4).pack(pady=6)

        win.update_idletasks()
        px = parent_panel.winfo_rootx() + parent_panel.winfo_width() + 4
        py = parent_panel.winfo_rooty()
        win.geometry(f"+{px}+{py}")

    def _toggle(self, guid: int, var: tk.BooleanVar):
        active = self._cw._active_tech_effects.setdefault(self._pb.instance_id, set())
        if var.get():
            active.add(guid)
        else:
            active.discard(guid)
        if hasattr(self._app, 'building_info_panel'):
            self._cw._notify_selection()


def _open_tech_popup(parent_panel, app, tech_effects, placed_building):
    _TechEffectsWindow(parent_panel, app, tech_effects, placed_building)


# --------------------------------------------------------------------------- #
#  Item Effects popup (opened from BuildingInfoPanel)
# --------------------------------------------------------------------------- #
_RARITY_COLORS = {
    'Common':    '#b0b0b0',
    'Rare':      '#5baaff',
    'Epic':      '#c060ff',
    'Legendary': '#f0a000',
    'Unique':    '#ff9944',
}


class _ItemEffectsWindow:
    def __init__(self, parent_panel, app, items, placed_building):
        cw = getattr(app, 'canvas_widget', None)
        if not cw:
            return
        self._cw = cw
        self._app = app
        self._pb = placed_building
        lang = getattr(app, 'language', 'english')
        dm = get_data_manager()

        win = tk.Toplevel(parent_panel)
        win.title("Item Effects")
        win.configure(bg=BG_SECTION)
        win.resizable(False, False)
        win.grab_set()

        active = cw._active_item_effects.setdefault(placed_building.instance_id, set())
        active_boosts = cw._active_item_boosts.setdefault(placed_building.instance_id, set())
        self._vars = {}
        self._boost_vars = {}

        tk.Label(win, text="Available Items", bg=BG_SECTION, fg=FG_GOLD,
                 font=FONT_BOLD_SMALL).pack(anchor='w', padx=10, pady=(8, 4))
        tk.Frame(win, height=1, bg=BORDER_GOLD).pack(fill=tk.X, padx=6)

        # Scrollable content area
        container = tk.Frame(win, bg=BG_SECTION)
        container.pack(fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(container, orient='vertical')
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        canvas_sc = tk.Canvas(container, bg=BG_SECTION, yscrollcommand=scrollbar.set,
                              highlightthickness=0, width=420, height=440)
        canvas_sc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=canvas_sc.yview)
        inner = tk.Frame(canvas_sc, bg=BG_SECTION)
        inner_id = canvas_sc.create_window((0, 0), window=inner, anchor='nw')

        def _on_frame_configure(*_):
            canvas_sc.configure(scrollregion=canvas_sc.bbox('all'))
        inner.bind('<Configure>', _on_frame_configure)

        def _on_canvas_configure(event):
            canvas_sc.itemconfig(inner_id, width=event.width)
        canvas_sc.bind('<Configure>', _on_canvas_configure)

        def _on_mousewheel(event):
            canvas_sc.yview_scroll(int(-1 * (event.delta / 120)), 'units')
        canvas_sc.bind('<MouseWheel>', _on_mousewheel)
        win.bind('<MouseWheel>', _on_mousewheel)

        for item in items:
            guid = item.get('guid')
            name_d = item.get('name') or {}
            name = name_d.get(lang) or name_d.get('english', f'#{guid}')
            desc_d = item.get('infoDescription') or {}
            desc = desc_d.get(lang) or desc_d.get('english', '')
            icon_path = item.get('icon', '')
            rarity = item.get('rarity', 'Common')
            rarity_color = _RARITY_COLORS.get(rarity, FG_MAIN)
            has_boost = bool(item.get('boostBuffs'))

            # ── Item header row: icon + checkbox + rarity tag ──────────────
            row_f = tk.Frame(inner, bg=BG_SECTION)
            row_f.pack(fill=tk.X, padx=10, pady=(6, 0))
            icon = _load_product_icon(icon_path, 16)
            if icon:
                lbl = tk.Label(row_f, image=icon, bg=BG_SECTION)
                lbl.image = icon
                lbl.pack(side=tk.LEFT, padx=(0, 4))

            var = tk.BooleanVar(value=guid in active)
            self._vars[guid] = var
            make_checkbox(row_f, name, var, bg=BG_SECTION, font=FONT_SMALL).pack(side=tk.LEFT)
            tk.Label(row_f, text=f'[{rarity}]', bg=BG_SECTION, fg=rarity_color,
                     font=FONT_XSMALL).pack(side=tk.LEFT, padx=(6, 0))

            # ── Description ────────────────────────────────────────────────
            if desc:
                tk.Label(inner, text=desc, bg=BG_SECTION, fg=FG_DIM, font=FONT_XSMALL,
                         wraplength=380, justify='left', anchor='w').pack(
                    fill=tk.X, padx=28, pady=(0, 2))

            # ── Effect preview ─────────────────────────────────────────────
            reg_bonuses, boost_bonuses = dm.get_item_effect_preview(guid)
            if reg_bonuses or boost_bonuses:
                b_frame = tk.Frame(inner, bg=BG_SECTION)
                b_frame.pack(anchor='w', padx=28, pady=(0, 2))

                def _bonus_row(parent, bonus):
                    b_row = tk.Frame(parent, bg=BG_SECTION)
                    b_row.pack(anchor='w')
                    b_icon = _load_product_icon(bonus['icon'], 12)
                    if b_icon:
                        lbl2 = tk.Label(b_row, image=b_icon, bg=BG_SECTION)
                        lbl2.image = b_icon
                        lbl2.pack(side=tk.LEFT, padx=(0, 2))
                    total = bonus['total']
                    val_str = f"{total:+.0f}" if total == int(total) else f"{total:+.2f}"
                    suffix = ' /bldg.' if bonus.get('radius') else ''
                    tk.Label(b_row, text=f"{bonus['attr']}: {val_str}{suffix}",
                             bg=BG_SECTION, fg=FG_GOLD, font=FONT_XSMALL).pack(side=tk.LEFT)

                for bonus in reg_bonuses:
                    _bonus_row(b_frame, bonus)

                # Boost preview (shown below regular, with boost condition hint)
                if has_boost and boost_bonuses:
                    boost_hint_d = item.get('boostHint') or {}
                    boost_hint = boost_hint_d.get(lang) or boost_hint_d.get('english', '')
                    boost_row_f = tk.Frame(inner, bg=BG_SECTION)
                    boost_row_f.pack(fill=tk.X, padx=28, pady=(2, 0))
                    boost_var = tk.BooleanVar(value=guid in active_boosts)
                    self._boost_vars[guid] = boost_var
                    make_checkbox(boost_row_f, "Boosted", boost_var,
                                  bg=BG_SECTION, font=FONT_XSMALL).pack(side=tk.LEFT)
                    if boost_hint:
                        tk.Label(boost_row_f, text=f'({boost_hint})',
                                 bg=BG_SECTION, fg=FG_DIM, font=FONT_XSMALL,
                                 wraplength=260, justify='left').pack(side=tk.LEFT, padx=(4, 0))
                    b_boost_frame = tk.Frame(inner, bg=BG_SECTION)
                    b_boost_frame.pack(anchor='w', padx=44, pady=(0, 2))
                    for bonus in boost_bonuses:
                        _bonus_row(b_boost_frame, bonus)

                    boost_var.trace_add('write',
                                        lambda *_, g=guid, v=boost_var: self._toggle_boost(g, v))

            tk.Frame(inner, height=1, bg=BG_SECTION).pack(fill=tk.X, pady=(2, 0))
            var.trace_add('write', lambda *_, g=guid, v=var: self._toggle(g, v))

        tk.Frame(win, height=1, bg=BORDER_GOLD).pack(fill=tk.X, padx=6, pady=(8, 0))
        tk.Button(win, text="Close", command=win.destroy, bg=BG_SECTION, fg=FG_MAIN,
                  font=FONT_SMALL, relief=tk.FLAT, padx=10, pady=4).pack(pady=6)

        win.update_idletasks()
        px = parent_panel.winfo_rootx() + parent_panel.winfo_width() + 4
        py = parent_panel.winfo_rooty()
        win.geometry(f"+{px}+{py}")

    def _toggle(self, guid: int, var: tk.BooleanVar):
        active = self._cw._active_item_effects.setdefault(self._pb.instance_id, set())
        if var.get():
            active.add(guid)
        else:
            active.discard(guid)
            # deactivating item also deactivates its boost
            self._cw._active_item_boosts.get(self._pb.instance_id, set()).discard(guid)
            bv = self._boost_vars.get(guid)
            if bv is not None:
                bv.set(False)
        if hasattr(self._app, 'building_info_panel'):
            self._cw._notify_selection()

    def _toggle_boost(self, guid: int, var: tk.BooleanVar):
        active_boosts = self._cw._active_item_boosts.setdefault(self._pb.instance_id, set())
        if var.get():
            active_boosts.add(guid)
        else:
            active_boosts.discard(guid)
        if hasattr(self._app, 'building_info_panel'):
            self._cw._notify_selection()


def _open_item_popup(parent_panel, app, items, placed_building):
    _ItemEffectsWindow(parent_panel, app, items, placed_building)


# --------------------------------------------------------------------------- #
#  Building Info Panel (top-right overlay on canvas)
# --------------------------------------------------------------------------- #
class BuildingInfoPanel(tk.Frame):
    """
    Overlay panel in the top-right of the canvas showing info about the currently selected / hovered building.
    """

    def __init__(self, master, app, **kwargs):
        super().__init__(master, bg=BG_SECTION, bd=1, relief=tk.FLAT, highlightbackground=BORDER_GOLD, highlightthickness=1, **kwargs)
        self.app = app
        self.dm = get_data_manager()
        self._current_guid: Optional[int] = None
        self._apply_to_category_var = tk.BooleanVar(value=False)
        self._last_show_args: Optional[tuple] = None
        self._build_ui()

    def _build_ui(self):
        self._frame = tk.Frame(self, bg=BG_SECTION)
        self._frame.pack(fill=tk.BOTH, expand=True, padx=6, pady=6)
        self._render_placeholder()

    def _fit_height(self):
        """Resize panel height to fit content, capped at the canvas height."""
        self.update_idletasks()
        # 12 = 6px pady top + 6px pady bottom; 2 = 1px border each side
        needed = self._frame.winfo_reqheight() + 14
        canvas_h = self.master.winfo_height()
        if canvas_h < 50:
            canvas_h = 600  # before first draw
        max_h = canvas_h - 8  # 4px top offset + 4px bottom clearance
        self.place_configure(height=max(min(needed, max_h), 40))

    def _render_placeholder(self):
        for w in self._frame.winfo_children():
            w.destroy()
        _lbl(self._frame, "Select a building", fg=FG_DIM, font=FONT_SMALL).pack(anchor='w')
        self.after(1, self._fit_height)

    def clear(self):
        self._current_guid = None
        self._render_placeholder()

    def _pick_color(self, guid: int):
        bd = self.dm.get_building(guid)
        if not bd:
            return
        current = self.dm.get_building_color(bd)
        _rgb, hex_color = colorchooser.askcolor(color=current, parent=self, title="Building Colour")
        if not hex_color:
            return
        self.dm.set_building_color(
            guid, hex_color, apply_to_category=self._apply_to_category_var.get())

        settings = getattr(self.app, 'settings', None)
        if settings is not None:
            settings['building_color_overrides'] = {
                str(g): c for g, c in self.dm.building_color_overrides.items()}
            settings['category_color_overrides'] = dict(self.dm.category_color_overrides)
            save_settings(settings)

        cw = getattr(self.app, 'canvas_widget', None)
        if cw:
            cw._redraw()
        if self._last_show_args:
            self.show_building(*self._last_show_args)

    def show_building(self, bd: Optional[BuildingData], rotation: int = 0, free_tiles: Optional[int] = None, placed_building=None):
        if bd is None:
            self.clear()
            return
        self._current_guid = bd.guid
        self._last_show_args = (bd, rotation, free_tiles, placed_building)
        lang = getattr(self.app, 'language', 'english')
        f = self._frame
        for w in f.winfo_children():
            w.destroy()

        def row(key, val, key_fg=FG_DIM, val_fg=FG_MAIN):
            r = tk.Frame(f, bg=BG_SECTION)
            r.pack(fill=tk.X, pady=1)
            tk.Label(r, text=key, bg=BG_SECTION, fg=key_fg, font=FONT_XSMALL, width=12, anchor='e').pack(side=tk.LEFT)
            tk.Label(r, text=val, bg=BG_SECTION, fg=val_fg, font=FONT_XSMALL, anchor='w', justify='left', wraplength=110).pack(side=tk.LEFT, padx=4)

        def cost_row(pid, amt, eff_amt=None):
            pd = self.dm.get_product(pid)
            pname = pd.get_name(lang) if pd else f'#{pid}'
            icon = _load_product_icon(pd.icon_path if pd else '', 14)
            r = tk.Frame(f, bg=BG_SECTION)
            r.pack(fill=tk.X, pady=1)
            tk.Label(r, text='', bg=BG_SECTION, font=FONT_XSMALL, width=2).pack(side=tk.LEFT)
            if icon:
                lbl = tk.Label(r, image=icon, bg=BG_SECTION)
                lbl.image = icon
                lbl.pack(side=tk.LEFT, padx=(0, 2))
            if eff_amt is not None and abs(eff_amt - amt) > 0.01:
                eff_str = f"{eff_amt:.1f}" if eff_amt != int(eff_amt) else str(int(eff_amt))
                tk.Label(r, text=eff_str, bg=BG_SECTION, fg=FG_GOLD, font=FONT_XSMALL).pack(side=tk.LEFT)
                tk.Label(r, text=f'({amt})', bg=BG_SECTION, fg=FG_DIM, font=FONT_XSMALL).pack(side=tk.LEFT, padx=(2, 0))
            else:
                tk.Label(r, text=str(amt), bg=BG_SECTION, fg=FG_MAIN, font=FONT_XSMALL).pack(side=tk.LEFT)
            tk.Label(r, text=pname, bg=BG_SECTION, fg=FG_DIM, font=FONT_XSMALL).pack(side=tk.LEFT, padx=4)

        # Title
        name = bd.get_name(lang)
        tk.Label(f, text=name, bg=BG_SECTION, fg=FG_GOLD, font=FONT_BOLD_SMALL, wraplength=180, anchor='w', justify='left').pack(anchor='w', pady=(0, 2))

        # Colour-picker icon + swatch + "apply to category" option, on one line
        color_row = tk.Frame(f, bg=BG_SECTION)
        color_row.pack(fill=tk.X, pady=(0, 2))
        color_icon = tk.Label(color_row, text='🎨', bg=BG_SECTION, fg=FG_MAIN, font=FONT_XSMALL, cursor='hand2')
        color_icon.pack(side=tk.LEFT, padx=(0, 4))
        color_icon.bind('<Button-1>', lambda _e, g=bd.guid: self._pick_color(g))

        swatch = tk.Canvas(color_row, width=18, height=18, bg=BG_SECTION, highlightthickness=1, highlightbackground=BORDER_COLOR, cursor='hand2')
        swatch.create_rectangle(1, 1, 17, 17, fill=self.dm.get_building_color(bd), outline='')
        swatch.pack(side=tk.LEFT, padx=(0, 6))
        swatch.bind('<Button-1>', lambda _e, g=bd.guid: self._pick_color(g))

        make_checkbox(color_row, "Apply to category", self._apply_to_category_var, bg=BG_SECTION, font=FONT_XSMALL).pack(side=tk.LEFT)

        tk.Frame(f, height=1, bg=BORDER_GOLD).pack(fill=tk.X, pady=2)

        row("GUID:", str(bd.guid))
        row("Region:", ", ".join(bd.associated_regions))
        row("Category:", bd.get_category(lang))

        # Size in tiles
        rot = rotation % 360
        if rot in (0, 90, 180, 270):
            if rot in (0, 180):
                size_str = f"{bd.width} × {bd.height}"
            else:
                size_str = f"{bd.height} × {bd.width}"
            row("Size (90°):", size_str)
        else:
            from data_manager import _get_45_grid_counts
            nw45, nh45 = _get_45_grid_counts(bd, rotation)
            row("Size (45°):", f"{nw45} × {nh45}")

        # Module info + quick-build button
        if bd.module_guid:
            cw         = getattr(self.app, 'canvas_widget', None)
            parent_iid = placed_building.instance_id if placed_building else None

            # ── Primary module ────────────────────────────────────────────
            mod_bd   = self.dm.get_building(bd.module_guid)
            mod_name = mod_bd.get_name(lang) if mod_bd else self.dm.get_building_name(bd.module_guid, lang)
            row("Module:", mod_name[:20])
            if bd.module_limit:
                limit = bd.module_limit
                if cw and placed_building:
                    placed = sum(1 for pm in cw.placed_buildings
                                 if pm.guid == bd.module_guid
                                 and pm.parent_id == placed_building.instance_id)
                    done      = placed >= limit
                    count_str = f"{'✓ ' if done else ''}{placed}/{limit}"
                    row("Mod. Limit:", count_str, val_fg=FG_GOLD if done else FG_MAIN)
                else:
                    row("Mod. Limit:", str(limit))
            if bd.module_build_radius:
                row("Mod. Radius:", str(bd.module_build_radius))

            # ── Additional module ─────────────────────────────────────────
            add_mod_bd = None
            if bd.additional_module_guid:
                add_mod_bd   = self.dm.get_building(bd.additional_module_guid)
                add_mod_name = add_mod_bd.get_name(lang) if add_mod_bd else f'#{bd.additional_module_guid}'
                row("Add. Module:", add_mod_name[:20])
                add_limit = 1
                if cw and placed_building:
                    add_placed = sum(1 for pm in cw.placed_buildings
                                     if pm.guid == bd.additional_module_guid
                                     and pm.parent_id == placed_building.instance_id)
                    done      = add_placed >= add_limit
                    count_str = f"{'✓ ' if done else ''}{add_placed}/{add_limit}"
                    row("Add. Limit:", count_str, val_fg=FG_GOLD if done else FG_MAIN)
                else:
                    row("Add. Limit:", str(add_limit))

            # ── Buttons (one per line) ────────────────────────────────────
            def _build_module(g=bd.module_guid, piid=parent_iid):
                cw2 = getattr(self.app, 'canvas_widget', None)
                if cw2:
                    cw2.set_build_mode(g, module_parent_id=piid)
                    if mod_bd and hasattr(self.app, 'building_info_panel'):
                        self.app.building_info_panel.show_building(mod_bd, 0)
            btn_row1 = tk.Frame(f, bg=BG_SECTION)
            btn_row1.pack(fill=tk.X, pady=(2, 0))
            MenuButton(btn_row1, text=f"Modul: {mod_name[:16]}",
                       command=_build_module).pack(side=tk.LEFT, padx=(8, 2))

            if bd.additional_module_guid:
                def _build_add_module(g=bd.additional_module_guid, piid=parent_iid):
                    cw2 = getattr(self.app, 'canvas_widget', None)
                    if cw2:
                        cw2.set_build_mode(g, module_parent_id=piid)
                        if add_mod_bd and hasattr(self.app, 'building_info_panel'):
                            self.app.building_info_panel.show_building(add_mod_bd, 0)
                btn_row2 = tk.Frame(f, bg=BG_SECTION)
                btn_row2.pack(fill=tk.X, pady=(2, 0))
                _add_btn = MenuButton(btn_row2, text=f"Modul: {add_mod_name[:16]}",
                                      command=_build_add_module)
                _add_btn.pack(side=tk.LEFT, padx=(8, 2))
                # Enforce hard limit of 1 for additional module
                _add_full = (cw and placed_building and
                             sum(1 for pm in cw.placed_buildings
                                 if pm.guid == bd.additional_module_guid
                                 and pm.parent_id == placed_building.instance_id) >= 1)
                if _add_full:
                    _add_btn.set_disabled(True)

        if bd.radius and isinstance(bd.radius, dict):
            r_type = bd.radius.get('type', 'Radius')
            r_val  = bd.radius.get('value', '')
            label  = "Street Dist.:" if r_type == 'StreetDistance' else "Radius:"
            row(label, str(r_val))

            cw = getattr(self.app, 'canvas_widget', None)
            if cw:
                count = None
                in_range_guids = None
                active_tech = (cw._active_tech_effects.get(placed_building.instance_id, set())
                               if placed_building is not None else set())
                active_items_r = (cw._active_item_effects.get(placed_building.instance_id, set())
                                  if placed_building is not None else set())
                active_boosts_r = (cw._active_item_boosts.get(placed_building.instance_id, set())
                                   if placed_building is not None else set())
                if placed_building is not None:
                    count = cw.get_in_range_count(
                        bd, placed_building.grid_x, placed_building.grid_y,
                        placed_building.rotation, exclude_id=placed_building.instance_id,
                        active_tech_guids=active_tech)
                    if bd.functional_effects or bd.public_service_effect or active_tech or active_items_r:
                        in_range_guids = cw.get_in_range_guids(
                            bd, placed_building.grid_x, placed_building.grid_y,
                            placed_building.rotation, exclude_id=placed_building.instance_id,
                            active_tech_guids=active_tech)
                elif cw.build_mode_guid == bd.guid and cw._ghost_grid_pos is not None:
                    gx, gy = cw._ghost_grid_pos
                    count = cw.get_in_range_count(bd, gx, gy, cw.build_rotation)
                if count is not None:
                    row("Affected:", str(count), val_fg=FG_GOLD)

                # Effect bonuses (only for placed selected buildings)
                if in_range_guids is not None:
                    bonuses = self.dm.compute_radius_bonuses(
                        bd.guid, in_range_guids, active_tech,
                        active_items_r, active_boosts_r)
                    for bonus in bonuses:
                        icon = _load_product_icon(bonus['icon'], 14)
                        total = bonus['total']
                        val_str = f"{total:+.0f}" if total == int(total) else f"{total:+.2f}"
                        r = tk.Frame(f, bg=BG_SECTION)
                        r.pack(fill=tk.X, pady=1)
                        tk.Label(r, text='', bg=BG_SECTION, font=FONT_XSMALL, width=2).pack(side=tk.LEFT)
                        if icon:
                            lbl = tk.Label(r, image=icon, bg=BG_SECTION)
                            lbl.image = icon
                            lbl.pack(side=tk.LEFT, padx=(0, 2))
                        tk.Label(r, text=f"{bonus['attr']}: {val_str}", bg=BG_SECTION, fg=FG_GOLD, font=FONT_XSMALL).pack(side=tk.LEFT)

                # Tech effects button
                if placed_building is not None:
                    tech_effects = self.dm.get_available_tech_effects(bd.guid)
                    if tech_effects:
                        def _open_techs(te=tech_effects, pb=placed_building):
                            _open_tech_popup(self, self.app, te, pb)
                        btn_tech_row = tk.Frame(f, bg=BG_SECTION)
                        btn_tech_row.pack(fill=tk.X, pady=(2, 0))
                        MenuButton(btn_tech_row, text="Tech Effects", command=_open_techs).pack(side=tk.LEFT, padx=(8, 2))

        # Item effects button + active item bonuses
        if placed_building is not None:
            items_avail = self.dm.get_available_items(bd.guid)
            if items_avail:
                cw_item = getattr(self.app, 'canvas_widget', None)
                active_items = (cw_item._active_item_effects.get(placed_building.instance_id, set())
                                if cw_item else set())

                def _open_items(it=items_avail, pb=placed_building):
                    _open_item_popup(self, self.app, it, pb)
                btn_item_row = tk.Frame(f, bg=BG_SECTION)
                btn_item_row.pack(fill=tk.X, pady=(2, 0))
                MenuButton(btn_item_row, text="Item Effects", command=_open_items).pack(side=tk.LEFT, padx=(8, 2))

                if active_items:
                    active_boosts = (cw_item._active_item_boosts.get(placed_building.instance_id, set())
                                     if cw_item else set())
                    item_bonuses = self.dm.compute_item_bonuses(bd.guid, active_items, active_boosts)
                    for bonus in item_bonuses:
                        icon = _load_product_icon(bonus['icon'], 14)
                        total = bonus['total']
                        val_str = f"{total:+.0f}" if total == int(total) else f"{total:+.2f}"
                        r = tk.Frame(f, bg=BG_SECTION)
                        r.pack(fill=tk.X, pady=1)
                        tk.Label(r, text='', bg=BG_SECTION, font=FONT_XSMALL, width=2).pack(side=tk.LEFT)
                        if icon:
                            lbl = tk.Label(r, image=icon, bg=BG_SECTION)
                            lbl.image = icon
                            lbl.pack(side=tk.LEFT, padx=(0, 2))
                        tk.Label(r, text=f"{bonus['attr']}: {val_str}", bg=BG_SECTION,
                                 fg=FG_GOLD, font=FONT_XSMALL).pack(side=tk.LEFT)

        # Free area productivity
        if bd.free_area_productivity and isinstance(bd.free_area_productivity, dict):
            inf_r  = bd.free_area_productivity.get('influenceRadius', '')
            needed = bd.free_area_productivity.get('neededArea', 0)
            tk.Frame(f, height=1, bg=FG_SEPARATOR).pack(fill=tk.X, pady=3)
            tk.Label(f, text="Free Area", bg=BG_SECTION, fg=FG_GOLD, font=FONT_XSMALL).pack(anchor='w')
            row("Radius:", str(inf_r))
            if free_tiles is not None:
                if free_tiles >= needed:
                    row("Free Tiles:", f"✓  ({free_tiles}/{needed})", val_fg=FG_GOLD)
                else:
                    row("Free Tiles:", f"✗  {free_tiles}/{needed}", val_fg=ACCENT_RED)
            else:
                row("Needed:", str(needed))

        # Construction costs
        if bd.construction_costs:
            tk.Frame(f, height=1, bg=FG_SEPARATOR).pack(fill=tk.X, pady=3)
            tk.Label(f, text="Construction", bg=BG_SECTION, fg=FG_GOLD, font=FONT_XSMALL).pack(anchor='w')
            for cc in bd.construction_costs:
                cost_row(cc['product'], cc['amount'])

        # Maintenance costs
        if bd.maintenance_costs:
            tk.Frame(f, height=1, bg=FG_SEPARATOR).pack(fill=tk.X, pady=3)
            tk.Label(f, text="Maintenance", bg=BG_SECTION, fg=FG_GOLD, font=FONT_XSMALL).pack(anchor='w')
            maint_pct = 0.0
            workforce_pct = 0.0
            if placed_building:
                cw_mc = getattr(self.app, 'canvas_widget', None)
                if cw_mc:
                    _ai = cw_mc._active_item_effects.get(placed_building.instance_id, set())
                    _ab = cw_mc._active_item_boosts.get(placed_building.instance_id, set())
                    if _ai:
                        mods = self.dm.get_item_maintenance_modifiers(_ai, _ab)
                        maint_pct = mods['maint_pct']
                        workforce_pct = mods['workforce_pct']
            for mc in bd.maintenance_costs:
                pid = mc['product']
                base = mc['amount']
                if maint_pct != 0.0 or workforce_pct != 0.0:
                    pd = self.dm.get_product(pid)
                    is_wf = pd is not None and pd.is_workforce
                    pct = workforce_pct if is_wf else maint_pct
                    eff = base * (1 + pct / 100.0) if pct != 0.0 else base
                    cost_row(pid, base, eff_amt=eff)
                else:
                    cost_row(pid, base)

        # Upgrade button
        if bd.upgrade_guid and placed_building:
            upgrade_bd = self.dm.get_building(bd.upgrade_guid)
            upgrade_name = upgrade_bd.get_name(lang) if upgrade_bd else f'#{bd.upgrade_guid}'
            def _do_upgrade(piid=placed_building.instance_id, g=bd.upgrade_guid):
                cw2 = getattr(self.app, 'canvas_widget', None)
                if cw2:
                    cw2.upgrade_building(piid, g)
            tk.Frame(f, height=1, bg=BORDER_GOLD).pack(fill=tk.X, pady=(6, 2))
            tk.Label(f, text="Upgrade", bg=BG_SECTION, fg=FG_GOLD, font=FONT_XSMALL).pack(anchor='w')
            btn_upgrade_row = tk.Frame(f, bg=BG_SECTION)
            btn_upgrade_row.pack(fill=tk.X, pady=(2, 4))
            MenuButton(btn_upgrade_row, text=f"→ {upgrade_name}", command=_do_upgrade, wraplength=170).pack(fill=tk.X, padx=(8, 4))

        self.after(1, self._fit_height)
