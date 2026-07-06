"""
Anno 117 Layout Tool - Build Menu (bottom bar)
In-game style:
  Row 1 (TOP):    Scrollable icon strip - one icon per item in the current selection.
  Row 2 (BOTTOM): Region tabs | Tier tabs | Infrastructure/Materials/Ornaments | Delete/Clear
Chain/category detail popovers appear above the icon strip.
"""
import tkinter as tk
import os
from typing import Optional, Callable

from config import (
    BG_MAIN, BG_SECTION, BG_HOVER, BG_SELECTED, FG_MAIN, FG_DIM, FG_GOLD,
    FG_SEPARATOR, BORDER_COLOR, BORDER_GOLD,
    FONT_BOLD_SMALL, FONT_SMALL, FONT_XSMALL,
    get_category_color, resource_path, REGION_DISPLAY,
)
from data_manager import get_data_manager, BuildingData

try:
    from PIL import Image, ImageTk
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False


# ── Layout constants ──────────────────────────────────────────────────────────
ICON_STRIP_SIZE       = 40    # icon size in the base icon strip
DETAIL_ICON_SIZE      = 44    # icon size inside chain/category popups
DETAIL_ROW_H          = 72    # pixel height per chain-row; must match minsize in _build_chain
DETAIL_TITLE_H        = 38    # title label + separator + top/bottom padding
DETAIL_PAD_H          = 8     # bottom margin inside popup
DETAIL_CATEGORY_ROW_H = 72    # content height for category popups (labels removed; +8px gap above scrollbar)
DETAIL_CATEGORY_SCROLL_H = 16 # horizontal scrollbar height reserved in category popups
MENU_STRIP_H          = 76    # icon strip row (indicator ~10px + icon 40px + padding + scrollbar)
MENU_NAV_H            = 36    # navigation tabs row


# ── Shared image cache keyed by (path_or_guid, size) ─────────────────────────
_icon_cache: dict = {}


def _first_icon_in_items(items: list, dm, size: int):
    """Recursively find the first usable icon from a (possibly nested) items list."""
    for it in items:
        t = it.get('type', 'building')
        if t == 'building':
            bd = dm.get_building(it.get('guid'))
            if bd:
                icon = _load_building_icon(bd, size)
                if icon:
                    return icon
        elif t == 'production_chain':
            blds = it.get('buildings', [])
            if blds:
                tier0 = min(blds, key=lambda b: b.get('tier', 99))
                bd = dm.get_building(tier0.get('guid'))
                if bd:
                    icon = _load_building_icon(bd, size)
                    if icon:
                        return icon
        elif t == 'category':
            icon = _first_icon_in_items(it.get('items', []), dm, size)
            if icon:
                return icon
    return None


def _hex_to_rgb(h: str) -> tuple:
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))


def _load_icon_from_path(icon_path: str, size: int):
    """Load and cache a PIL PhotoImage from a relative file path."""
    if not PIL_AVAILABLE or not icon_path:
        return None
    key = (icon_path, size)
    if key in _icon_cache:
        return _icon_cache[key]
    full = resource_path(icon_path)
    try:
        if os.path.exists(full):
            img = Image.open(full).convert('RGBA').resize((size, size), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            _icon_cache[key] = photo
            return photo
    except Exception:
        pass
    return None


def _load_building_icon(bd: BuildingData, size: int):
    """Load and cache icon for a BuildingData, with coloured-abbrev fallback."""
    if not PIL_AVAILABLE:
        return None
    if bd.icon_path:
        img = _load_icon_from_path(bd.icon_path, size)
        if img:
            return img
    # Fallback: category-coloured tile with 2-letter abbreviation
    key = (f'_fallback_{bd.guid}', size)
    if key in _icon_cache:
        return _icon_cache[key]
    from PIL import ImageDraw
    pil_img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(pil_img)
    cat_color = get_category_color(bd.get_category_english())
    r, g, b = _hex_to_rgb(cat_color)
    draw.rectangle([1, 1, size - 2, size - 2], fill=(r, g, b, 200), outline=(200, 200, 200, 120), width=1)
    abbrev = bd.get_name('english')[:2].upper()
    try:
        draw.text((size // 2, size // 2), abbrev, fill=(255, 255, 255, 220), anchor='mm')
    except Exception:
        pass
    photo = ImageTk.PhotoImage(pil_img)
    _icon_cache[key] = photo
    return photo


# ─────────────────────────────────────────────────────────────────────────────
#  IconButton
# ─────────────────────────────────────────────────────────────────────────────
NAV_ICON_SIZE  = 22   # icon size inside nav-bar tier buttons
QUICK_BTN_SIZE = 24   # quick-access / materials icon buttons in the nav bar
FIXED_BTN_SIZE = 28   # infrastructure / ornaments icon buttons (match tier-button height)

# Quick-access entries keyed by region.
# Building entries: {'guid': int, 'label': str}
# Infrastructure-category entries: {'infra_cat_guid': int, 'label': str} - opens a popup.
QUICK_ACCESS = {
    'Roman':  [
        {'guid': 8541,  'label': 'Road'},
        {'guid': 23996, 'label': 'Paved Road'},
        {'guid': 77947, 'label': 'Marble Road'},
        {'guid': 3087,  'label': 'Res.'},
        {'guid': 3310,  'label': 'WH'},
        {'infra_cat_guid': 41370, 'label': 'Watch'},   # City Watch category
    ],
    'Celtic': [
        {'guid': 24355, 'label': 'Road'},
        {'guid': 24357, 'label': 'Paved Road'},
        {'guid': 77948, 'label': 'Marble Road'},
        {'guid': 6414,  'label': 'Res.'},
        {'guid': 7055,  'label': 'WH'},
        {'infra_cat_guid': 41341, 'label': 'Watch'},   # City Watch category
    ],
}


class IconButton(tk.Canvas):
    """Square canvas button with icon image or text abbreviation."""

    def __init__(self, master, size=ICON_STRIP_SIZE, label='', icon=None, tooltip='', color=BG_SECTION, selected=False, command=None, has_popup=False, **kwargs):
        super().__init__(master, width=size, height=size, bg=BG_MAIN, highlightthickness=0, **kwargs)
        self.size       = size
        self.label      = label
        self.icon_img   = icon
        self.tooltip    = tooltip
        self.base_color = color
        self._selected  = selected
        self._has_popup = has_popup
        self.command    = command
        self._hover     = False
        self._draw()
        self.bind('<Enter>',            self._on_enter)
        self.bind('<Leave>',            self._on_leave)
        self.bind('<ButtonRelease-1>',  self._on_release)

    def _draw(self):
        self.delete('all')
        s = self.size
        if self._selected:
            bg, outline = BG_SELECTED, BORDER_GOLD
        elif self._hover:
            bg, outline = BG_HOVER, BORDER_GOLD
        else:
            bg, outline = self.base_color, BORDER_COLOR
        self.create_rectangle(1, 1, s - 1, s - 1, fill=bg, outline=outline, width=1)
        if self.icon_img:
            self.create_image(s // 2, s // 2, image=self.icon_img, anchor='center')
        else:
            abbrev = self.label[:3] if self.label else '?'
            self.create_text(s // 2, s // 2, text=abbrev, fill=FG_GOLD if self._selected else FG_MAIN, font=FONT_XSMALL, anchor='center')
        if self._selected:
            self.create_rectangle(1, 1, s - 1, s - 1, fill='', outline=FG_GOLD, width=1)
            self.create_rectangle(2, s - 4, s - 2, s - 1, fill=FG_GOLD, outline='')

    def set_selected(self, val: bool):
        self._selected = val
        self._draw()

    def _on_enter(self, e):
        if self.command:
            self._hover = True
            self._draw()
        if self.tooltip:
            self._show_tooltip()

    def _on_leave(self, e):
        if self.command:
            self._hover = False
            self._draw()
        self._hide_tooltip()

    def _on_release(self, e):
        if self.command:
            self.command()

    _tooltip_win = None

    def _show_tooltip(self):
        IconButton._hide_tooltip(self)
        x = self.winfo_rootx() + self.size // 2
        y = self.winfo_rooty() - 28
        win = tk.Toplevel(self)
        win.wm_overrideredirect(True)
        win.wm_geometry(f'+{x}+{y}')
        win.config(bg=BG_SECTION)
        tk.Label(win, text=self.tooltip, bg=BG_SECTION, fg=FG_GOLD, font=FONT_XSMALL, padx=6, pady=3, relief=tk.SOLID, bd=1).pack()
        IconButton._tooltip_win = win

    def _hide_tooltip(self):
        if IconButton._tooltip_win:
            try:
                IconButton._tooltip_win.destroy()
            except Exception:
                pass
            IconButton._tooltip_win = None


# ─────────────────────────────────────────────────────────────────────────────
#  MenuButton  (text tab)
# ─────────────────────────────────────────────────────────────────────────────
class MenuButton(tk.Frame):
    """Styled navigation tab - optionally shows a small icon to the left of the text."""

    def __init__(self, master, text: str, icon=None, command=None, wraplength=0, **kwargs):
        super().__init__(master, bg=BG_SECTION, highlightbackground=BORDER_COLOR, highlightthickness=1, cursor='hand2', **kwargs)
        self._selected  = False
        self._command   = command
        self._icon_lbl  = None

        if icon is not None:
            self._icon_lbl = tk.Label(self, image=icon, bg=BG_SECTION, cursor='hand2', padx=2)
            self._icon_lbl.image = icon   # prevent GC
            self._icon_lbl.pack(side=tk.LEFT, padx=(4, 0))

        lbl_kw = dict(bg=BG_SECTION, fg=FG_MAIN, font=FONT_BOLD_SMALL, padx=8, pady=4, cursor='hand2', justify='left')
        if wraplength:
            lbl_kw['wraplength'] = wraplength
        self._lbl = tk.Label(self, text=text, **lbl_kw)
        self._lbl.pack(side=tk.LEFT)

        bindable = [self, self._lbl]
        if self._icon_lbl:
            bindable.append(self._icon_lbl)
        for w in bindable:
            w.bind('<Enter>',    self._on_enter)
            w.bind('<Leave>',    self._on_leave)
            w.bind('<Button-1>', self._on_click)

    def set_selected(self, val: bool):
        self._selected = val
        if val:
            self._lbl.config(bg=BG_SELECTED, fg=FG_GOLD)
            self.config(bg=FG_GOLD, highlightbackground=BORDER_GOLD)
            if self._icon_lbl:
                self._icon_lbl.config(bg=BG_SELECTED)
        else:
            self._lbl.config(bg=BG_SECTION, fg=FG_MAIN)
            self.config(bg=BG_SECTION, highlightbackground=BORDER_COLOR)
            if self._icon_lbl:
                self._icon_lbl.config(bg=BG_SECTION)

    def set_disabled(self, val: bool):
        self._disabled = val
        cur = 'arrow' if val else 'hand2'
        fg  = FG_DIM   if val else FG_MAIN
        self._lbl.config(fg=fg, cursor=cur)
        self.config(cursor=cur)
        if self._icon_lbl:
            self._icon_lbl.config(cursor=cur)

    def _on_enter(self, e):
        if not self._selected and not getattr(self, '_disabled', False):
            self._lbl.config(bg=BG_HOVER, fg=FG_GOLD)
            self.config(highlightbackground=BORDER_GOLD)
            if self._icon_lbl:
                self._icon_lbl.config(bg=BG_HOVER)

    def _on_leave(self, e):
        if not self._selected and not getattr(self, '_disabled', False):
            self._lbl.config(bg=BG_SECTION, fg=FG_MAIN)
            self.config(highlightbackground=BORDER_COLOR)
            if self._icon_lbl:
                self._icon_lbl.config(bg=BG_SECTION)

    def _on_click(self, e):
        if self._command and not getattr(self, '_disabled', False):
            self._command()


# ─────────────────────────────────────────────────────────────────────────────
#  DetailPopup
# ─────────────────────────────────────────────────────────────────────────────
class DetailPopup(tk.Frame):
    """
    Popover showing chain or category detail.

    Production chain layout (grid, first branch at bottom):
        Tier N  →  Tier N-1  →  ...  →  Tier 0 (centred, rowspan)

    Category layout: icons left-to-right.
      • building         → click selects it (on_select_building)
      • production_chain → end-product icon; click opens chain popup (on_open_item)
      • category         → category icon;    click drills into it   (on_open_item)
    """

    def __init__(self, master, app, item: dict, lang: str, on_select_building: Callable, on_open_item: Optional[Callable] = None, **kwargs):
        super().__init__(master, bg=BG_SECTION, highlightbackground=BORDER_GOLD, highlightthickness=1, **kwargs)
        self.app               = app
        self.item              = item
        self.lang              = lang
        self.on_select_building = on_select_building
        self.on_open_item       = on_open_item
        self.dm                = get_data_manager()
        self._build()

    # ── scaffold ──────────────────────────────────────────────────────────

    def _build(self):
        item_type = self.item.get('type', 'building')

        # Title
        hdr = tk.Frame(self, bg=BG_SECTION)
        hdr.pack(fill=tk.X, padx=8, pady=(6, 2))
        tk.Label(hdr, text=self._title(), bg=BG_SECTION, fg=FG_GOLD, font=FONT_BOLD_SMALL).pack(side=tk.LEFT)
        tk.Frame(self, height=1, bg=BORDER_COLOR).pack(fill=tk.X, padx=6)

        if item_type == 'production_chain':
            inner = tk.Frame(self, bg=BG_SECTION)
            inner.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 2))
            self._build_chain(inner)

        elif item_type == 'category':
            # scroll_outer fills the full popup interior; canvas expands to fill it.
            # Scrollbar reserved at the bottom; only shown when content overflows.
            scroll_outer = tk.Frame(self, bg=BG_SECTION)
            scroll_outer.pack(fill=tk.BOTH, expand=True, padx=4, pady=(4, 2))

            hbar = tk.Scrollbar(scroll_outer, orient=tk.HORIZONTAL)
            # packed lazily below

            cat_canvas = tk.Canvas(scroll_outer, bg=BG_SECTION, highlightthickness=0)
            cat_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
            cat_canvas.configure(xscrollcommand=hbar.set)
            hbar.configure(command=cat_canvas.xview)

            inner = tk.Frame(cat_canvas, bg=BG_SECTION)
            win_id = cat_canvas.create_window((0, 0), window=inner, anchor='nw')

            def _sync(e=None, _c=cat_canvas, _wid=win_id, _hb=hbar):
                _c.update_idletasks()
                req_w  = inner.winfo_reqwidth()
                req_h  = inner.winfo_reqheight()
                cw     = max(_c.winfo_width(),  1)
                ch     = max(_c.winfo_height(), 1)
                _c.itemconfigure(_wid, width=max(req_w, cw), height=max(req_h, ch))
                _c.configure(scrollregion=(0, 0, req_w, req_h))
                needs = req_w > cw
                if needs and not _hb.winfo_ismapped():
                    _hb.pack(side=tk.BOTTOM, fill=tk.X, before=_c)
                elif not needs and _hb.winfo_ismapped():
                    _hb.pack_forget()

            inner.bind('<Configure>', _sync)
            cat_canvas.bind('<Configure>', _sync)

            def _on_wheel(e, _c=cat_canvas):
                _c.xview_scroll(-1 if e.delta > 0 else 1, 'units')
            cat_canvas.bind('<MouseWheel>', _on_wheel)
            inner.bind('<MouseWheel>', _on_wheel)

            self._build_category(inner)

    # ── chain tree parsing (post-order JSON → tree of input connections) ──────

    @staticmethod
    def _parse_chain_tree(buildings):
        """
        The buildings list is a post-order traversal of the production tree
        (leaves before parents, higher tier = deeper leaf).
        Recover the tree by stacking: when a node at tier T is processed,
        pop all stack nodes whose tier > T - those are its direct inputs.
        Returns a list of root nodes (typically just the tier-0 building).
        Each node is a dict with 'guid', 'tier', 'inputs', and 'parent' keys.
        """
        stack = []
        for b in buildings:
            node = {'guid': b.get('guid'), 'tier': b.get('tier', 0),
                    'inputs': [], 'parent': None}
            inputs = []
            while stack and stack[-1]['tier'] > node['tier']:
                child = stack.pop()
                child['parent'] = node
                inputs.insert(0, child)   # preserve JSON order
            node['inputs'] = inputs
            stack.append(node)
        return stack   # remaining nodes are roots (usually just the tier-0 building)

    @staticmethod
    def _leaf_count(node):
        """Number of leaves in this subtree (= grid rows it occupies)."""
        if not node['inputs']:
            return 1
        return sum(DetailPopup._leaf_count(c) for c in node['inputs'])

    @staticmethod
    def _assign_rows(node, start, out):
        """DFS row assignment: node occupies [start, start+leaf_count)."""
        lc = DetailPopup._leaf_count(node)
        out[id(node)] = (start, start + lc)
        cur = start
        for child in node['inputs']:
            DetailPopup._assign_rows(child, cur, out)
            cur += DetailPopup._leaf_count(child)

    # ── chain layout (tree-aware multi-row grid) ───────────────────────────

    def _build_chain(self, parent):
        buildings = self.item.get('buildings', [])
        if not buildings:
            return

        roots = self._parse_chain_tree(buildings)
        if not roots:
            return

        # Assign rows to every node
        row_map = {}   # id(node) -> (row_start, row_end_exclusive)
        cur = 0
        for root in roots:
            self._assign_rows(root, cur, row_map)
            cur += self._leaf_count(root)
        n_rows = cur

        # Collect all nodes and determine column from tier
        all_nodes = []
        def _collect(node):
            all_nodes.append(node)
            for c in node['inputs']:
                _collect(c)
        for root in roots:
            _collect(root)

        if not all_nodes:
            return

        max_tier = max(n['tier'] for n in all_nodes)
        # Higher tier (deeper leaf) = further left.  Tier T → grid column (max_tier-T)*2.
        def tier_col(t):
            return (max_tier - t) * 2

        def get_desired_center(node):
            """Icon centre in grid-pixels from grid top.
            Leaves: natural slot midpoint.  Non-leaves: mean of inputs' centres, so parent icons align visually between their children."""
            row_start, row_end = row_map[id(node)]
            N = row_end - row_start
            if not node['inputs']:
                return (row_start + N / 2) * DETAIL_ROW_H
            centers = [get_desired_center(inp) for inp in node['inputs']]
            return sum(centers) / len(centers)

        # Place building cells (show disabled placeholder for missing GUIDs)
        for node in all_nodes:
            bd = self.dm.get_building(node['guid'])
            row_start, row_end = row_map[id(node)]
            rowspan = row_end - row_start
            col = tier_col(node['tier'])
            cell = tk.Frame(parent, bg=BG_SECTION)
            cell.grid(row=row_start, column=col, rowspan=rowspan, padx=4, pady=4, sticky='nsew')
            # rely: fraction of cell height at which the icon centre should sit
            desired_px = get_desired_center(node)
            cell_h = rowspan * DETAIL_ROW_H - 8  # slot height minus 2×pady
            rely = (desired_px - row_start * DETAIL_ROW_H - 4) / cell_h if cell_h > 0 else 0.5
            if bd:
                self._building_cell(cell, bd, select_on_click=True, rely=rely)
            else:
                # Placeholder for unknown/future GUIDs
                btn_ph = IconButton(cell, size=DETAIL_ICON_SIZE, icon=None, label='?', color=BG_MAIN)
                btn_ph.place(relx=0.5, rely=rely, anchor='center')
                tk.Label(cell, text=f'#{node["guid"]}', bg=BG_SECTION, fg=FG_DIM, font=FONT_XSMALL, wraplength=80, justify='center').place(relx=0.5, rely=rely, anchor='n', y=DETAIL_ICON_SIZE // 2 + 2)

        # Arrows: pady=0 centres at N×ROW_H/2, matching btn.place(rely=0.5).
        for node in all_nodes:
            if node['tier'] == 0:
                continue
            row_start, row_end = row_map[id(node)]
            col  = tier_col(node['tier'])
            acol = col + 1
            rowspan = row_end - row_start
            tk.Label(parent, text='→', bg=BG_SECTION, fg=FG_GOLD, font=FONT_SMALL).grid(row=row_start, column=acol, rowspan=rowspan, padx=2, pady=0)

        # Building columns need an explicit minsize - place() children don't
        # contribute to reqwidth, so without this the columns collapse.
        for col in range(0, max_tier * 2 + 1, 2):
            parent.grid_columnconfigure(col, minsize=90)
        for row in range(n_rows):
            parent.grid_rowconfigure(row, weight=1, minsize=DETAIL_ROW_H)

    # ── category layout (flat horizontal) ────────────────────────────────

    def _build_category(self, parent):
        items = self.item.get('items', [])
        _IND_FONT = ('TkDefaultFont', 7)
        for sub in items:
            sub_type  = sub.get('type', 'building')
            has_popup = sub_type in ('production_chain', 'category')

            cell = tk.Frame(parent, bg=BG_SECTION)
            cell.pack(side=tk.LEFT, padx=3, pady=(4, 8))

            # Uniform indicator slot - same height for every item so icons align
            tk.Label(cell, text='▲' if has_popup else '', bg=BG_SECTION, fg=FG_GOLD if has_popup else BG_SECTION, font=_IND_FONT).pack(side=tk.TOP)

            if sub_type == 'building':
                bd = self.dm.get_building(sub.get('guid'))
                if not bd:
                    continue
                name = bd.get_name(self.lang)
                icon = _load_building_icon(bd, DETAIL_ICON_SIZE)
                guid = bd.guid
                cmd  = (lambda g=guid: self.on_select_building(g)) if bd.is_placeable() else lambda: None
                btn  = IconButton(cell, size=DETAIL_ICON_SIZE, icon=icon, tooltip=name, color=BG_SECTION, command=cmd)
                btn.pack()

            elif sub_type == 'production_chain':
                buildings = sub.get('buildings', [])
                tier0 = min(buildings, key=lambda b: b.get('tier', 99), default=None)
                if not tier0:
                    continue
                bd = self.dm.get_building(tier0.get('guid'))
                if not bd:
                    continue
                name      = self._loc(sub.get('name', {}))
                icon      = _load_building_icon(bd, DETAIL_ICON_SIZE)
                local_sub = sub
                btn = IconButton(cell, size=DETAIL_ICON_SIZE, icon=icon, tooltip=name, color=BG_SECTION, command=None)
                btn.pack()
                btn.command = lambda it=local_sub: self._drill(it)

            elif sub_type == 'category':
                icon_path = sub.get('icon', '')
                icon      = _load_icon_from_path(icon_path, DETAIL_ICON_SIZE)
                if icon is None:
                    icon = _first_icon_in_items(sub.get('items', []), self.dm, DETAIL_ICON_SIZE)
                name      = self._loc(sub.get('name', {}))
                local_sub = sub
                btn = IconButton(cell, size=DETAIL_ICON_SIZE, icon=icon, tooltip=name, color=BG_SECTION, command=None)
                btn.pack()
                btn.command = lambda it=local_sub: self._drill(it)

    # ── helpers ───────────────────────────────────────────────────────────

    def _building_cell(self, cell: tk.Frame, bd: BuildingData, select_on_click: bool, rely: float = 0.5):
        name = bd.get_name(self.lang)
        icon = _load_building_icon(bd, DETAIL_ICON_SIZE)
        guid = bd.guid
        # Place btn and label directly - no inner frame, so cell reqwidth is
        # determined by grid_columnconfigure(minsize=90) rather than children.
        # rely=0.5 for leaves (icon centre = N×ROW_H/2 = arrow centre with pady=0).
        # rely<0.5 for non-leaf nodes shifts the icon to the mean of its inputs.
        placeable = bd.is_placeable()
        btn = IconButton(cell, size=DETAIL_ICON_SIZE, icon=icon, tooltip=name, color=BG_SECTION, command=(lambda g=guid: self.on_select_building(g)) if select_on_click and placeable else lambda: None)
        btn.place(relx=0.5, rely=rely, anchor='center')

    def _drill(self, item: dict):
        """Delegate chain/category clicks to the host build menu, passing self as parent."""
        if self.on_open_item:
            self.on_open_item(item, self)

    def _title(self) -> str:
        name = self.item.get('name', {})
        return (name.get(self.lang) or name.get('english', '')) \
            if isinstance(name, dict) else str(name)

    def _loc(self, name) -> str:
        if isinstance(name, dict):
            return name.get(self.lang) or name.get('english', '')
        return str(name)

    # ── sizing helpers (used by BuildMenu before placing) ─────────────────

    def chain_rows(self) -> int:
        buildings = self.item.get('buildings', [])
        if not buildings:
            return 1
        roots = self._parse_chain_tree(buildings)
        return sum(self._leaf_count(r) for r in roots)


# ─────────────────────────────────────────────────────────────────────────────
#  BuildMenu
# ─────────────────────────────────────────────────────────────────────────────
class BuildMenu(tk.Frame):
    """
    Two-row build menu bar matching the in-game style.
    Row 1 (TOP):    Scrollable icon strip.
    Row 2 (BOTTOM): Region tabs | Tier tabs | Infra/Materials/Ornaments | Delete/Clear
    """

    def __init__(self, master, app, **kwargs):
        kwargs.pop('height', None)
        total_h = MENU_STRIP_H + MENU_NAV_H + 3   # +gold border +separator
        super().__init__(master, bg=BG_MAIN, height=total_h, **kwargs)
        self.pack_propagate(False)

        self.app = app
        self.dm  = get_data_manager()

        self._current_region: str = None
        # Stack: each entry is (DetailPopup, item_id, pos_tuple(x,y,w,h))
        self._popup_stack: list = []

        self._region_btns: dict = {}
        self._tier_btns: dict   = {}
        self._fixed_btns: list  = []
        self._scrollbar_check_id = None

        self._build_ui()
        # Bind canvas right-click to close all popups after app is fully built
        self.after(200, self._bind_canvas_close)

    # ── UI construction ────────────────────────────────────────────────────

    def _build_ui(self):
        # Gold top border
        tk.Frame(self, bg=BORDER_GOLD, height=1).pack(fill=tk.X, side=tk.TOP)

        # ── Row 1 (TOP): Icon strip ────────────────────────────────────────
        row1 = tk.Frame(self, bg=BG_SECTION, height=MENU_STRIP_H)
        row1.pack(fill=tk.X, side=tk.TOP)
        row1.pack_propagate(False)

        # Canvas for horizontally-scrollable strip
        self._strip_canvas = tk.Canvas(row1, bg=BG_SECTION, highlightthickness=0)
        self._strip_canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # Horizontal scrollbar - packed only when content overflows
        self._strip_scrollbar = tk.Scrollbar(row1, orient=tk.HORIZONTAL, command=self._strip_canvas.xview)
        self._strip_canvas.configure(xscrollcommand=self._strip_scrollbar.set)

        # Inner frame for icon wrappers (same API as before)
        self._strip_inner = tk.Frame(self._strip_canvas, bg=BG_SECTION)
        self._strip_win_id = self._strip_canvas.create_window(
            (0, 0), window=self._strip_inner, anchor='nw')

        def _on_strip_configure(e=None):
            self._strip_canvas.itemconfigure(self._strip_win_id, height=self._strip_canvas.winfo_height())
            # Re-evaluate scrollbar need after geometry settles
            if self._scrollbar_check_id is not None:
                self.after_cancel(self._scrollbar_check_id)
            self._scrollbar_check_id = self.after(50, self._update_strip_scrollbar)

        self._strip_inner.bind('<Configure>', _on_strip_configure)
        self._strip_canvas.bind('<Configure>', _on_strip_configure)

        # Shift+wheel → horizontal scroll (Windows)
        def _on_strip_hwheel(e):
            if e.num == 4 or e.delta > 0:
                self._strip_canvas.xview_scroll(-1, 'units')
            else:
                self._strip_canvas.xview_scroll(1, 'units')

        self._strip_canvas.bind('<Shift-MouseWheel>', _on_strip_hwheel)
        self._strip_inner.bind('<Shift-MouseWheel>', _on_strip_hwheel)

        # Row separator
        tk.Frame(self, bg=BORDER_COLOR, height=1).pack(fill=tk.X)

        # ── Row 2 (BOTTOM): Navigation tabs ───────────────────────────────
        # Order: Region | Quick+Materials | Infra | Ornaments | Tiers
        row2 = tk.Frame(self, bg=BG_SECTION, height=MENU_NAV_H)
        row2.pack(fill=tk.X, side=tk.TOP)
        row2.pack_propagate(False)

        # Region tabs
        rf = tk.Frame(row2, bg=BG_SECTION)
        rf.pack(side=tk.LEFT, padx=4, pady=4)
        for region in self.dm.get_regions():
            display = REGION_DISPLAY.get(region, region)
            btn = MenuButton(rf, text=display, command=lambda r=region: self._select_region(r))
            btn.pack(side=tk.LEFT, padx=2)
            self._region_btns[region] = btn

        tk.Frame(row2, width=1, bg=BORDER_COLOR).pack(side=tk.LEFT, fill=tk.Y, pady=4)

        # Quick-access buttons (road / residence / warehouse - rebuilt per region)
        self._quick_frame = tk.Frame(row2, bg=BG_SECTION)
        self._quick_frame.pack(side=tk.LEFT, padx=(4, 2), pady=4)

        # Materials icon button - fixed, adjacent to quick-access buttons
        self._materials_btn = IconButton(
            row2, size=QUICK_BTN_SIZE,
            icon=_load_icon_from_path(
                'data/ui/fhd/base/icon_content/building/'
                'icon_3d_construction_category_materials.png', QUICK_BTN_SIZE),
            tooltip="Materials", color=BG_SECTION, command=self._show_materials)
        self._materials_btn.pack(side=tk.LEFT, padx=(0, 4), pady=4)

        tk.Frame(row2, width=1, bg=BORDER_COLOR).pack(side=tk.LEFT, fill=tk.Y, pady=4)

        # Infrastructure icon button
        self._infra_btn = IconButton(
            row2, size=FIXED_BTN_SIZE,
            icon=_load_icon_from_path(
                'data/ui/fhd/base/icon_content/building/'
                'icon_3d_construction_main_group_construction.png', FIXED_BTN_SIZE),
            tooltip="Infrastructure", color=BG_SECTION, command=self._show_infrastructure)
        self._infra_btn.pack(side=tk.LEFT, padx=2, pady=4)

        # Ornaments icon button
        self._ornaments_btn = IconButton(
            row2, size=FIXED_BTN_SIZE,
            icon=_load_icon_from_path(
                'data/ui/fhd/base/icon_content/building/'
                'icon_3d_construction_main_group_ornaments.png', FIXED_BTN_SIZE),
            tooltip="Ornaments", color=BG_SECTION, command=self._show_ornaments)
        self._ornaments_btn.pack(side=tk.LEFT, padx=2, pady=4)

        tk.Frame(row2, width=1, bg=BORDER_COLOR).pack(side=tk.LEFT, fill=tk.Y, pady=4)

        # Tier tabs (rebuilt per region)
        self._tier_frame = tk.Frame(row2, bg=BG_SECTION)
        self._tier_frame.pack(side=tk.LEFT, padx=4, pady=4)

        self._fixed_btns = [self._materials_btn, self._infra_btn, self._ornaments_btn]

        # Boot with first region
        regions = self.dm.get_regions()
        if regions:
            self._select_region(regions[0])

    # ── Canvas right-click → close all popups ──────────────────────────────

    def _bind_canvas_close(self):
        cw = getattr(self.app, 'canvas_widget', None)
        if cw and hasattr(cw, 'canvas'):
            cw.canvas.bind('<ButtonPress-3>', lambda e: self._close_all_popups(), add='+')

    # ── Region / tier navigation ───────────────────────────────────────────

    def _select_region(self, region: str):
        for r, btn in self._region_btns.items():
            btn.set_selected(r == region)
        self._current_region = region
        self._close_all_popups()
        self._rebuild_tiers(region)

    def _rebuild_quick_buttons(self, region: str):
        for w in self._quick_frame.winfo_children():
            w.destroy()
        for entry in QUICK_ACCESS.get(region, []):
            cat_guid = entry.get('infra_cat_guid')
            if cat_guid is not None:
                # Infrastructure-category entry - show a popup like the strip does.
                infra    = self.dm.get_menu_section(region, 'infrastructure')
                cat_item = next((c for c in infra.get('items', [])
                                 if c.get('guid') == cat_guid
                                 and c.get('type') == 'category'), None)
                if not cat_item:
                    continue
                icon = _first_icon_in_items(cat_item.get('items', []),
                                            self.dm, QUICK_BTN_SIZE)
                name = self._loc(cat_item.get('name', {}))
                wrapper = tk.Frame(self._quick_frame, bg=BG_SECTION)
                wrapper.pack(side=tk.LEFT, padx=2)
                btn = IconButton(wrapper, size=QUICK_BTN_SIZE, icon=icon, tooltip=name, color=BG_SECTION, command=None, has_popup=True)
                btn.pack()
                btn.command = lambda c=cat_item, w=wrapper: self._toggle_detail_popup(c, w)
                continue

            guid = entry.get('guid')
            bd   = self.dm.get_building(guid)
            if not bd:
                continue
            name = bd.get_name(self.app.language)
            icon = _load_building_icon(bd, QUICK_BTN_SIZE)
            btn  = IconButton(self._quick_frame, size=QUICK_BTN_SIZE, icon=icon, tooltip=name, color=BG_SECTION, command=lambda g=guid: self._on_building_selected(g))
            btn.pack(side=tk.LEFT, padx=2)

    def _rebuild_tiers(self, region: str):
        self._rebuild_quick_buttons(region)
        for w in self._tier_frame.winfo_children():
            w.destroy()
        self._tier_btns.clear()
        for btn in self._fixed_btns:
            btn.set_selected(False)

        tiers = self.dm.get_tiers_for_region(region)
        first = None
        for tier in tiers:
            name      = self._loc(tier.get('name', {}))
            icon_path = tier.get('icon', '')
            icon      = _load_icon_from_path(icon_path, NAV_ICON_SIZE) if icon_path else None

            def _cmd(t=tier, n=name):
                self._show_tier(t, n)

            btn = MenuButton(self._tier_frame, text=name, icon=icon, command=_cmd)
            btn.pack(side=tk.LEFT, padx=2)
            self._tier_btns[name] = btn
            if first is None:
                first = (tier, name)

        if first:
            self._show_tier(*first)

    def _show_tier(self, tier: dict, tier_name: str):
        for n, btn in self._tier_btns.items():
            btn.set_selected(n == tier_name)
        for btn in self._fixed_btns:
            btn.set_selected(False)
        self._close_all_popups()
        self._fill_strip(tier.get('items', []))

    # ── Fixed tab handlers ─────────────────────────────────────────────────

    def _show_infrastructure(self):
        self._activate_fixed(self._infra_btn)
        section = self.dm.get_menu_section(self._current_region or '', 'infrastructure')
        self._fill_strip(section.get('items', []))

    def _show_materials(self):
        self._activate_fixed(self._materials_btn)
        section = self.dm.get_menu_section(self._current_region or '', 'materials')
        self._fill_strip(section.get('items', []))

    def _show_ornaments(self):
        self._activate_fixed(self._ornaments_btn)
        section = self.dm.get_menu_section(self._current_region or '', 'ornaments')
        self._fill_strip(section.get('items', []))

    def _activate_fixed(self, active: MenuButton):
        for btn in self._tier_btns.values():
            btn.set_selected(False)
        for btn in self._fixed_btns:
            btn.set_selected(btn is active)
        self._close_all_popups()

    # ── Icon strip population ──────────────────────────────────────────────

    def _fill_strip(self, items: list):
        self._strip_canvas.xview_moveto(0)
        for w in self._strip_inner.winfo_children():
            w.destroy()
        for item in items:
            t = item.get('type', 'building')
            if t == 'building':
                self._add_building_icon(item)
            elif t == 'production_chain':
                self._add_chain_icon(item)
            elif t == 'category':
                self._add_category_icon(item)
        # Cancel any pending check and schedule a fresh one after geometry settles
        if self._scrollbar_check_id is not None:
            self.after_cancel(self._scrollbar_check_id)
        self._scrollbar_check_id = self.after(50, self._update_strip_scrollbar)

    def _update_strip_scrollbar(self):
        """Show the horizontal scrollbar only when strip icons overflow the canvas width."""
        self._scrollbar_check_id = None
        self.update_idletasks()
        canvas_w = self._strip_canvas.winfo_width()
        if canvas_w <= 1:
            self._scrollbar_check_id = self.after(100, self._update_strip_scrollbar)
            return
        # Sum children directly - more reliable than winfo_reqwidth() for a
        # canvas-embedded frame whose width may be clamped to the viewport.
        children = self._strip_inner.winfo_children()
        if children:
            content_w = sum(c.winfo_reqwidth() for c in children) + 4
        else:
            content_w = 0
        content_h = self._strip_canvas.winfo_height()
        # Force the canvas window to its full content width so the scrollregion
        # is accurate even when the canvas viewport is narrower.
        self._strip_canvas.itemconfigure(
            self._strip_win_id, width=max(content_w, canvas_w))
        self._strip_canvas.configure(
            scrollregion=(0, 0, content_w, content_h))
        needs_scroll = content_w > canvas_w
        is_mapped    = self._strip_scrollbar.winfo_ismapped()
        if needs_scroll and not is_mapped:
            self._strip_scrollbar.pack(side=tk.BOTTOM, fill=tk.X)
        elif not needs_scroll and is_mapped:
            self._strip_scrollbar.pack_forget()

    def _make_strip_wrapper(self, has_indicator: bool) -> tuple:
        """Return (wrapper, ind_label). Both building and popup icons use the same
        layout so all icons sit at the same vertical position in the strip."""
        wrapper = tk.Frame(self._strip_inner, bg=BG_SECTION)
        wrapper.pack(side=tk.LEFT, padx=2, pady=2)
        # Indicator slot - always same height; text only shown for popup icons
        ind = tk.Label(wrapper, text='▲' if has_indicator else '', bg=BG_SECTION, fg=FG_GOLD if has_indicator else BG_SECTION, font=('TkDefaultFont', 7))
        ind.pack(side=tk.TOP)
        return wrapper, ind

    def _add_building_icon(self, item: dict):
        guid = item.get('guid')
        bd   = self.dm.get_building(guid)
        name = bd.get_name(self.app.language) if bd else f'#{guid}'
        icon = _load_building_icon(bd, ICON_STRIP_SIZE) if bd else None
        wrapper, _ = self._make_strip_wrapper(has_indicator=False)
        if bd:
            btn = IconButton(wrapper, size=ICON_STRIP_SIZE, icon=icon, tooltip=name, color=BG_SECTION, command=lambda g=guid: self._on_building_selected(g))
        else:
            # No building data - show greyed-out, non-clickable placeholder
            btn = IconButton(wrapper, size=ICON_STRIP_SIZE, icon=icon, tooltip=name, color=BG_MAIN, command=None)
        btn.pack(side=tk.TOP)

    def _add_chain_icon(self, item: dict):
        """Show the end-product (tier-0) icon for a production chain."""
        buildings = item.get('buildings', [])
        if not buildings:
            return
        tier0 = min(buildings, key=lambda b: b.get('tier', 99))
        bd    = self.dm.get_building(tier0.get('guid'))
        if not bd:
            return
        name    = self._loc(item.get('name', {})) or bd.get_name(self.app.language)
        icon    = _load_building_icon(bd, ICON_STRIP_SIZE)
        wrapper, ind = self._make_strip_wrapper(has_indicator=True)
        btn = IconButton(wrapper, size=ICON_STRIP_SIZE, icon=icon, tooltip=name, color=BG_SECTION, command=None)
        btn.pack(side=tk.TOP)
        btn.command = lambda i=item, w=wrapper: self._toggle_detail_popup(i, w)
        ind.bind('<Button-1>', lambda e, i=item, w=wrapper: self._toggle_detail_popup(i, w))

    def _add_category_icon(self, item: dict):
        """Show the category's own icon (from construction-menu 'icon' field)."""
        icon_path = item.get('icon', '')
        icon      = _load_icon_from_path(icon_path, ICON_STRIP_SIZE)
        if icon is None:
            icon = _first_icon_in_items(item.get('items', []), self.dm, ICON_STRIP_SIZE)
        name    = self._loc(item.get('name', {}))
        wrapper, ind = self._make_strip_wrapper(has_indicator=True)
        btn = IconButton(wrapper, size=ICON_STRIP_SIZE, icon=icon, tooltip=name, color=BG_SECTION, command=None)
        btn.pack(side=tk.TOP)
        btn.command = lambda i=item, w=wrapper: self._toggle_detail_popup(i, w)
        ind.bind('<Button-1>', lambda e, i=item, w=wrapper: self._toggle_detail_popup(i, w))

    # ── Detail popup stack ─────────────────────────────────────────────────

    def _toggle_detail_popup(self, item: dict, anchor: IconButton):
        """Open popup for item from the icon strip, or close it if already the base popup."""
        item_id = id(item)
        # If this item is already the root (bottom) of the stack → close everything
        if self._popup_stack and self._popup_stack[0][1] == item_id:
            self._close_all_popups()
            return
        self._close_all_popups()
        self._open_popup_for_item(item, anchor=anchor, parent_entry=None)

    def _open_popup_for_item(self, item: dict, anchor: Optional[IconButton], parent_entry: Optional[tuple]):
        """
        Create and place a DetailPopup.
        parent_entry: a stack entry (popup, item_id, pos) to stack above, or None.
        """
        popup = DetailPopup(
            self.master, self.app,
            item=item,
            lang=self.app.language,
            on_select_building=self._on_building_selected,
            on_open_item=self._on_open_item_from_popup,
        )

        # Height - chain uses per-row constant; categories use fixed content height
        popup.update_idletasks()
        if item.get('type') == 'production_chain':
            n_rows = popup.chain_rows()
            popup_h = DETAIL_TITLE_H + n_rows * DETAIL_ROW_H + DETAIL_PAD_H
        else:
            popup_h = DETAIL_TITLE_H + DETAIL_CATEGORY_ROW_H + DETAIL_CATEGORY_SCROLL_H + DETAIL_PAD_H

        # Width - chains fit to content; categories expand to fill available space
        master_w = self.master.winfo_width()
        if item.get('type') == 'production_chain':
            req_w = popup.winfo_reqwidth() + 20
            popup_w = max(240, min(req_w, master_w - 10))
        else:
            popup_w = max(240, master_w - 10)

        self.update_idletasks()

        if parent_entry is not None:
            # Stack this popup directly above the parent
            _, _, (par_x, par_y, par_w, par_h) = parent_entry
            popup_bottom = par_y             # new popup's bottom = parent's top
            bx    = par_x
            btn_w = par_w
        elif anchor is not None:
            # anchor is the wrapper frame - its top is the top of the ▲ indicator
            bx    = anchor.winfo_rootx() - self.master.winfo_rootx()
            by    = anchor.winfo_rooty() - self.master.winfo_rooty()
            btn_w = anchor.winfo_width()
            popup_bottom = by - 4   # 4px gap above the ▲ indicator
        else:
            popup_bottom = 0
            bx    = 0
            btn_w = popup_w

        px = max(0, min(bx + btn_w // 2 - popup_w // 2, master_w - popup_w))
        py = max(0, popup_bottom - popup_h)

        popup.place(x=px, y=py, width=popup_w, height=popup_h)
        self._popup_stack.append((popup, id(item), (px, py, popup_w, popup_h)))

    def _on_open_item_from_popup(self, item: dict, parent_popup: 'DetailPopup'):
        """
        Called when a chain/category is clicked inside an open popup.
        Stacks the new popup above the parent; doesn't close the parent.
        """
        item_id = id(item)

        # Find parent in the stack
        parent_idx = next((i for i, e in enumerate(self._popup_stack)
                           if e[0] is parent_popup), None)

        if parent_idx is not None:
            # Close anything above the parent first (previous drilled level)
            while len(self._popup_stack) > parent_idx + 1:
                self._destroy_top()

        # If the item is now already at the top of stack → toggle it closed
        if self._popup_stack and self._popup_stack[-1][1] == item_id:
            self._destroy_top()
            return

        parent_entry = self._popup_stack[parent_idx] if parent_idx is not None else None
        self._open_popup_for_item(item, anchor=None, parent_entry=parent_entry)

    def _destroy_top(self):
        if self._popup_stack:
            try:
                self._popup_stack[-1][0].place_forget()
                self._popup_stack[-1][0].destroy()
            except Exception:
                pass
            self._popup_stack.pop()

    def _close_all_popups(self):
        while self._popup_stack:
            self._destroy_top()

    # ── Building selection ─────────────────────────────────────────────────

    def _on_building_selected(self, guid: int):
        self.app.canvas_widget.set_build_mode(guid)
        bd = self.dm.get_building(guid)
        if bd and hasattr(self.app, 'building_info_panel'):
            self.app.building_info_panel.show_building(bd, 0)

    # ── Helpers ────────────────────────────────────────────────────────────

    def _loc(self, name_dict) -> str:
        if isinstance(name_dict, dict):
            lang = getattr(self.app, 'language', 'english')
            return name_dict.get(lang) or name_dict.get('english', '?')
        return str(name_dict)

    def update_language(self):
        if self._current_region:
            self._select_region(self._current_region)
