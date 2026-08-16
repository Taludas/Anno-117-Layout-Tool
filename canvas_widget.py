"""
Anno 117 Layout Tool - Main Canvas Widget
2D grid with zoom/pan, building placement, selection, undo/redo.
"""
import tkinter as tk
from tkinter import messagebox
import base64
import math
import copy
import json
import os
import heapq
from typing import Optional, Callable

from config import (
    BG_MAIN, BG_SECTION, BG_HOVER, FG_MAIN, FG_DIM, FG_GOLD, FG_SEPARATOR,
    BORDER_COLOR, BORDER_GOLD,
    GRID_COLOR_90, GRID_COLOR_45,
    DEFAULT_TILE_SIZE, MIN_TILE_SIZE, MAX_TILE_SIZE, ZOOM_FACTOR,
    resource_path, FONT_SMALL, FONT_XSMALL,
)
from data_manager import BuildingData, get_data_manager, _get_45_grid_counts
from dialogs import save_settings

try:
    from PIL import Image, ImageTk, ImageDraw
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False

# ── Island tile categories (must match extract_islands.py) ───────────────────
_ISLE_SEA       = 0   # open sea / out of bounds
_ISLE_LAND      = 1   # non-buildable terrain (cliffs, mountains)
_ISLE_BUILDABLE = 2   # regular buildable land
_ISLE_HARBOUR   = 3   # buildable coastal water (harbour zone)
_ISLE_MARSH     = 4   # marsh / irrigation area (buildable)

# Background colours per tile type, keyed by light_mode bool
_ISLE_COLORS = {
    False: {  # dark theme
        _ISLE_SEA:       '#0a1628',   # deep navy
        _ISLE_LAND:      '#3c2a10',   # dark stone / brown
        _ISLE_BUILDABLE: '#1e3c1a',   # forest green
        _ISLE_HARBOUR:   '#0d2848',   # deep coastal blue
        _ISLE_MARSH:     '#3a4c10',   # olive-yellow (clearly distinct from green)
    },
    True: {   # light theme
        _ISLE_SEA:       '#4878b8',   # saturated blue
        _ISLE_LAND:      '#c0a050',   # warm sandy stone
        _ISLE_BUILDABLE: '#88c870',   # clear grass green
        _ISLE_HARBOUR:   '#60a0d0',   # coastal blue
        _ISLE_MARSH:     '#c8d838',   # yellow-green (clearly distinct from grass)
    },
}

# Tiles on which land buildings may be placed
_ISLE_BUILDABLE_TILES = {_ISLE_BUILDABLE, _ISLE_MARSH}

ROAD_FILL_COLORS = {
    'Dirt Road':        '#c8a870',
    'Paved Road':       '#707070',
    'Marble Road':      '#e8e6e0',
    'Aqueduct':         '#4a90c4',
    'Aqueduct Source':  '#5aaad4',
    'Aqueduct Cistern': '#3a80b4',
    'Drainage Channel': '#6a9080',
}
ROAD_FILL_DEFAULT = '#888888'

ROAD_PRIORITY = {
    'Dirt Road':   1,
    'Paved Road':  2,
    'Marble Road': 3,
}

# StreetDistance reach multiplier: paved/marble roads let a building's street-distance budget stretch 1.5x further per tile travelled along them.
ROAD_STREET_DISTANCE_MULTIPLIER = {
    'Paved Road':  1.5,
    'Marble Road': 1.5,
}
ROAD_STREET_DISTANCE_DEFAULT = 1.0  # Dirt Road and anything else: no bonus

def _road_street_distance_cost(bd: BuildingData) -> float:
    """Cost of stepping onto one tile of this road for StreetDistance BFS -
    the inverse of its reach multiplier, so a higher multiplier consumes less of the budget and lets the search travel further per tile."""
    multiplier = ROAD_STREET_DISTANCE_MULTIPLIER.get(bd.get_name('english'), ROAD_STREET_DISTANCE_DEFAULT)
    return 1.0 / multiplier

MODULE_CONFLICT_LIGHTEN_STEP = 0.30  # blend-toward-white amount per rank in a colour-conflict cluster
MODULE_CONFLICT_LIGHTEN_MAX  = 0.85  # cap so a large cluster doesn't wash out to white

def _lighten_color(hex_color: str, amount: float) -> str:
    """Blend a '#rrggbb' colour toward white by `amount` (0-1)."""
    h = hex_color.lstrip('#')
    if len(h) != 6:
        return hex_color
    try:
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    except ValueError:
        return hex_color
    r = int(r + (255 - r) * amount)
    g = int(g + (255 - g) * amount)
    b = int(b + (255 - b) * amount)
    return f'#{r:02x}{g:02x}{b:02x}'

# Infrastructure building names that behave like roads (drag-to-place,
# show-outline/icon). Only the Aqueduct Arch segment (named plain "Aqueduct",
# GUIDs 19723/29525) qualifies - Aqueduct Source and Aqueduct Cistern are
# large single-placement endpoint structures, not drag-drawn like a road.
_ROAD_LIKE_INFRA = frozenset({
    'Aqueduct', 'Drainage Channel',
})


def _is_road_like(bd: BuildingData) -> bool:
    """True for Road/OrnamentalRoad categories and aqueduct/drainage-channel buildings."""
    cat = bd.get_category_english()
    if 'Road' in cat:
        return True
    if cat == 'Infrastructure Building':
        return bd.get_name('english') in _ROAD_LIKE_INFRA
    return False


_DRAG_PLACEABLE_CATEGORIES = frozenset({
    'Building Module',   # e.g. Silos
    'Cultivation Area',  # farm fields (Oat Field, Wheat Field, …)
    'Livestock Area',    # pastures placed around livestock farms
})

# Categories excluded from "affected buildings" when a building has an effect radius: decorative/cosmetic items that don't benefit from services.
_RADIUS_EXCLUDED_CATEGORIES = _DRAG_PLACEABLE_CATEGORIES | frozenset({
    'Ground Patterns',  # quay tiles, ground decoration
    'Amenity',          # ornamental amenity items
    'Ornament',         # pure ornamental buildings
})

# SubTilesGrid nibble shapes for polygon field tiles.
# Coordinates are (x, y) in units of ts/2 from the grid anchor (cx0, cy0), so the full 1×1 orthogonal tile spans [0..2] × [0..2]:
#   TL=(0,0)  TR=(2,0)  BR=(2,2)  BL=(0,2)  C=(1,1)
# Bit meanings (triangles of the square from centre, top-down view):
#   bit3 (T): [C,TL,TR]  bit2 (R): [C,TR,BR]
#   bit1 (B): [C,BR,BL]  bit0 (L): [C,BL,TL]
# Adjacent pairs degenerate: C lies on the square diagonal, so the two triangles collapse into a single half-square triangle.
# Non-adjacent pairs (0x5, 0xA) need two separate polygons.
# Each list entry is one polygon drawn with create_polygon.
_NIBBLE_SHAPES: dict[int, list[list[tuple]]] = {
    0x0: [],
    0x1: [[(1,1),(0,2),(0,0)]],                              # L
    0x2: [[(1,1),(2,2),(0,2)]],                              # B
    0x3: [[(0,0),(2,2),(0,2)]],                              # L+B  → lower-left half (TL-BR diag)
    0x4: [[(1,1),(2,0),(2,2)]],                              # R
    0x5: [[(1,1),(0,2),(0,0)], [(1,1),(2,0),(2,2)]],         # L+R  (non-adjacent)
    0x6: [[(2,0),(2,2),(0,2)]],                              # B+R  → lower-right half (TR-BL diag)
    0x7: [[(2,0),(1,1),(0,0),(0,2),(2,2)]],                  # L+B+R → missing T (pentagon)
    0x8: [[(1,1),(0,0),(2,0)]],                              # T
    0x9: [[(0,0),(2,0),(0,2)]],                              # T+L  → upper-left half (TR-BL diag)
    0xA: [[(1,1),(0,0),(2,0)], [(1,1),(2,2),(0,2)]],         # T+B  (non-adjacent)
    0xB: [[(0,0),(2,0),(1,1),(2,2),(0,2)]],                  # T+L+B → missing R (pentagon)
    0xC: [[(0,0),(2,0),(2,2)]],                              # T+R  → upper-right half (TL-BR diag)
    0xD: [[(0,0),(2,0),(2,2),(1,1),(0,2)]],                  # T+R+L → missing B (pentagon)
    0xE: [[(2,0),(2,2),(0,2),(1,1),(0,0)]],                  # B+R+T → missing L (pentagon)
    0xF: [[(0,0),(2,0),(2,2),(0,2)]],                        # full square
}

def _nibble_centroid(nibble: int) -> tuple[float, float]:
    """Return the vertex-average centroid of a nibble shape in ts/2 units."""
    shapes = _NIBBLE_SHAPES.get(nibble & 0xF, [])
    pts = [pt for shape in shapes for pt in shape]
    if not pts:
        return (1.0, 1.0)
    return (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))

def _is_drag_placeable(bd: BuildingData) -> bool:
    """True for buildings that support drag-to-place (roads, aqueducts, modules, fields)."""
    return _is_road_like(bd) or bd.get_category_english() in _DRAG_PLACEABLE_CATEGORIES


_HOUSE_CATEGORIES = frozenset({
    'Roman Residence', 'Celtic Residence', 'Romano-Celtic Residence',
})

def _is_house(bd: BuildingData) -> bool:
    """True for residence buildings, which support max-2-wide block drag-placement."""
    return bd.get_category_english() in _HOUSE_CATEGORIES


def _road_priority(bd: BuildingData) -> int:
    """Return placement priority for road buildings (0 = not a road)."""
    if 'Road' not in bd.get_category_english():
        return 0
    return ROAD_PRIORITY.get(bd.get_name('english'), 1)


# Infrastructure that a road may run underneath/across without colliding (the thin arch/canal segments - not the bulky source/cistern endpoints).
_ROAD_CROSSABLE_INFRA = frozenset({'Aqueduct', 'Drainage Channel'})

def _is_road_crossable_infra(bd: BuildingData) -> bool:
    return (bd.get_category_english() == 'Infrastructure Building'
            and bd.get_name('english') in _ROAD_CROSSABLE_INFRA)


def _can_coexist(bd_a: BuildingData, bd_b: BuildingData) -> bool:
    """True if two buildings are allowed to occupy the same tile(s)."""
    a_road, b_road = _road_priority(bd_a) > 0, _road_priority(bd_b) > 0
    if a_road and b_road:
        return True  # road vs road: overlap allowed, eviction handles priority
    a_cross, b_cross = _is_road_crossable_infra(bd_a), _is_road_crossable_infra(bd_b)
    return (a_road and b_cross) or (b_road and a_cross)


def _road_45_uv(bd: BuildingData, pb) -> tuple:
    """Return (u0, u1, v0, v1) axis-aligned rectangle in (u=x+y, v=x-y) space."""
    rot = pb.rotation % 360
    nw, nh = _get_45_grid_counts(bd, rot)
    bbox_half = (nw + nh) * 0.25
    bcx = pb.grid_x + bbox_half
    bcy = pb.grid_y + bbox_half
    uc, vc = bcx + bcy, bcx - bcy
    return uc - nw * 0.5, uc + nw * 0.5, vc - nh * 0.5, vc + nh * 0.5


def _uv_fully_covered(u0: float, u1: float, v0: float, v1: float, rects: list) -> bool:
    """Return True if (u0,u1)×(v0,v1) is completely covered by the union of rects."""
    if not rects:
        return False
    # Coordinate-compress within the target rectangle so we can test each cell
    us = sorted({u0, u1}
                | {max(u0, min(u1, r[0])) for r in rects}
                | {max(u0, min(u1, r[1])) for r in rects})
    vs = sorted({v0, v1}
                | {max(v0, min(v1, r[2])) for r in rects}
                | {max(v0, min(v1, r[3])) for r in rects})
    for i in range(len(us) - 1):
        for j in range(len(vs) - 1):
            mu = (us[i] + us[i + 1]) / 2
            mv = (vs[j] + vs[j + 1]) / 2
            if not any(r[0] <= mu <= r[1] and r[2] <= mv <= r[3] for r in rects):
                return False
    return True

SQRT2 = math.sqrt(2)


class PlacedBuilding:
    """A building instance placed on the canvas."""
    _next_id = 1

    def __init__(self, guid: int, grid_x: float, grid_y: float, rotation: int = 0, instance_id: int = None, parent_id: int = None, nibble: int = 0):
        self.guid = guid
        self.grid_x = grid_x       # top-left corner in grid coords
        self.grid_y = grid_y
        self.rotation = rotation   # 0, 45, 90, 135, 180, 225, 270, 315
        self.nibble = nibble       # SubTilesGrid polygon sub-tile bitmask (0 = normal building)
        if instance_id is None:
            self.instance_id = PlacedBuilding._next_id
            PlacedBuilding._next_id += 1
        else:
            self.instance_id = instance_id
        self.parent_id: int = parent_id  # instance_id of the parent farm, if placed as a module

    def clone(self) -> 'PlacedBuilding':
        return PlacedBuilding(self.guid, self.grid_x, self.grid_y,
                              self.rotation, instance_id=None, nibble=self.nibble)

    def to_dict(self) -> dict:
        d = {
            'guid': self.guid,
            'grid_x': self.grid_x,
            'grid_y': self.grid_y,
            'rotation': self.rotation,
            'instance_id': self.instance_id,
        }
        if self.parent_id is not None:
            d['parent_id'] = self.parent_id
        if self.nibble:
            d['nibble'] = self.nibble
        return d

    @staticmethod
    def from_dict(d: dict) -> 'PlacedBuilding':
        return PlacedBuilding(
            d['guid'], d['grid_x'], d['grid_y'],
            d.get('rotation', 0), d.get('instance_id'),
            parent_id=d.get('parent_id'),
            nibble=d.get('nibble', 0),
        )


def _rotation_footprint(bd: BuildingData, rotation: int):
    """
    Return (w, h, offset_x, offset_y) bounding-box footprint in grid tiles.
    For 90° aligned rotations, exact integer tiles.
    For 45° diagonal rotations, the bounding box is a square of side (nw+nh)*0.5 grid tiles, where nw/nh are the snapped 45°-grid tile counts.
    """
    rot = rotation % 360
    if rot in (0, 180):
        return bd.width, bd.height, 0.0, 0.0
    elif rot in (90, 270):
        return bd.height, bd.width, 0.0, 0.0
    else:
        nw, nh = _get_45_grid_counts(bd, rotation)
        bbox_side = (nw + nh) * 0.5   # actual square bounding box in grid tiles
        return bbox_side, bbox_side, 0.0, 0.0


def _get_occupied_tiles(bd: BuildingData, gx: float, gy: float, rotation: int):
    """Return set of (col, row) integer tiles occupied by a placed building."""
    rot = rotation % 360
    if rot in (0, 90, 180, 270):
        w, h, _, _ = _rotation_footprint(bd, rotation)
        tiles = set()
        for dy in range(h):
            for dx in range(w):
                tiles.add((int(gx) + dx, int(gy) + dy))
        return tiles
    else:
        # 45° diagonal: SAT (Separating Axis Theorem) intersection test between the tile unit square and the rotated building rectangle.
        # Four axes to test: (1,0), (0,1) from the tile; (1,1), (1,-1) from the building.
        # Using <= for separation so exactly-touching edges are NOT collisions.
        rot = rotation % 360
        nw, nh = _get_45_grid_counts(bd, rot)
        bbox_half = (nw + nh) * 0.25
        bcx = gx + bbox_half
        bcy = gy + bbox_half
        q = 0.25  # shorthand
        # Building corner coordinates (precomputed for all four corners)
        bxs = (bcx + (nh - nw) * q,  bcx + (nw + nh) * q,
               bcx + (nw - nh) * q,  bcx - (nw + nh) * q)
        bys = (bcy - (nw + nh) * q,  bcy + (nw - nh) * q,
               bcy + (nw + nh) * q,  bcy + (nh - nw) * q)
        x0 = int(math.floor(gx))
        y0 = int(math.floor(gy))
        x1 = int(math.ceil(gx + 2 * bbox_half))
        y1 = int(math.ceil(gy + 2 * bbox_half))
        tiles = set()
        for row in range(y0, y1):
            for col in range(x0, x1):
                separated = False
                for ax, ay in ((1, 0), (0, 1), (1, 1), (1, -1)):
                    b_min = min(bxs[i] * ax + bys[i] * ay for i in range(4))
                    b_max = max(bxs[i] * ax + bys[i] * ay for i in range(4))
                    # Tile corners: (col,row),(col+1,row),(col+1,row+1),(col,row+1)
                    t0 = col * ax + row * ay
                    t1 = (col + 1) * ax + row * ay
                    t2 = (col + 1) * ax + (row + 1) * ay
                    t3 = col * ax + (row + 1) * ay
                    t_min = min(t0, t1, t2, t3)
                    t_max = max(t0, t1, t2, t3)
                    if b_max <= t_min or t_max <= b_min:
                        separated = True
                        break
                if not separated:
                    tiles.add((col, row))
        return tiles


def _rotate_quad_90cw(q: int) -> int:
    """Rotate a tile quadrant mask 90° CW.
    Bit encoding: 1=W 2=S 4=E 8=N (kept inner triangles).
    90° CW maps: N→W, E→N, S→E, W→S.
    """
    if not q:
        return 0
    new_W = (q >> 3) & 1   # old N
    new_S = (q >> 0) & 1   # old W
    new_E = (q >> 1) & 1   # old S
    new_N = (q >> 2) & 1   # old E
    return new_W | (new_S << 1) | (new_E << 2) | (new_N << 3)


def _overlaps_nonbuildable_half(poly: list, tx: int, ty: int, quad: int) -> bool:
    """SAT test: True if the 4-corner building polygon (flat [x0,y0,x1,y1,...]) overlaps the non-buildable (land-coloured) triangle of a cut LAND tile.

    Non-buildable triangles per cut direction:
      NE (0b0011) keeps W+S → land is SW half → TL, BL, BR
      NW (0b0110) keeps S+E → land is SE half → TR, BL, BR
      SE (0b1001) keeps W+N → land is NW half → TL, TR, BL
      SW (0b1100) keeps E+N → land is NE half → TL, TR, BR
    """
    if   quad == 0b0011: tri_x = (tx,   tx,   tx+1); tri_y = (ty,   ty+1, ty+1)
    elif quad == 0b0110: tri_x = (tx+1, tx,   tx+1); tri_y = (ty,   ty+1, ty+1)
    elif quad == 0b1001: tri_x = (tx,   tx+1, tx  ); tri_y = (ty,   ty,   ty+1)
    elif quad == 0b1100: tri_x = (tx,   tx+1, tx+1); tri_y = (ty,   ty,   ty+1)
    else: return True  # unknown quad → conservatively block
    bxs = (poly[0], poly[2], poly[4], poly[6])
    bys = (poly[1], poly[3], poly[5], poly[7])
    for ax, ay in ((1, 0), (0, 1), (1, 1), (1, -1)):
        b_min = min(bxs[i]*ax + bys[i]*ay for i in range(4))
        b_max = max(bxs[i]*ax + bys[i]*ay for i in range(4))
        t_min = min(tri_x[i]*ax + tri_y[i]*ay for i in range(3))
        t_max = max(tri_x[i]*ax + tri_y[i]*ay for i in range(3))
        if b_max <= t_min or t_max <= b_min:
            return False  # separated on this axis → no overlap
    return True  # overlapping on all axes


class CanvasWidget(tk.Frame):
    """Main canvas widget with grid, building placement, etc."""

    def __init__(self, master, app, **kwargs):
        super().__init__(master, bg=BG_MAIN, **kwargs)
        self.app = app
        self.dm = get_data_manager()

        # Grid state
        self.tile_size = DEFAULT_TILE_SIZE   # current pixel size of one tile
        self.pan_x = 0.0                     # canvas origin in screen coords
        self.pan_y = 0.0
        self.show_45_grid = tk.BooleanVar(value=self._setting('show_45_grid', True))


        # Buildings
        self.placed_buildings: list[PlacedBuilding] = []
        self._collision_map: dict = {}  # (col, row) -> list of instance_ids

        # Connected-road-network graph, used for StreetDistance BFS.
        # Rebuilt lazily (see _get_road_graph) whenever placed_buildings changes.
        self._road_graph = None
        self._road_graph_dirty = True

        # Module-touching-a-different-parent's-module pairs, used to decide which parent/module group needs a lightened colour variant (see _resolve_render_color). Rebuilt lazily whenever placed_buildings changes; layout-independent (colour) data isn't cached here, so colour-override changes don't need to invalidate it separately.
        self._module_touch_pairs_cache = None
        self._parent_color_ranks_cache: Optional[dict] = None
        self._draw_order_cache: Optional[list] = None
        self._road_pos_cache: Optional[tuple] = None  # (diag_road_pos, ortho_road_pos)

        # Pan-only fast-redraw state
        self._layout_dirty: bool = True   # True → next redraw must be a full rebuild
        self._prev_pan_x: float = 0.0
        self._prev_pan_y: float = 0.0

        # Undo/redo stacks (each entry is a snapshot of placed_buildings)
        self._undo_stack: list = []
        self._redo_stack: list = []
        self._max_history = 50

        # Selection state
        self.selected_ids: set = set()
        self._box_sel_start = None  # canvas coords of box-select start
        self._box_sel_rect = None   # canvas item id

        # Build mode
        self.build_mode_guid: Optional[int] = None
        self.build_rotation: int = 0
        self._ghost_items: list = []
        self._ghost_grid_pos = None
        self._dbg_hover_items: list = []  # debug: hover tooltip canvas items

        # Drag-move state
        self._drag_start_canvas = None
        self._drag_start_grids: dict = {}  # instance_id -> (gx, gy)
        self._drag_extra_ids: set = set()  # parented modules moved along with their parent
        self._is_dragging = False
        self._drag_moved = False
        self._drag_last_notify_grids: dict = {}  # grid positions at last _notify_selection call

        # Icon cache
        self._icon_cache: dict = {}
        self._photo_cache: dict = {}  # keep references

        # Delete mode
        self.delete_mode = tk.BooleanVar(value=False)

        # Light / dark mode
        self.light_mode = tk.BooleanVar(value=self._setting('light_mode', False))

        # Road / module display settings
        self.road_show_outline  = tk.BooleanVar(value=self._setting('road_show_outline', True))
        self.road_show_icon     = tk.BooleanVar(value=self._setting('road_show_icon', True))
        self.module_show_icon   = tk.BooleanVar(value=self._setting('module_show_icon', True))

        # Placement-mode toggles
        self.module_rect_mode = tk.BooleanVar(value=self._setting('module_rect_mode', False))  # 1x1 modules/fields: rectangle-fill drag
        self.line_mode        = tk.BooleanVar(value=self._setting('line_mode', False))  # roads/channels/aqueducts: click-click line tool

        # Persist these display/tool settings across app restarts.
        for _key, _var in [
            ('show_45_grid', self.show_45_grid),
            ('light_mode', self.light_mode),
            ('road_show_outline', self.road_show_outline),
            ('road_show_icon', self.road_show_icon),
            ('module_show_icon', self.module_show_icon),
            ('module_rect_mode', self.module_rect_mode),
            ('line_mode', self.line_mode),
        ]:
            _var.trace_add('write', lambda *_a, k=_key, v=_var: self._persist_setting(k, v))

        # Road drag-placement state
        self._road_drag_active = False
        self._road_drag_last_pos = None
        self._road_drag_placed_ids: set = set()  # instance_ids placed in current drag

        # Module parentage: instance_id of the farm we're currently building modules for
        self._module_parent_id: int = None

        # House block drag-placement state (max-2-wide, custom-length blocks)
        self._house_drag_active = False
        self._house_drag_anchor = None         # (gx, gy) anchor at drag start
        self._house_drag_positions: list = []  # current preview block positions

        # Module rectangle-fill state (active when module_rect_mode is on)
        self._module_rect_active = False
        self._module_rect_anchor = None         # (gx, gy) anchor at drag start
        self._module_rect_positions: list = []  # current preview fill positions

        # Straight-line tool state (active when line_mode is on)
        self._line_start = None  # (gx, gy) of the first click, or None

        # Multi-building paste ghost state (Ctrl+V with >1 building copied,
        # or move mode below re-using the same machinery)
        self._paste_active = False
        self._paste_clipboard: list = []     # cloned PlacedBuilding items, original positions
        self._paste_anchor_orig = None       # (gx, gy) whole-tile reference anchor at copy time
        self._paste_ghost_pos = None         # (gx, gy) current mouse-tracked target anchor

        # Effects carried through copy/paste and move operations.
        # _clipboard_effects: clipboard-pb instance_id → {tech, items, boosts} snapshots _pending_paste_effects: set when a single-building paste/move is in flight; consumed (and cleared) by the next _place_building call.
        self._clipboard_effects: dict = {}
        self._pending_paste_effects = None

        # Move mode (hotkey M): re-ghosts the current selection in place so it can be repositioned/rotated using the normal ghost/paste-ghost placement and collision logic, instead of transforming placed buildings' positions analytically.
        self._move_mode_active = False
        self._move_restore_snapshot: list = []  # to_dict() snapshots, restored if cancelled
        self._move_id_remap: dict = {}  # clipboard instance_id -> real placed instance_id, accumulated across partial commits in one move session

        # Tech effect activation per placed building instance: instance_id -> set of effect GUIDs
        self._active_tech_effects: dict = {}
        # Item effect activation per placed building instance: instance_id -> set of item GUIDs
        self._active_item_effects: dict = {}
        # Boosted item GUIDs per instance (subset of _active_item_effects)
        self._active_item_boosts: dict = {}

        # Island state
        self._island_name: str | None = None
        self._island_w: int = 0
        self._island_h: int = 0
        self._island_tiles: bytes | None = None     # flat row-major bytes, tile type only (0-4)
        self._island_quads: bytes | None = None     # flat row-major bytes, quadrant cut mask per tile
        self._island_base_img_dark: object = None   # PIL Image, 1px/tile, dark theme
        self._island_base_img_light: object = None  # PIL Image, 1px/tile, light theme
        self._island_photo_ref = None               # keep PIL PhotoImage alive (legacy ref)
        self._island_bg_cache_key = None            # kept for compatibility; chunked bg ignores it
        self._island_chunk_photos: dict = {}        # (cx, cy, ts_key, light) -> ImageTk.PhotoImage
        self._island_chunk_size: int = 32           # tiles per chunk side
        self._drawn_island_chunks: set = set()      # (cx, cy) pairs currently on the canvas

        # Deferred redraws
        self._deferred_redraw_id = None
        self._deferred_island_bg_id = None          # kept so _on_pan_start cancel is harmless
        self._deferred_stats_id = None              # deferred info-panel stats update

        self._build_ui()
        self._bind_events()
        self._center_view()

    def _setting(self, key: str, default):
        """Read a persisted display/tool setting from app.settings, if the host app provides one (falls back to `default` for the bare app stubs used in tests, or before a value has ever been set)."""
        settings = getattr(self.app, 'settings', None)
        if settings is None:
            return default
        return settings.get(key, default)

    def _persist_setting(self, key: str, var: tk.BooleanVar, *_):
        """Write a display/tool checkbox's current value back to app.settings and save it, so it survives an app restart."""
        settings = getattr(self.app, 'settings', None)
        if settings is None:
            return
        settings[key] = var.get()
        save_settings(settings)

    # ------------------------------------------------------------------ #
    #  UI construction
    # ------------------------------------------------------------------ #
    def _build_ui(self):
        self.canvas = tk.Canvas(
            self, bg=BG_MAIN, highlightthickness=0,
            cursor="crosshair"
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        # Bottom-right overlay: quick display-setting toggles
        self._overlay_frame = tk.Frame(self.canvas, bg=BG_SECTION, bd=1, relief=tk.FLAT)
        self._overlay_frame.place(relx=1.0, rely=1.0, anchor='se', x=-4, y=-4)

        self._make_hotbar_checkbox(self._overlay_frame, "45° Grid", self.show_45_grid).pack(side=tk.TOP, anchor='w', padx=6, pady=(4, 2))
        self._make_hotbar_checkbox(self._overlay_frame, "Module Icons", self.module_show_icon).pack(side=tk.TOP, anchor='w', padx=6, pady=2)
        self._make_hotbar_checkbox(self._overlay_frame, "Road Icons", self.road_show_icon).pack(side=tk.TOP, anchor='w', padx=6, pady=2)
        self._make_hotbar_checkbox(self._overlay_frame, "Module Box Fill", self.module_rect_mode).pack(side=tk.TOP, anchor='w', padx=6, pady=2)
        self._make_hotbar_checkbox(self._overlay_frame, "Road Line Tool", self.line_mode).pack(side=tk.TOP, anchor='w', padx=6, pady=(2, 4))

    def _make_hotbar_checkbox(self, parent, label: str, variable: tk.BooleanVar) -> tk.Frame:
        """Small Unicode-glyph checkbox for the bottom-right canvas overlay
        (matching the View menu's underlying setting, just quicker to reach)."""
        frame = tk.Frame(parent, bg=BG_SECTION, cursor='hand2')
        glyph = tk.Label(frame, text='☐', bg=BG_SECTION, fg=FG_GOLD, font=FONT_SMALL, cursor='hand2')
        glyph.pack(side=tk.LEFT, padx=(0, 4))
        tk.Label(frame, text=label, bg=BG_SECTION, fg=FG_DIM, font=FONT_SMALL, cursor='hand2').pack(side=tk.LEFT)

        def _refresh(*_):
            glyph.config(text='☑' if variable.get() else '☐')
            self._redraw()

        def _toggle(*_):
            variable.set(not variable.get())

        variable.trace_add('write', _refresh)
        for w in [frame] + list(frame.winfo_children()):
            w.bind('<Button-1>', _toggle)
        _refresh()
        return frame

    def _bind_events(self):
        c = self.canvas
        c.bind("<Configure>", self._on_resize)
        c.bind("<MouseWheel>", self._on_mousewheel)          # Windows/Mac
        c.bind("<Button-4>", self._on_mousewheel)            # Linux scroll up
        c.bind("<Button-5>", self._on_mousewheel)            # Linux scroll down
        c.bind("<ButtonPress-2>", self._on_pan_start)
        c.bind("<B2-Motion>", self._on_pan_move)
        c.bind("<ButtonRelease-2>", self._on_pan_end)
        c.bind("<ButtonPress-1>", self._on_left_click)
        c.bind("<B1-Motion>", self._on_left_drag)
        c.bind("<ButtonRelease-1>", self._on_left_release)
        c.bind("<ButtonPress-3>", self._on_right_click)
        c.bind("<Double-Button-1>", self._on_double_click)
        c.bind("<Motion>", self._on_mouse_move)
        c.bind("<Leave>", self._on_mouse_leave)

        # Keyboard – bound at top-level via app
        self.bind_all("<Delete>", self._on_delete_key)
        self.bind_all("<comma>", self._on_rotate_ccw)
        self.bind_all("<period>", self._on_rotate_cw)
        self.bind_all("<Control-z>", self._on_undo)
        self.bind_all("<Control-y>", self._on_redo)
        self.bind_all("<Control-c>", self._on_copy)
        self.bind_all("<Control-v>", self._on_paste)
        self.bind_all("<Control-a>", self._on_select_all)
        self.bind_all("<KeyPress-m>", lambda e: self.start_move_mode())
        self.bind_all("<KeyPress-M>", lambda e: self.start_move_mode())
        self.bind_all("<Shift-KeyPress-u>", self._on_road_swap)
        self.bind_all("<Shift-KeyPress-U>", self._on_road_swap)
        self._clipboard: list = []

    # ------------------------------------------------------------------ #
    #  Coordinate helpers
    # ------------------------------------------------------------------ #
    def _center_view(self):
        """Centre the grid origin in the canvas."""
        self.update_idletasks()
        w = self.canvas.winfo_width() or 800
        h = self.canvas.winfo_height() or 600
        self.pan_x = w / 2 - 10 * self.tile_size
        self.pan_y = h / 2 - 10 * self.tile_size
        self._redraw()

    def canvas_to_grid(self, cx: float, cy: float):
        """Convert canvas pixel coords to fractional grid (col, row)."""
        gx = (cx - self.pan_x) / self.tile_size
        gy = (cy - self.pan_y) / self.tile_size
        return gx, gy

    def grid_to_canvas(self, gx: float, gy: float):
        """Convert grid coords to canvas pixel coords (top-left of tile)."""
        cx = gx * self.tile_size + self.pan_x
        cy = gy * self.tile_size + self.pan_y
        return cx, cy

    @staticmethod
    def _snap_45_anchor(gx_raw: float, gy_raw: float, nw: int, nh: int):
        """
        Snap the anchor (top-left of bounding box) of a 45°-rotated building
        so that all four building edges fall exactly on diagonal grid lines.

        The conditions derived from aligning side edges with y±x = integer:
          Cx + Cy ≡ offset_u (mod 1)  where offset_u = 0.5 if nh is odd, else 0
          Cx - Cy ≡ offset_v (mod 1)  where offset_v = 0.5 if nw is odd, else 0
        We transform to (u, v) = (Cx+Cy, Cx-Cy), snap each coordinate, then
        convert back to an anchor.
        """
        offset_u = 0.5 if nw % 2 == 1 else 0.0
        offset_v = 0.5 if nh % 2 == 1 else 0.0
        bbox_half = (nw + nh) * 0.25
        # Centre from raw anchor
        u_raw = gx_raw + gy_raw + 2 * bbox_half   # = Cx + Cy
        v_raw = gx_raw - gy_raw                    # = Cx - Cy (bbox_half cancels)
        u_snap = round(u_raw - offset_u) + offset_u
        v_snap = round(v_raw - offset_v) + offset_v
        cx_snap = (u_snap + v_snap) / 2
        cy_snap = (u_snap - v_snap) / 2
        return cx_snap - bbox_half, cy_snap - bbox_half

    @staticmethod
    def _building_center(bd: BuildingData, gx: float, gy: float, rotation: int):
        """Return the grid-space centre of a building from its anchor and rotation."""
        rot = rotation % 360
        if rot in (0, 90, 180, 270):
            w = bd.width if rot in (0, 180) else bd.height
            h = bd.height if rot in (0, 180) else bd.width
            return gx + w / 2, gy + h / 2
        else:
            nw, nh = _get_45_grid_counts(bd, rot)
            bbox_half = (nw + nh) * 0.25
            return gx + bbox_half, gy + bbox_half

    def _snap_anchor_from_center(self, bd: BuildingData, cx: float, cy: float, rotation: int):
        """Return a snapped anchor for a building centred at (cx, cy) at rotation."""
        rot = rotation % 360
        if rot in (0, 90, 180, 270):
            w = bd.width if rot in (0, 180) else bd.height
            h = bd.height if rot in (0, 180) else bd.width
            return float(math.floor(cx - w / 2 + 0.5)), float(math.floor(cy - h / 2 + 0.5))
        else:
            nw, nh = _get_45_grid_counts(bd, rot)
            bbox_half = (nw + nh) * 0.25
            return self._snap_45_anchor(cx - bbox_half, cy - bbox_half, nw, nh)

    def snap_to_grid(self, gx: float, gy: float, rotation: int = 0, bd: BuildingData = None):
        """Snap grid coords to tile or 0.5-tile grid depending on rotation."""
        rot = rotation % 360
        if rot in (0, 90, 180, 270):
            return float(math.floor(gx)), float(math.floor(gy))
        elif bd is not None:
            nw, nh = _get_45_grid_counts(bd, rot)
            return self._snap_45_anchor(gx, gy, nw, nh)
        else:
            # No building data: snap to nearest 45°-grid corner (both-odd fallback)
            return self._snap_45_anchor(gx, gy, 1, 1)

    # ------------------------------------------------------------------ #
    #  Drawing
    # ------------------------------------------------------------------ #
    def _redraw(self, event=None):
        c = self.canvas
        light = self.light_mode.get()
        if self._island_tiles is not None:
            sea_hex = _ISLE_COLORS[light][_ISLE_SEA]
            c.configure(bg=sea_hex)
        else:
            c.configure(bg='#ffffff' if light else BG_MAIN)
        c.delete('all')
        # All canvas items were just cleared — reset chunk tracking and evict stale chunk photos for any previous tile_size or light-mode setting.
        self._drawn_island_chunks.clear()
        _ts_key = round(self.tile_size * 100)
        _light  = self.light_mode.get()
        _stale  = [k for k in self._island_chunk_photos if k[2] != _ts_key or k[3] != _light]
        for k in _stale:
            del self._island_chunk_photos[k]
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 2 or h < 2:
            return

        if self._island_tiles is not None:
            self._draw_island_bg()
        self._draw_grid(w, h)
        in_range = self._draw_buildings()
        self._draw_radius_overlay()
        self._draw_in_range_highlights(in_range)
        self._draw_ghost()
        if self._box_sel_rect is not None:
            # Redrawn separately
            pass
        # Restore box selection rectangle if active
        if self._box_sel_start and hasattr(self, '_box_sel_cur'):
            x0, y0 = self._box_sel_start
            x1, y1 = self._box_sel_cur
            self._box_sel_rect = c.create_rectangle(
                x0, y0, x1, y1,
                outline=FG_GOLD, fill='', dash=(4, 4), tags='boxsel'
            )

        # After a full rebuild, future pan events can use the fast path.
        self._layout_dirty = False
        self._prev_pan_x = self.pan_x
        self._prev_pan_y = self.pan_y

    def _redraw_pan(self):
        """Fast pan-only redraw: shift persistent 'world' items by the pan delta, then rebuild only the viewport-dependent elements (island background, grid lines, ghost).
        Falls back to a full _redraw() if the layout is dirty (zoom/layout change since the last full build)."""
        c = self.canvas
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 2 or h < 2:
            return
        if self._layout_dirty:
            self._redraw()
            return

        dx = self.pan_x - self._prev_pan_x
        dy = self.pan_y - self._prev_pan_y
        self._prev_pan_x = self.pan_x
        self._prev_pan_y = self.pan_y

        # One Tcl command moves ALL world-coordinate items in C — ~1 ms for 9000 buildings vs ~180 ms of delete-all + per-item re-create calls.
        if dx or dy:
            c.move('all', dx, dy)

        # Fill any island-background chunks that scrolled into view during the pan step (~2 ms per new chunk at ts≈7; zero cost on cache hit).
        if self._island_tiles is not None:
            self._fill_island_chunks()

        # Grid lines and ghost span the full viewport and must be recreated.
        c.delete('grid90')
        c.delete('grid45')
        c.delete('ghost')
        self._draw_grid(w, h)

        # _draw_grid() creates items at the top of the Z-order (last created = highest), which puts them above buildings from the last full _redraw().
        # Fix: raise grid lines to just above the island background so buildings stay on top of the grid.
        if c.find_withtag('island_bg'):
            if c.find_withtag('grid90'):
                c.tag_raise('grid90', 'island_bg')
            if c.find_withtag('grid45'):
                c.tag_raise('grid45', 'grid90' if c.find_withtag('grid90') else 'island_bg')
        else:
            # No island background — lower grid below all other items (buildings).
            if c.find_withtag('grid90'):
                c.lower('grid90')
            if c.find_withtag('grid45'):
                c.lower('grid45')

        self._draw_ghost()

    def _draw_grid(self, w, h):
        c = self.canvas
        ts = self.tile_size

        # Visible grid range
        col_start = int(math.floor((0 - self.pan_x) / ts)) - 1
        col_end   = int(math.ceil((w - self.pan_x) / ts)) + 1
        row_start = int(math.floor((0 - self.pan_y) / ts)) - 1
        row_end   = int(math.ceil((h - self.pan_y) / ts)) + 1

        # Determine which grid(s) to show based on active build rotation.
        # In build mode: show only the grid matching the current rotation family.
        # Outside build mode: show 90° always; 45° based on the checkbox toggle.
        rot = self.build_rotation % 360
        in_build = self.build_mode_guid is not None
        show_90 = (not in_build) or (rot in (0, 90, 180, 270))
        show_45 = (in_build and rot not in (0, 90, 180, 270)) or \
                  (not in_build and self.show_45_grid.get())

        # Theme colours
        if self.light_mode.get():
            col90 = '#1a1a1a'
            col45 = '#cccccc'
        else:
            col90 = GRID_COLOR_90
            col45 = GRID_COLOR_45

        # 90° grid lines (only when tile_size >= 6)
        if show_90 and ts >= 6:
            for col in range(col_start, col_end + 1):
                x = col * ts + self.pan_x
                c.create_line(x, 0, x, h, fill=col90, tags='grid90')
            for row in range(row_start, row_end + 1):
                y = row * ts + self.pan_y
                c.create_line(0, y, w, y, fill=col90, tags='grid90')

        # 45° diagonal grid - O(cols+rows) lines, one per diagonal band
        if show_45 and ts >= 4:
            # \ diagonals: y = x + b,  b = k*ts + (pan_y - pan_x)
            bk_off = self.pan_y - self.pan_x
            for k in range(row_start - col_end, row_end - col_start + 1):
                b = k * ts + bk_off
                if b > h or b < -w:
                    continue
                x1 = max(0, -b)
                x2 = min(w, h - b)
                c.create_line(x1, x1 + b, x2, x2 + b, fill=col45, tags='grid45')

            # / diagonals: y = -x + b,  b = k*ts + (pan_y + pan_x)
            sl_off = self.pan_y + self.pan_x
            for k in range(row_start + col_start, row_end + col_end + 1):
                b = k * ts + sl_off
                if b < 0 or b > w + h:
                    continue
                x1 = max(0, b - h)
                x2 = min(w, b)
                c.create_line(x1, -x1 + b, x2, -x2 + b, fill=col45, tags='grid45')

    def _draw_buildings(self) -> set:
        """Draw all buildings plus the selected (gold) outline re-stroke.
        Returns the in-range id set so the caller can draw the green in-range highlight afterward (see _draw_in_range_highlights) -
        that needs to happen after radius rings/reach-highlights too, so it has to be a separate, later pass rather than done here."""
        in_range = self._get_in_range_ids()
        parent_color_ranks = self._get_parent_color_ranks()

        # Separate position sets for diagonal vs orthogonal road tiles, used to detect 90°/45° junction tiles that need a diamond corner clipped.
        # Use math.floor (not round) because road_imports tiles have half-integer grid positions (gx = tl_x − wx − 1.0 where wx is a half-integer).
        # Python banker's rounding gives floor(370.5)=370 but round(371.5)=372, breaking the expected step of 1 between consecutive diagonal tiles.
        # Cached alongside _draw_order_cache — both are invalidated on layout change.
        if self._road_pos_cache is None:
            _diag: set = set()
            _ortho: set = set()
            for _pb in self.placed_buildings:
                _bd = self.dm.get_building(_pb.guid)
                if _bd and _is_road_like(_bd) and not _pb.nibble:
                    _pos = (math.floor(_pb.grid_x), math.floor(_pb.grid_y))
                    if _pb.rotation % 360 not in (0, 90, 180, 270):
                        _diag.add(_pos)
                    else:
                        _ortho.add(_pos)
            self._road_pos_cache = (_diag, _ortho)
        diag_road_pos, ortho_road_pos = self._road_pos_cache

        # Draw in priority order: roads/quays first (underneath), buildings on top.
        # Road tiles: 1-3 by type. 1x1 quay (Ground Patterns) tiles: 0.
        # Everything else (buildings, ornaments, etc.): 10 — always above roads/quays.
        # The sort order depends only on building type (guid), not position, so cache it and only rebuild when placed_buildings changes.
        if self._draw_order_cache is None:
            def _draw_key(pb):
                bd = self.dm.get_building(pb.guid)
                if not bd:
                    return 10
                pri = _road_priority(bd)
                if pri > 0:
                    return pri  # 1-3: road tiles drawn underneath buildings
                if (bd.get_category_english() == 'Ground Patterns'
                        and bd.width == 1 and bd.height == 1):
                    return 0    # 1x1 quay tiles also drawn underneath
                return 10       # all other buildings drawn on top of roads/quays
            self._draw_order_cache = sorted(self.placed_buildings, key=_draw_key)

        for pb in self._draw_order_cache:
            self._draw_placed_building(pb, in_range=pb.instance_id in in_range, parent_color_ranks=parent_color_ranks, diag_road_pos=diag_road_pos, ortho_road_pos=ortho_road_pos)

        # Re-stroke the selected (gold) outline in a final pass, on top of every building.
        # Otherwise a later-drawn neighbour's plain black outline can visually cover a highlight that should take priority,
        # since the inline colour above is only as recent as that building's own position in the draw-order sort.
        for pb in self.placed_buildings:
            if pb.instance_id not in self.selected_ids:
                continue
            bd = self.dm.get_building(pb.guid)
            if not bd:
                continue
            self._draw_reach_outline(bd, pb.grid_x, pb.grid_y, pb.rotation, FG_GOLD, 3)
        return in_range

    def _draw_in_range_highlights(self, in_range: set):
        """Draw the green in-range outline, on top of everything else - including the gold selected-building outline and gold radius rings/reach-highlights - so green always has the final visual priority."""
        for pb in self.placed_buildings:
            if pb.instance_id not in in_range:
                continue
            bd = self.dm.get_building(pb.guid)
            if not bd:
                continue
            self._draw_reach_outline(bd, pb.grid_x, pb.grid_y, pb.rotation, '#2ecc71', 2)

    def _draw_placed_building(self, pb: PlacedBuilding, ghost=False, alpha_factor=1.0, in_range=False, parent_color_ranks: Optional[dict] = None, diag_road_pos: set = None, ortho_road_pos: set = None):
        bd = self.dm.get_building(pb.guid)
        if not bd:
            return
        c = self.canvas
        ts = self.tile_size
        rot = pb.rotation % 360

        cat_color = self._resolve_render_color(pb, bd, parent_color_ranks)
        selected = pb.instance_id in self.selected_ids

        # Road display overrides (also applies to aqueducts and drainage channels)
        is_road   = not ghost and _is_road_like(bd)
        is_module = not ghost and bd.get_category_english() in _DRAG_PLACEABLE_CATEGORIES
        road_fill = None
        if is_road and not self.road_show_icon.get():
            eng_name = bd.get_name('english')
            road_fill = ROAD_FILL_COLORS.get(eng_name, ROAD_FILL_DEFAULT)
        skip_module_icon = is_module and not self.module_show_icon.get()

        cx0, cy0 = self.grid_to_canvas(pb.grid_x, pb.grid_y)

        # ── SubTilesGrid polygon tile (farm fields imported from savegame) ────────
        # Each nibble tile is drawn as triangle(s) on the 45° grid, occupying a 2×2 bounding box anchored at (cx0, cy0) — the same footprint as a road tile.
        # The 4 quadrant triangles from the diamond centre:
        #   bit0 (L) = [C, B, L],  bit1 (B) = [C, R, B]
        #   bit2 (R) = [C, T, R],  bit3 (T) = [C, L, T]
        if pb.nibble:
            # Road/aqueduct nibble tiles are AreaPolygonObjectManager junction artifacts — road connectivity data, not field shapes.
            # The diamond building tile at that position already provides the correct visual.
            if is_road:
                return
            shapes = _NIBBLE_SHAPES.get(pb.nibble & 0xF, [])
            if shapes:
                tag  = 'ghost' if ghost else f'bld_{pb.instance_id}'
                fill = road_fill if road_fill is not None else cat_color
                half = ts / 2   # shapes use half-tile units; 2 = full tile width
                if ghost:
                    for shape in shapes:
                        pts = [v for fx, fy in shape
                               for v in (cx0 + fx * half, cy0 + fy * half)]
                        c.create_polygon(pts, fill=fill, outline=FG_GOLD, width=1, dash=(4, 4), stipple='gray50', tags=tag)
                else:
                    outline_c = FG_GOLD if selected else '#000000'
                    outline_w = 2 if selected else 1
                    for shape in shapes:
                        pts = [v for fx, fy in shape
                               for v in (cx0 + fx * half, cy0 + fy * half)]
                        c.create_polygon(pts, fill=fill, outline=outline_c, width=outline_w, tags=tag)
            if road_fill is None and not skip_module_icon and ts >= 8:
                popcount      = bin(pb.nibble & 0xF).count('1')
                fill_fraction = popcount / 4.0
                icon_px       = max(8, int(ts * 0.65 * math.sqrt(fill_fraction)))
                fcx, fcy      = _nibble_centroid(pb.nibble)
                icx           = cx0 + fcx * half
                icy           = cy0 + fcy * half
                icon = self._get_icon(bd, icon_px)
                if icon:
                    c.create_image(icx, icy, image=icon, anchor='center', tags='ghost' if ghost else f'bld_{pb.instance_id}')
                elif ts >= 16:
                    abbrev = bd.get_name(self.app.language)[:3]
                    c.create_text(icx, icy, text=abbrev, fill=FG_MAIN, font=FONT_XSMALL if ts < 32 else FONT_SMALL, tags='ghost' if ghost else f'bld_{pb.instance_id}')
            return

        if rot in (0, 90, 180, 270):
            w, h = (bd.width, bd.height) if rot in (0, 180) else (bd.height, bd.width)
            px_w = w * ts
            px_h = h * ts
            fill = road_fill if road_fill is not None else cat_color
            outline_w = 3 if selected else 2
            if selected:
                outline_c = FG_GOLD
            elif in_range:
                outline_c = '#2ecc71'
            elif is_road and not self.road_show_outline.get():
                outline_c = fill
                outline_w = 1
            else:
                outline_c = '#000000'

            if ghost:
                # Draw with stipple to indicate ghost (tkinter has no alpha)
                rect = c.create_rectangle(
                    cx0, cy0, cx0 + px_w, cy0 + px_h,
                    fill=fill, outline=FG_GOLD,
                    width=1, dash=(4, 4), stipple='gray50', tags='ghost'
                )
            else:
                rect = c.create_rectangle(
                    cx0, cy0, cx0 + px_w, cy0 + px_h,
                    fill=fill, outline=outline_c,
                    width=outline_w, tags=f'bld_{pb.instance_id}'
                )
                if selected:
                    # Selection highlight
                    c.create_rectangle(
                        cx0 + 1, cy0 + 1, cx0 + px_w - 1, cy0 + px_h - 1,
                        fill='', outline=FG_GOLD, width=1,
                        dash=(3, 3), tags=f'bld_{pb.instance_id}'
                    )

            # Icon (skipped for roads/modules per display settings)
            center_cx = cx0 + px_w / 2
            center_cy = cy0 + px_h / 2
            if road_fill is None and not skip_module_icon:
                icon_px = max(1, int(min(px_w, px_h) * 0.65))
                icon = self._get_icon(bd, icon_px)
                if icon:
                    c.create_image(center_cx, center_cy, image=icon, anchor='center', tags='ghost' if ghost else f'bld_{pb.instance_id}')
                elif ts >= 16:
                    abbrev = bd.get_name(self.app.language)[:3]
                    c.create_text(
                        center_cx, center_cy,
                        text=abbrev, fill=FG_MAIN,
                        font=FONT_XSMALL if ts < 32 else FONT_SMALL,
                        tags='ghost' if ghost else f'bld_{pb.instance_id}'
                    )

        else:
            # 45° rotated building – draw as proper rotated rectangle.
            # Each 45°-grid tile has side = 0.5√2 normal tiles.
            # A building of nw×nh 45°-tiles has side lengths nw*0.5√2 and nh*0.5√2.
            # After 45° CW rotation the bounding box is square: (nw+nh)*0.5 tiles.
            # With half = ts/4, the four corners relative to the bbox centre are:
            #   top:    (cx + (nh-nw)*half,  cy - (nw+nh)*half)
            #   right:  (cx + (nw+nh)*half,  cy + (nw-nh)*half)
            #   bottom: (cx + (nw-nh)*half,  cy + (nw+nh)*half)
            #   left:   (cx - (nw+nh)*half,  cy + (nh-nw)*half)
            # 135°/315°: swap width↔height so orientation is correct for non-square.
            nw, nh = _get_45_grid_counts(bd, rot)
            half = 0.25 * ts
            bbox_px = (nw + nh) * half   # half of bbox side in pixels

            cx = cx0 + bbox_px
            cy = cy0 + bbox_px

            pts = [
                cx + (nh - nw) * half, cy - (nw + nh) * half,
                cx + (nw + nh) * half, cy + (nw - nh) * half,
                cx + (nw - nh) * half, cy + (nw + nh) * half,
                cx - (nw + nh) * half, cy + (nh - nw) * half,
            ]

            # At 90°/45° junctions one diamond corner is exposed (not adjacent
            # to any road tile).  Clip it by replacing that corner with the
            # diamond centre so the stray triangle is not rendered.
            #
            # Guard 1 – only road tiles with exactly ONE diagonal neighbour
            #   (start/end of a diagonal segment).  Interior tiles and bends
            #   (≥ 2 diagonal neighbours) must not be touched.
            # Guard 2 – use separate diag / ortho position sets so that a 90°
            #   road tile is never mistaken for a diagonal neighbour.
            # Orthogonal coverage uses the full 4-anchor neighbourhood of each
            #   corner: a 1×1 road at anchor (ax,ay) covers [ax,ax+1]×[ay,ay+1],
            #   so corner (px,py) is covered when any of the 4 anchors
            #   {px-1,px}×{py-1,py} is present in ortho_road_pos.
            if (is_road and nw == 2 and nh == 2
                    and diag_road_pos is not None and ortho_road_pos is not None):
                gx_r = math.floor(pb.grid_x)
                gy_r = math.floor(pb.grid_y)
                # Diagonal neighbours; each covers exactly two diamond corners:
                #   NE (−1,−1) → T and L   NW (−1,+1) → L and B
                #   SE (+1,−1) → T and R   SW (+1,+1) → R and B
                ne  = (gx_r - 1, gy_r - 1) in diag_road_pos
                sw  = (gx_r + 1, gy_r + 1) in diag_road_pos
                nw_ = (gx_r - 1, gy_r + 1) in diag_road_pos
                se  = (gx_r + 1, gy_r - 1) in diag_road_pos
                if ne + sw + nw_ + se == 1:
                    # Corner canvas positions (in grid tile units from anchor):
                    #   T → (gx+1, gy)   R → (gx+2, gy+1)
                    #   B → (gx+1, gy+2) L → (gx,   gy+1)
                    # A 1×1 ortho tile at (ax,ay) covers corner (px,py) when
                    # px-1 ≤ ax ≤ px  AND  py-1 ≤ ay ≤ py.
                    def _oc(px, py):  # ortho covers corner (px,py)?
                        return (px, py) in ortho_road_pos
                    t_ok = ne or se or _oc(gx_r + 1, gy_r    )
                    r_ok = sw or se or _oc(gx_r + 2, gy_r + 1)
                    b_ok = sw or nw_ or _oc(gx_r + 1, gy_r + 2)
                    l_ok = ne or nw_ or _oc(gx_r,     gy_r + 1)
                    miss = (not t_ok, not r_ok, not b_ok, not l_ok)
                    if miss.count(True) == 1:
                        # Clip only the 2 outer sub-triangles of the missing corner by replacing that corner with the midpoints of its two adjacent edges.
                        # This preserves the inner sub-triangles that belong to the adjacent covered directions (T, B, etc.).
                        h = ts / 2  # edge midpoint offset
                        if miss[0]:   # T missing → mid_TL, mid_TR, R, B, L
                            pts = [cx - h, cy - h,  cx + h, cy - h,
                                   cx + ts, cy,
                                   cx, cy + ts,
                                   cx - ts, cy]
                        elif miss[1]: # R missing → T, mid_TR, mid_RB, B, L
                            pts = [cx, cy - ts,
                                   cx + h, cy - h,  cx + h, cy + h,
                                   cx, cy + ts,
                                   cx - ts, cy]
                        elif miss[2]: # B missing → T, R, mid_RB, mid_BL, L
                            pts = [cx, cy - ts,
                                   cx + ts, cy,
                                   cx + h, cy + h,  cx - h, cy + h,
                                   cx - ts, cy]
                        else:         # L missing → T, R, B, mid_BL, mid_TL
                            pts = [cx, cy - ts,
                                   cx + ts, cy,
                                   cx, cy + ts,
                                   cx - h, cy + h,  cx - h, cy - h]

            fill_45 = road_fill if road_fill is not None else cat_color
            tag = 'ghost' if ghost else f'bld_{pb.instance_id}'
            if ghost:
                c.create_polygon(pts, fill=fill_45, outline=FG_GOLD, width=1, dash=(4, 4), tags=tag)
            else:
                if selected:
                    outline_c = FG_GOLD
                elif in_range:
                    outline_c = '#2ecc71'
                elif is_road and not self.road_show_outline.get():
                    outline_c = fill_45
                else:
                    outline_c = '#000000'
                c.create_polygon(pts, fill=fill_45, outline=outline_c, width=3 if selected else 2, tags=tag)
                if selected:
                    # Inner dashed gold ring (shrink each point toward center by ~3px)
                    shrink = 3.0
                    s_pts = []
                    for i in range(0, len(pts), 2):
                        vx = pts[i] - cx
                        vy = pts[i + 1] - cy
                        mag = math.hypot(vx, vy) or 1
                        s_pts.extend([pts[i] - vx / mag * shrink, pts[i + 1] - vy / mag * shrink])
                    c.create_polygon(s_pts, fill='', outline=FG_GOLD,
                                     width=1, dash=(3, 3), tags=tag)

            if road_fill is None and not skip_module_icon and ts >= 8:
                icon_px = max(8, int(bbox_px * 0.65))
                icon = self._get_icon(bd, icon_px)
                if icon:
                    c.create_image(cx, cy, image=icon, anchor='center', tags='ghost' if ghost else f'bld_{pb.instance_id}')
                elif ts >= 16:
                    abbrev = bd.get_name(self.app.language)[:3]
                    c.create_text(cx, cy, text=abbrev, fill=FG_MAIN, font=FONT_XSMALL, tags='ghost' if ghost else f'bld_{pb.instance_id}')


    def _draw_ghost(self):
        if self._paste_active:
            self._draw_paste_ghost()
            return
        if self.build_mode_guid is None:
            return
        if self._house_drag_active:
            self._draw_multi_ghost(self._house_drag_positions)
            return
        if self._module_rect_active:
            self._draw_multi_ghost(self._module_rect_positions)
            return
        if self._line_start is not None and self._ghost_grid_pos is not None:
            bd = self.dm.get_building(self.build_mode_guid)
            if bd:
                positions = self._compute_line_positions(bd, self._line_start, self._ghost_grid_pos, self.build_rotation)
                self._draw_multi_ghost(positions)
            return
        if self._ghost_grid_pos is None:
            return
        gx, gy = self._ghost_grid_pos
        ghost_pb = PlacedBuilding(self.build_mode_guid, gx, gy, self.build_rotation, instance_id=-1)
        # Check collision
        bd = self.dm.get_building(self.build_mode_guid)
        if bd:
            collision = (self._check_collision(bd, gx, gy, self.build_rotation)
                         or self._check_module_radius(bd, gx, gy))
            old_sel = set(self.selected_ids)
            self.selected_ids = set()
            self._draw_placed_building(ghost_pb, ghost=True)
            self.selected_ids = old_sel
            if collision:
                self._draw_collision_tint(bd, gx, gy, self.build_rotation)

    def _draw_collision_tint(self, bd: BuildingData, gx: float, gy: float, rotation: int):
        """Red stipple overlay marking a ghost position as blocked by a collision."""
        c = self.canvas
        cx0, cy0 = self.grid_to_canvas(gx, gy)
        rot = rotation % 360
        if rot in (0, 90, 180, 270):
            w, h = (bd.width, bd.height) if rot in (0, 180) else (bd.height, bd.width)
            c.create_rectangle(
                cx0, cy0, cx0 + w * self.tile_size, cy0 + h * self.tile_size,
                fill='#ff0000', outline='#ff0000', width=2,
                stipple='gray25', dash=(4, 2), tags='ghost'
            )
        else:
            ts = self.tile_size
            nw, nh = _get_45_grid_counts(bd, rot)
            half = 0.25 * ts
            bbox_px = (nw + nh) * half
            ccx = cx0 + bbox_px
            ccy = cy0 + bbox_px
            pts = [
                ccx + (nh - nw) * half, ccy - (nw + nh) * half,
                ccx + (nw + nh) * half, ccy + (nw - nh) * half,
                ccx + (nw - nh) * half, ccy + (nw + nh) * half,
                ccx - (nw + nh) * half, ccy + (nh - nw) * half,
            ]
            c.create_polygon(pts, fill='#ff0000', outline='#ff0000', width=2, stipple='gray25', dash=(4, 2), tags='ghost')

    def _draw_multi_ghost(self, positions: list):
        """Draw a ghost preview for each position in a house-block, module-rect, or straight-line drag."""
        if not positions:
            return
        bd = self.dm.get_building(self.build_mode_guid)
        if not bd:
            return
        old_sel = set(self.selected_ids)
        self.selected_ids = set()
        for gx, gy in positions:
            ghost_pb = PlacedBuilding(self.build_mode_guid, gx, gy, self.build_rotation, instance_id=-1)
            self._draw_placed_building(ghost_pb, ghost=True)
            if self._check_collision(bd, gx, gy, self.build_rotation):
                self._draw_collision_tint(bd, gx, gy, self.build_rotation)
        self.selected_ids = old_sel

    def _draw_paste_ghost(self):
        """Draw the previewed multi-building paste group, translated to follow the cursor as a single unit (each item keeps its own type/rotation)."""
        if not self._paste_clipboard or self._paste_ghost_pos is None:
            return
        ox = self._paste_ghost_pos[0] - self._paste_anchor_orig[0]
        oy = self._paste_ghost_pos[1] - self._paste_anchor_orig[1]
        old_sel = set(self.selected_ids)
        self.selected_ids = set()
        for pb in self._paste_clipboard:
            bd = self.dm.get_building(pb.guid)
            if not bd:
                continue
            gx, gy = pb.grid_x + ox, pb.grid_y + oy
            ghost_pb = PlacedBuilding(pb.guid, gx, gy, pb.rotation, instance_id=-1)
            self._draw_placed_building(ghost_pb, ghost=True)
            if self._check_collision(bd, gx, gy, pb.rotation):
                self._draw_collision_tint(bd, gx, gy, pb.rotation)
        self.selected_ids = old_sel

    @staticmethod
    def _footprint_rect(bd: BuildingData, pb: 'PlacedBuilding'):
        """Return (x0, y0, x1, y1) axis-aligned footprint in grid coords."""
        rot = pb.rotation % 360
        if rot in (0, 90, 180, 270):
            w = bd.width  if rot in (0, 180) else bd.height
            h = bd.height if rot in (0, 180) else bd.width
        else:
            nw, nh = _get_45_grid_counts(bd, rot)
            w = h = (nw + nh) * 0.5
        return pb.grid_x, pb.grid_y, pb.grid_x + w, pb.grid_y + h

    @staticmethod
    def _manhattan_dist_from_rect(x0, y0, x1, y1, px, py) -> float:
        """Manhattan distance from axis-aligned rect [x0,x1]×[y0,y1] to point (px,py)."""
        dx = max(0.0, x0 - px, px - x1)
        dy = max(0.0, y0 - py, py - y1)
        return dx + dy

    def _get_road_graph(self):
        """Return (cached) connected-road-network graph for StreetDistance BFS.

        Roads are the only "streets" - aqueduct arches and drainage channels are infrastructure, not walkable streets, so they're excluded.

        Adjacency is determined precisely (via polygon touch/overlap, see _polys_touch) rather than by integer-cell proximity:
        a 45°-rotated road's occupied-cell footprint spans a 2×2 block (roads are special- cased to a 2×2 45°-grid size),
        so consecutive diagonal tiles' coarse cell sets already overlap by one cell - an 8-directional cell-distance
        check on top of that would create false "shortcut" edges skipping real hops. The coarse cell map is kept only as a broad-phase filter
        to avoid an O(n²) precise check across the whole layout.

        Returns:
            tile_to_roads:  (col,row) -> set of road instance_ids on that tile
            road_neighbors: instance_id -> set of adjacent road instance_ids
            road_polys:     instance_id -> flat polygon points (for footprint checks)
            road_cost:      instance_id -> StreetDistance budget cost of stepping
                            onto that tile (see _road_street_distance_cost -
                            paved/marble roads cost less, extending reach 1.5x)
        """
        if not self._road_graph_dirty and self._road_graph is not None:
            return self._road_graph

        tile_to_roads: dict = {}
        road_tiles: dict = {}
        road_polys: dict = {}
        road_cost: dict = {}
        for pb in self.placed_buildings:
            bd = self.dm.get_building(pb.guid)
            if not bd or 'Road' not in bd.get_category_english():
                continue
            tiles = _get_occupied_tiles(bd, pb.grid_x, pb.grid_y, pb.rotation)
            road_tiles[pb.instance_id] = tiles
            road_polys[pb.instance_id] = self._get_poly_pts(bd, pb.grid_x, pb.grid_y, pb.rotation)
            road_cost[pb.instance_id] = _road_street_distance_cost(bd)
            for t in tiles:
                tile_to_roads.setdefault(t, set()).add(pb.instance_id)

        road_neighbors = {rid: set() for rid in road_tiles}
        checked_pairs = set()
        for rid, tiles in road_tiles.items():
            candidates = set()
            for (col, row) in tiles:
                for dc in (-1, 0, 1):
                    for dr in (-1, 0, 1):
                        candidates |= tile_to_roads.get((col + dc, row + dr), set())
            candidates.discard(rid)
            for other_id in candidates:
                pair = (rid, other_id) if rid < other_id else (other_id, rid)
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)
                if self._polys_touch(road_polys[rid], road_polys[other_id]):
                    road_neighbors[rid].add(other_id)
                    road_neighbors[other_id].add(rid)

        # Include road_tiles (rid → set of (col,row)) so _incremental_road_graph_update
        # can remove evicted roads without scanning the full tile_to_roads dict.
        self._road_graph = (tile_to_roads, road_tiles, road_neighbors, road_polys, road_cost)
        self._road_graph_dirty = False
        return self._road_graph

    def _incremental_road_graph_update(self, pb_add=None, evict_ids=()):
        """Update the cached road graph for one new road tile and/or evictions.

        Called instead of setting _road_graph_dirty so the expensive O(n²)
        full rebuild is avoided. Each call is O(k) where k is the number of
        candidate neighbor tiles (~6 for a 1×1 road), regardless of layout size.

        pb_add   – PlacedBuilding just added (road); None if only evicting.
        evict_ids – iterable of instance_ids being removed from placed_buildings.
        """
        if self._road_graph is None or self._road_graph_dirty:
            # No cached graph yet; let the next caller do a full build.
            return

        tile_to_roads, road_tiles, road_neighbors, road_polys, road_cost = self._road_graph

        # --- Evict removed roads from every data structure ---
        for rid in evict_ids:
            if rid not in road_tiles:
                continue
            for nb in list(road_neighbors.get(rid, ())):
                road_neighbors.get(nb, set()).discard(rid)
            for t in road_tiles[rid]:
                s = tile_to_roads.get(t)
                if s:
                    s.discard(rid)
                    if not s:
                        del tile_to_roads[t]
            road_tiles.pop(rid, None)
            road_polys.pop(rid, None)
            road_cost.pop(rid, None)
            road_neighbors.pop(rid, None)

        # --- Add new road tile ---
        if pb_add is not None:
            bd = self.dm.get_building(pb_add.guid)
            if bd and 'Road' in bd.get_category_english():
                rid_new = pb_add.instance_id
                tiles_new = _get_occupied_tiles(bd, pb_add.grid_x, pb_add.grid_y, pb_add.rotation)
                poly_new  = self._get_poly_pts(bd, pb_add.grid_x, pb_add.grid_y, pb_add.rotation)
                cost_new  = _road_street_distance_cost(bd)
                road_tiles[rid_new]     = tiles_new
                road_polys[rid_new]     = poly_new
                road_cost[rid_new]      = cost_new
                road_neighbors[rid_new] = set()
                for t in tiles_new:
                    tile_to_roads.setdefault(t, set()).add(rid_new)
                # Find neighbors: only roads within one tile of any occupied tile
                candidates: set = set()
                for (col, row) in tiles_new:
                    for dc in (-1, 0, 1):
                        for dr in (-1, 0, 1):
                            candidates |= tile_to_roads.get((col + dc, row + dr), set())
                candidates.discard(rid_new)
                for other_id in candidates:
                    if self._polys_touch(poly_new, road_polys[other_id]):
                        road_neighbors[rid_new].add(other_id)
                        road_neighbors[other_id].add(rid_new)

        # Tuple is mutable in-place (dicts share identity), so no reassignment needed.

    def _roads_touching_footprint(self, bd: BuildingData, gx: float, gy: float, rotation: int, tile_to_roads: dict, road_polys: dict) -> set:
        """Return road instance_ids whose footprint precisely touches/overlaps the footprint of a building at gx,gy,rotation - its "street access" tiles."""
        poly = self._get_poly_pts(bd, gx, gy, rotation)
        tiles = _get_occupied_tiles(bd, gx, gy, rotation)
        candidates = set()
        for (col, row) in tiles:
            for dc in (-1, 0, 1):
                for dr in (-1, 0, 1):
                    candidates |= tile_to_roads.get((col + dc, row + dr), set())
        return {rid for rid in candidates if self._polys_touch(poly, road_polys[rid])}

    @staticmethod
    def _street_distance_bfs(start_road_ids: set, road_neighbors: dict, road_cost: dict, max_hops: float) -> dict:
        """Weighted shortest-path search over the road graph from a set of
        starting road tiles, capped at max_hops. Each tile's own cost (see
        _road_street_distance_cost) determines how much of the budget
        stepping onto it consumes - paved/marble roads cost less, so a
        building's reach effectively extends 1.5x further along them than
        along dirt roads, instead of every tile costing a uniform 1 hop.
        Returns {instance_id: cumulative_cost} for every road reached
        within budget."""
        if max_hops <= 0:
            return {}
        dist: dict = {}
        heap = []
        for rid in start_road_ids:
            cost = road_cost.get(rid, 1.0)
            if cost <= max_hops:
                dist[rid] = cost
                heapq.heappush(heap, (cost, rid))
        while heap:
            d, rid = heapq.heappop(heap)
            if d > dist.get(rid, math.inf):
                continue  # stale entry: a shorter path to this tile was already found
            for nb in road_neighbors.get(rid, ()):
                nd = d + road_cost.get(nb, 1.0)
                if nd <= max_hops and nd < dist.get(nb, math.inf):
                    dist[nb] = nd
                    heapq.heappush(heap, (nd, nb))
        return dist

    def _compute_in_range_ids(self, bd_sel: BuildingData, gx: float, gy: float,
                               rotation: int, exclude_id: int = None,
                               r_val_override: float = None) -> set:
        """Return instance_ids of (non-road, non-module) buildings within
        bd_sel's effect radius, for a building at gx,gy,rotation. Works for
        either a placed building (pass its instance_id as exclude_id) or a
        ghost preview (exclude_id=None). Pass r_val_override to use an
        effective radius different from the building's base value (e.g. after
        applying a tech range upgrade)."""
        if not isinstance(bd_sel.radius, dict):
            return set()
        r_type = bd_sel.radius.get('type', 'Radius')
        r_val  = r_val_override if r_val_override is not None else bd_sel.radius.get('value', 0)

        in_range = set()

        if r_type == 'Radius':
            scx, scy = self._building_center(bd_sel, gx, gy, rotation)
            for pb in self.placed_buildings:
                if pb.instance_id == exclude_id:
                    continue
                bd = self.dm.get_building(pb.guid)
                if not bd:
                    continue
                if bd.guid == bd_sel.guid:
                    continue  # same building type doesn't count as "affected"
                # Roads, channels, modules/fields, and purely decorative items
                # aren't meaningful targets for an effect radius.
                if _is_road_like(bd) or bd.get_category_english() in _RADIUS_EXCLUDED_CATEGORIES:
                    continue
                ocx, ocy = self._building_center(bd, pb.grid_x, pb.grid_y, pb.rotation)
                dx, dy = ocx - scx, ocy - scy
                if math.sqrt(dx * dx + dy * dy) <= r_val:
                    in_range.add(pb.instance_id)
        else:
            # StreetDistance: real graph distance along the connected road
            # network (1 hop per tile, regardless of orientation, but
            # paved/marble tiles cost less - see _road_street_distance_cost).
            tile_to_roads, _rt, road_neighbors, road_polys, road_cost = self._get_road_graph()
            start_roads = self._roads_touching_footprint(
                bd_sel, gx, gy, rotation, tile_to_roads, road_polys)
            reach = self._street_distance_bfs(start_roads, road_neighbors, road_cost, r_val)
            if not reach:
                return set()
            reach_set = set(reach.keys())

            # Bounding box of tiles reachable by the BFS.  Buildings whose
            # footprint lies entirely outside this box (plus a 2-tile margin
            # for the adjacency checks inside _roads_touching_footprint) cannot
            # touch any reachable road — skip them without calling _polys_touch.
            # On a 250×250 tile island with a 10-tile radius this filters ~99 %
            # of buildings, collapsing the old O(n) 1500 ms loop to ~15 ms.
            min_col = min_row =  float('inf')
            max_col = max_row = -float('inf')
            for tile, rids in tile_to_roads.items():
                if rids & reach_set:
                    col, row = tile
                    if col < min_col: min_col = col
                    if col > max_col: max_col = col
                    if row < min_row: min_row = row
                    if row > max_row: max_row = row
            min_col -= 2;  max_col += 2
            min_row -= 2;  max_row += 2

            for pb in self.placed_buildings:
                if pb.instance_id == exclude_id:
                    continue
                bd = self.dm.get_building(pb.guid)
                if not bd:
                    continue
                if bd.guid == bd_sel.guid:
                    continue
                if _is_road_like(bd) or bd.get_category_english() in _RADIUS_EXCLUDED_CATEGORIES:
                    continue
                # Spatial pre-filter using footprint bounding box
                x0, y0, x1, y1 = self._footprint_rect(bd, pb)
                if (math.ceil(x1) < min_col or math.floor(x0) > max_col or
                        math.ceil(y1) < min_row or math.floor(y0) > max_row):
                    continue
                touching = self._roads_touching_footprint(
                    bd, pb.grid_x, pb.grid_y, pb.rotation, tile_to_roads, road_polys)
                if touching & reach_set:
                    in_range.add(pb.instance_id)

        return in_range

    def _get_in_range_ids(self) -> set:
        """Return instance_ids of buildings within the selected building's effect radius."""
        if len(self.selected_ids) != 1:
            return set()
        iid = next(iter(self.selected_ids))
        pb_sel = next((p for p in self.placed_buildings if p.instance_id == iid), None)
        if not pb_sel:
            return set()
        bd_sel = self.dm.get_building(pb_sel.guid)
        if not bd_sel:
            return set()
        r_val_override = self._effective_r_val(bd_sel, iid)
        return self._compute_in_range_ids(bd_sel, pb_sel.grid_x, pb_sel.grid_y,
                                          pb_sel.rotation, exclude_id=iid,
                                          r_val_override=r_val_override)

    def _effective_r_val(self, bd: BuildingData, instance_id: int) -> float | None:
        """Return the effective radius value for a placed building after applying
        any active tech range upgrades, or None if no upgrade is active."""
        if not isinstance(bd.radius, dict):
            return None
        active_tech = self._active_tech_effects.get(instance_id, set())
        if not active_tech:
            return None
        mult = self.dm.get_range_multiplier(bd.guid, active_tech)
        if mult == 1.0:
            return None
        return bd.radius.get('value', 0) * mult

    def get_in_range_count(self, bd_sel: BuildingData, gx: float, gy: float,
                            rotation: int, exclude_id: int = None,
                            active_tech_guids: set = None) -> int:
        """Public helper for UI panels: number of buildings affected by
        bd_sel's effect radius if placed/positioned at gx,gy,rotation."""
        r_val_override = None
        if active_tech_guids and isinstance(bd_sel.radius, dict):
            mult = self.dm.get_range_multiplier(bd_sel.guid, active_tech_guids)
            if mult != 1.0:
                r_val_override = bd_sel.radius.get('value', 0) * mult
        return len(self._compute_in_range_ids(bd_sel, gx, gy, rotation, exclude_id,
                                              r_val_override))

    def get_in_range_guids(self, bd_sel: BuildingData, gx: float, gy: float,
                            rotation: int, exclude_id: int = None,
                            active_tech_guids: set = None) -> list:
        """Return building GUIDs of all buildings within bd_sel's effect radius."""
        r_val_override = None
        if active_tech_guids and isinstance(bd_sel.radius, dict):
            mult = self.dm.get_range_multiplier(bd_sel.guid, active_tech_guids)
            if mult != 1.0:
                r_val_override = bd_sel.radius.get('value', 0) * mult
        ids = self._compute_in_range_ids(bd_sel, gx, gy, rotation, exclude_id,
                                         r_val_override)
        return [pb.guid for pb in self.placed_buildings if pb.instance_id in ids]

    def _draw_radius_overlay(self):
        """Draw effect-radius/module-radius indicators for either the single
        selected placed building, or - if none is selected - the building
        currently being positioned in ghost/build mode."""
        if len(self.selected_ids) == 1:
            iid = next(iter(self.selected_ids))
            pb = next((p for p in self.placed_buildings if p.instance_id == iid), None)
            if not pb:
                return
            bd = self.dm.get_building(pb.guid)
            if not bd:
                return
            self._draw_radius_rings(bd, pb.grid_x, pb.grid_y, pb.rotation,
                                    r_val_override=self._effective_r_val(bd, iid))
        elif (self.build_mode_guid is not None and self._ghost_grid_pos is not None
              and not self._house_drag_active and not self._module_rect_active
              and not self._paste_active and self._line_start is None):
            bd = self.dm.get_building(self.build_mode_guid)
            if not bd:
                return
            gx, gy = self._ghost_grid_pos
            self._draw_radius_rings(bd, gx, gy, self.build_rotation, canvas_tag='ghost')

    def _draw_radius_rings(self, bd: BuildingData, gx: float, gy: float, rotation: int,
                           r_val_override: float = None, canvas_tag: str = 'world'):
        """Draw the effect-radius/module-radius/free-area rings for a building
        at the given grid anchor (used for both placed and ghost previews)."""
        has_radius     = isinstance(bd.radius, dict)
        has_mod_radius = bool(bd.module_build_radius)
        has_free_radius = bool(bd.free_area_productivity
                               and isinstance(bd.free_area_productivity, dict)
                               and bd.free_area_productivity.get('influenceRadius'))
        if not has_radius and not has_mod_radius and not has_free_radius:
            return

        c  = self.canvas
        ts = self.tile_size
        gcx, gcy = self._building_center(bd, gx, gy, rotation)
        cx, cy   = self.grid_to_canvas(gcx, gcy)

        # Light mode's white background washes out the dark-theme palette
        # (gold/pale-blue/green all read as low-contrast on white), so use
        # darker, thicker, denser-dashed lines there instead.
        light = self.light_mode.get()
        eff_color  = '#b8860b' if light else FG_GOLD
        mod_color  = '#0d47a1' if light else '#4fc3f7'
        free_color = '#1b5e20' if light else '#4caf50'
        line_w     = 3 if light else 2
        eff_dash   = (10, 3) if light else (8, 4)
        thin_dash  = (8, 3) if light else (4, 4)

        if has_radius:
            r_type = bd.radius.get('type', 'Radius')
            r_val  = r_val_override if r_val_override is not None else bd.radius.get('value', 0)
            r_px   = r_val * ts
            if r_type == 'Radius':
                c.create_oval(cx - r_px, cy - r_px, cx + r_px, cy + r_px,
                              outline=eff_color, fill='', width=line_w, dash=eff_dash,
                              tags=('radius_overlay', canvas_tag))
            else:
                # StreetDistance – real graph distance along the connected
                # road network (paved/marble tiles cost less, see
                # _road_street_distance_cost), not a geometric shape, so
                # highlight every road tile actually within reach instead.
                tile_to_roads, _rt, road_neighbors, road_polys, road_cost = self._get_road_graph()
                start_roads = self._roads_touching_footprint(
                    bd, gx, gy, rotation, tile_to_roads, road_polys)
                reach = self._street_distance_bfs(start_roads, road_neighbors, road_cost, r_val)
                # Build a one-shot id→pb dict to avoid an O(n) list scan per road.
                pb_by_id = {p.instance_id: p for p in self.placed_buildings}
                for rid in reach:
                    rpb = pb_by_id.get(rid)
                    if not rpb:
                        continue
                    rbd = self.dm.get_building(rpb.guid)
                    if not rbd:
                        continue
                    self._draw_reach_outline(rbd, rpb.grid_x, rpb.grid_y,
                                             rpb.rotation, eff_color, line_w,
                                             canvas_tag=canvas_tag)

        if has_mod_radius:
            # In-game the module radius is a true circle from the building's
            # centre, but inflated by half of the *shorter* footprint side -
            # i.e. R = moduleBuildRadius + min(width, height) / 2.
            r_eff = bd.module_build_radius + min(bd.width, bd.height) / 2
            r_px = r_eff * ts
            c.create_oval(cx - r_px, cy - r_px, cx + r_px, cy + r_px,
                          outline=mod_color, fill='', width=line_w, dash=thin_dash,
                          tags=('radius_overlay', canvas_tag))

        if has_free_radius:
            r_px = bd.free_area_productivity['influenceRadius'] * ts
            c.create_oval(cx - r_px, cy - r_px, cx + r_px, cy + r_px,
                          outline=free_color, fill='', width=line_w, dash=thin_dash,
                          tags=('radius_overlay', canvas_tag))

    def _draw_reach_outline(self, bd: BuildingData, gx: float, gy: float,
                            rotation: int, color: str, line_w: int,
                            canvas_tag: str = 'world'):
        """Outline a building's footprint (rect or 45° diamond) in the given
        colour - used to highlight road tiles reachable within a
        StreetDistance budget."""
        c = self.canvas
        ts = self.tile_size
        cx0, cy0 = self.grid_to_canvas(gx, gy)
        rot = rotation % 360
        if rot in (0, 90, 180, 270):
            w, h = (bd.width, bd.height) if rot in (0, 180) else (bd.height, bd.width)
            c.create_rectangle(cx0, cy0, cx0 + w * ts, cy0 + h * ts,
                               outline=color, width=line_w,
                               tags=('radius_overlay', canvas_tag))
        else:
            nw, nh = _get_45_grid_counts(bd, rot)
            half = 0.25 * ts
            bbox_px = (nw + nh) * half
            ccx, ccy = cx0 + bbox_px, cy0 + bbox_px
            pts = [
                ccx + (nh - nw) * half, ccy - (nw + nh) * half,
                ccx + (nw + nh) * half, ccy + (nw - nh) * half,
                ccx + (nw - nh) * half, ccy + (nw + nh) * half,
                ccx - (nw + nh) * half, ccy + (nh - nw) * half,
            ]
            c.create_polygon(pts, outline=color, fill='', width=line_w,
                             tags=('radius_overlay', canvas_tag))

    def _get_icon(self, bd: BuildingData, size: int):
        """Load and return a PhotoImage for the building icon."""
        if not PIL_AVAILABLE:
            return None
        size = max(8, min(size, 128))
        key = (bd.guid, size)
        if key in self._photo_cache:
            return self._photo_cache[key]

        icon_path = resource_path(bd.icon_path) if bd.icon_path else None
        try:
            if icon_path and os.path.exists(icon_path):
                img = Image.open(icon_path).convert('RGBA')
                img = img.resize((size, size), Image.LANCZOS)
            else:
                raise FileNotFoundError
        except Exception:
            if not PIL_AVAILABLE:
                return None
            # Create placeholder
            img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rectangle([2, 2, size - 3, size - 3],
                           outline=(255, 255, 255, 120), width=1)
            abbrev = bd.get_name('english')[:2].upper()
            try:
                draw.text((size // 2, size // 2), abbrev,
                          fill=(255, 255, 255, 180), anchor='mm')
            except Exception:
                pass

        photo = ImageTk.PhotoImage(img)
        self._photo_cache[key] = photo
        return photo

    # ------------------------------------------------------------------ #
    #  Pan / Zoom
    # ------------------------------------------------------------------ #
    _pan_last_x = 0
    _pan_last_y = 0

    def _on_pan_start(self, event):
        self._pan_last_x = event.x
        self._pan_last_y = event.y
        self._pan_start_x = event.x
        self._pan_start_y = event.y
        self._pan_did_move = False
        self.canvas.config(cursor='fleur')
        # Suppress deferred redraws while panning to prevent mid-pan canvas blanks.
        if self._deferred_redraw_id is not None:
            self.after_cancel(self._deferred_redraw_id)
            self._deferred_redraw_id = None
        if self._deferred_island_bg_id is not None:
            self.after_cancel(self._deferred_island_bg_id)
            self._deferred_island_bg_id = None

    def _on_pan_move(self, event):
        dx = event.x - self._pan_last_x
        dy = event.y - self._pan_last_y
        if abs(event.x - self._pan_start_x) > 4 or abs(event.y - self._pan_start_y) > 4:
            self._pan_did_move = True
        self.pan_x += dx
        self.pan_y += dy
        self._pan_last_x = event.x
        self._pan_last_y = event.y
        self._redraw_pan()

    def _on_pan_end(self, event):
        if not self._pan_did_move:
            # Treat as a click: rotate build-mode ghost or selected buildings
            self.rotate_build(direction=1)
        cur = 'crosshair' if self.build_mode_guid else 'arrow'
        self.canvas.config(cursor=cur)
        # Island background is kept current by _fill_island_chunks() on each
        # pan event; no separate deferred refresh is needed any more.
        if self._layout_dirty:
            # If the layout is dirty (e.g. placement happened before this pan),
            # schedule the deferred full rebuild now that panning has stopped.
            self._schedule_deferred_redraw(80)

    def _on_mousewheel(self, event):
        # Determine zoom direction
        if event.num == 4 or event.delta > 0:
            factor = ZOOM_FACTOR
        else:
            factor = 1 / ZOOM_FACTOR

        old_ts = self.tile_size
        new_ts = max(MIN_TILE_SIZE, min(MAX_TILE_SIZE, old_ts * factor))
        if new_ts == old_ts:
            return

        # Zoom towards mouse position
        mx, my = event.x, event.y
        self.pan_x = mx - (mx - self.pan_x) * (new_ts / old_ts)
        self.pan_y = my - (my - self.pan_y) * (new_ts / old_ts)
        self.tile_size = new_ts
        self._photo_cache.clear()  # icon sizes change
        self._redraw()

    def _on_resize(self, event):
        self._redraw()

    # ------------------------------------------------------------------ #
    #  Deferred / incremental redraw helpers
    # ------------------------------------------------------------------ #
    def _schedule_deferred_redraw(self, delay_ms: int = 200):
        """Schedule a full _redraw() to run after `delay_ms`, cancelling any
        previously pending deferred rebuild. Used to settle visual state after
        incremental placement without blocking the interaction."""
        if self._deferred_redraw_id is not None:
            self.after_cancel(self._deferred_redraw_id)
        self._deferred_redraw_id = self.after(delay_ms, self._do_deferred_redraw)

    def _do_deferred_redraw(self):
        self._deferred_redraw_id = None
        self._redraw()

    def _refresh_island_bg(self):
        """Rebuild just the island background canvas image (no full redraw).
        Called after panning stops to fill in any leading-edge artifact."""
        self._deferred_island_bg_id = None
        c = self.canvas
        c.delete('island_bg')
        if self._island_tiles is not None:
            self._draw_island_bg()
            c.lower('island_bg')

    def _incremental_add_building(self, pb: 'PlacedBuilding',
                                   evict: set = None):
        """Draw one newly-placed building on the canvas without a full redraw.
        Sets _layout_dirty = False so panning keeps using the cheap fast path.

        evict – set of instance_ids whose canvas items and draw-order-cache
                entries must be removed (buildings replaced by this placement).
        """
        bd = self.dm.get_building(pb.guid)

        # ── Update draw order cache ──────────────────────────────────────
        if self._draw_order_cache is not None:
            # Remove evicted entries first
            if evict:
                self._draw_order_cache = [
                    e for e in self._draw_order_cache
                    if e.instance_id not in evict
                ]
            # Insert new building at the correct Z-order position.
            if bd is None:
                self._draw_order_cache.append(pb)
            else:
                pri = _road_priority(bd)
                is_quay = (bd.get_category_english() == 'Ground Patterns'
                           and bd.width == 1 and bd.height == 1)
                if is_quay:
                    new_key = 0
                elif pri > 0:
                    new_key = pri  # 1, 2, or 3 for road-like tiles
                else:
                    new_key = 10  # regular buildings always on top
                if new_key == 10:
                    self._draw_order_cache.append(pb)
                else:
                    # Insert before the first entry with a higher priority value.
                    inserted = False
                    for i, existing in enumerate(self._draw_order_cache):
                        ebd = self.dm.get_building(existing.guid)
                        if ebd is None:
                            existing_key = 10
                        else:
                            epri = _road_priority(ebd)
                            if epri > 0:
                                existing_key = epri
                            elif (ebd.get_category_english() == 'Ground Patterns'
                                  and ebd.width == 1 and ebd.height == 1):
                                existing_key = 0
                            else:
                                existing_key = 10
                        if existing_key > new_key:
                            self._draw_order_cache.insert(i, pb)
                            inserted = True
                            break
                    if not inserted:
                        self._draw_order_cache.append(pb)

        # ── Update road position cache ───────────────────────────────────
        if self._road_pos_cache is not None:
            diag_set, ortho_set = self._road_pos_cache
            if evict:
                # We can't easily remove evicted positions without knowing
                # whether another road still occupies the same tile, so drop
                # the cache and let _draw_buildings() rebuild it on demand.
                self._road_pos_cache = None
                diag_set, ortho_set = set(), set()
            if bd is not None and _is_road_like(bd) and not pb.nibble:
                pos = (math.floor(pb.grid_x), math.floor(pb.grid_y))
                if pb.rotation % 360 not in (0, 90, 180, 270):
                    diag_set.add(pos)
                else:
                    ortho_set.add(pos)

        # ── Draw the new building ────────────────────────────────────────
        parent_color_ranks = self._get_parent_color_ranks()
        diag_pos, ortho_pos = self._road_pos_cache or (set(), set())
        self._draw_placed_building(
            pb,
            parent_color_ranks=parent_color_ranks,
            diag_road_pos=diag_pos,
            ortho_road_pos=ortho_pos,
        )
        self._layout_dirty = False

    # ------------------------------------------------------------------ #
    #  Island-background chunk helpers  /  Deferred stats update
    # ------------------------------------------------------------------ #
    def _get_island_chunk(self, cx: int, cy: int):
        """Return the cached PhotoImage for island background chunk (cx, cy).

        Each chunk covers _island_chunk_size × _island_chunk_size tiles and is
        ~224×224 px at ts≈7, making PhotoImage conversion ~2 ms instead of the
        ~200 ms needed for a full-canvas image.
        """
        if not PIL_AVAILABLE:
            return None
        ts     = self.tile_size
        light  = self.light_mode.get()
        ts_key = round(ts * 100)
        key    = (cx, cy, ts_key, light)
        photo  = self._island_chunk_photos.get(key)
        if photo is not None:
            return photo

        base = self._island_base_img_light if light else self._island_base_img_dark
        if base is None:
            return None

        cs     = self._island_chunk_size
        iw, ih = self._island_w, self._island_h
        left   = cx * cs
        top    = cy * cs
        right  = min(iw, left + cs)
        bottom = min(ih, top + cs)
        if right <= left or bottom <= top:
            return None

        crop   = base.crop((left, top, right, bottom))
        out_w  = max(1, math.ceil((right - left) * ts))
        out_h  = max(1, math.ceil((bottom - top) * ts))
        scaled = crop.resize((out_w, out_h), Image.NEAREST)

        if self._island_quads is not None and ts >= 4:
            self._render_island_quads(scaled, left, top, right, bottom, ts)

        photo = ImageTk.PhotoImage(scaled)
        self._island_chunk_photos[key] = photo
        return photo

    def _fill_island_chunks(self):
        """Create canvas items for island-background chunks now in the viewport
        but not yet drawn.  Called on each pan event; each new chunk takes ~2 ms
        to create at ts≈7 and is then cached for future pan events at the same
        zoom level.
        """
        if not PIL_AVAILABLE or self._island_tiles is None:
            return
        c   = self.canvas
        cw  = c.winfo_width()
        ch  = c.winfo_height()
        ts  = self.tile_size
        cs  = self._island_chunk_size
        px0 = self.pan_x
        py0 = self.pan_y

        cx_lo = max(0, int(math.floor(-px0 / (ts * cs))))
        cx_hi = int(math.ceil((cw - px0) / (ts * cs)))
        cy_lo = max(0, int(math.floor(-py0 / (ts * cs))))
        cy_hi = int(math.ceil((ch - py0) / (ts * cs)))

        added = False
        for cx in range(cx_lo, cx_hi + 1):
            for cy in range(cy_lo, cy_hi + 1):
                if (cx, cy) in self._drawn_island_chunks:
                    continue
                photo = self._get_island_chunk(cx, cy)
                if photo is None:
                    continue
                px = cx * cs * ts + px0
                py = cy * cs * ts + py0
                c.create_image(px, py, anchor='nw', image=photo, tags='island_bg')
                self._drawn_island_chunks.add((cx, cy))
                added = True

        if added:
            c.lower('island_bg')

    # ------------------------------------------------------------------ #
    #  Deferred stats-panel update
    # ------------------------------------------------------------------ #
    def _schedule_deferred_stats_update(self, delay_ms: int = 150):
        """Schedule a deferred info-panel stats refresh, collapsing rapid calls."""
        if self._deferred_stats_id is not None:
            self.after_cancel(self._deferred_stats_id)
        self._deferred_stats_id = self.after(delay_ms, self._do_deferred_stats_update)

    def _do_deferred_stats_update(self):
        self._deferred_stats_id = None
        if hasattr(self.app, 'info_panel'):
            self.app.info_panel.update_stats(self.get_layout_stats())

    def _try_place_road_at(self, gx: float, gy: float):
        """Place one road/module tile without pushing undo (used during drag-placement)."""
        bd = self.dm.get_building(self.build_mode_guid)
        if not bd or self._check_collision(bd, gx, gy, self.build_rotation):
            return
        if self._check_module_radius(bd, gx, gy):
            return
        # Block placement if it would polygon-overlap any tile placed in this drag session
        if self._road_drag_placed_ids:
            poly_new = self._get_poly_pts(bd, gx, gy, self.build_rotation)
            for dpb in self.placed_buildings:
                if dpb.instance_id not in self._road_drag_placed_ids:
                    continue
                drag_bd = self.dm.get_building(dpb.guid)
                if not drag_bd:
                    continue
                if self._polys_overlap(poly_new,
                                       self._get_poly_pts(drag_bd, dpb.grid_x,
                                                          dpb.grid_y, dpb.rotation)):
                    return
        evict = self._get_roads_to_evict(bd, gx, gy, self.build_rotation)
        if evict:
            self.placed_buildings = [p for p in self.placed_buildings
                                     if p.instance_id not in evict]
            for eid in evict:
                self.canvas.delete(f'bld_{eid}')
        pb = PlacedBuilding(self.build_mode_guid, gx, gy, self.build_rotation,
                            parent_id=self._module_parent_id)
        self.placed_buildings.append(pb)
        self._road_drag_placed_ids.add(pb.instance_id)
        self._road_drag_last_pos = (gx, gy)
        self._rebuild_collision(is_road_change=False, is_module_change=False,
                                preserve_draw_caches=True)
        self._incremental_road_graph_update(pb, evict)
        if hasattr(self.app, 'mark_dirty'):
            self.app.mark_dirty()
        self._schedule_deferred_stats_update()
        # Draw new road tile immediately (junction clips are deferred to drag-end).
        self._incremental_add_building(pb, evict=evict)

    def _surround_block_with_roads(self, gx: float, gy: float):
        """Surround the contiguous block of non-road buildings at (gx, gy) with roads."""
        col = int(math.floor(gx))
        row = int(math.floor(gy))

        # Build per-building tile sets (roads excluded from group membership)
        building_tiles: dict = {}   # iid -> set of (col, row)
        tile_to_iids:  dict = {}    # (col, row) -> list of iids
        for pb in self.placed_buildings:
            bd_pb = self.dm.get_building(pb.guid)
            if not bd_pb or _is_road_like(bd_pb):
                continue
            tiles = _get_occupied_tiles(bd_pb, pb.grid_x, pb.grid_y, pb.rotation)
            building_tiles[pb.instance_id] = tiles
            for t in tiles:
                tile_to_iids.setdefault(t, []).append(pb.instance_id)

        start_ids = set(tile_to_iids.get((col, row), []))
        if not start_ids:
            return

        # BFS: flood-fill the contiguous group via 4-cardinal tile adjacency
        group_ids: set = set(start_ids)
        queue = list(start_ids)
        while queue:
            iid = queue.pop()
            for (c, r) in building_tiles.get(iid, set()):
                for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    for nb_iid in tile_to_iids.get((c + dc, r + dr), []):
                        if nb_iid not in group_ids:
                            group_ids.add(nb_iid)
                            queue.append(nb_iid)

        # Union of all tiles belonging to the group
        group_tiles: set = set()
        for iid in group_ids:
            group_tiles |= building_tiles[iid]

        bd = self.dm.get_building(self.build_mode_guid)
        if not bd:
            return

        # Derive road rotation from the block's actual rotation family so that
        # clicking a 45°-family block always produces 45° roads and a 90°-family
        # block always produces 0° roads, regardless of what rotation the user
        # currently has selected in build mode.
        sample_pb = next((p for p in self.placed_buildings if p.instance_id in group_ids), None)
        block_is_45 = sample_pb is not None and (sample_pb.rotation % 90 != 0)
        road_rot = 45 if block_is_45 else 0
        is_45 = block_is_45

        if is_45:
            # Valid 45° anchor positions are at half-integer offsets. Walk a
            # half-unit grid over the expanded bounding box, snap each candidate
            # to the 45° grid, then keep only positions whose road UV bounding box
            # (u=x+y, v=x-y) touches the group's UV bounding box in both dimensions.
            #
            # UV-bounding-box overlap (using ≤, so touching counts) is the right
            # filter: side roads touch in one UV axis, the 4 outer-corner roads of
            # the ring touch in both, and truly outer-ring positions are strictly
            # separated in at least one UV axis.  Roads that end up inside the group
            # are caught and rejected by _check_collision below.

            margin = 2
            xs = [c for c, _ in group_tiles]
            ys = [r for _, r in group_tiles]
            x0, x1 = min(xs) - margin, max(xs) + margin + 2
            y0, y1 = min(ys) - margin, max(ys) + margin + 2

            # Collect group building polygons and compute the group's UV bounding box.
            iid_to_pb = {pb.instance_id: pb for pb in self.placed_buildings}
            group_polys = []
            for iid in group_ids:
                pb_g = iid_to_pb.get(iid)
                if pb_g:
                    bd_g = self.dm.get_building(pb_g.guid)
                    if bd_g:
                        group_polys.append(
                            self._get_poly_pts(bd_g, pb_g.grid_x, pb_g.grid_y, pb_g.rotation))

            if not group_polys:
                return
            g_u_min = min(p[j] + p[j+1] for p in group_polys for j in range(0, len(p), 2))
            g_u_max = max(p[j] + p[j+1] for p in group_polys for j in range(0, len(p), 2))
            g_v_min = min(p[j] - p[j+1] for p in group_polys for j in range(0, len(p), 2))
            g_v_max = max(p[j] - p[j+1] for p in group_polys for j in range(0, len(p), 2))

            seen_snapped: set = set()
            perimeter_pos: list = []
            for ix2 in range(int(x0 * 2), int(x1 * 2) + 1):
                for iy2 in range(int(y0 * 2), int(y1 * 2) + 1):
                    raw_x, raw_y = ix2 * 0.5, iy2 * 0.5
                    sx, sy = self.snap_to_grid(raw_x, raw_y, road_rot, bd)
                    key = (round(sx * 4), round(sy * 4))
                    if key in seen_snapped:
                        continue
                    seen_snapped.add(key)
                    road_poly = self._get_poly_pts(bd, sx, sy, road_rot)
                    r_u_min = min(road_poly[j] + road_poly[j+1] for j in range(0, len(road_poly), 2))
                    r_u_max = max(road_poly[j] + road_poly[j+1] for j in range(0, len(road_poly), 2))
                    r_v_min = min(road_poly[j] - road_poly[j+1] for j in range(0, len(road_poly), 2))
                    r_v_max = max(road_poly[j] - road_poly[j+1] for j in range(0, len(road_poly), 2))
                    if (r_u_min <= g_u_max and r_u_max >= g_u_min and
                            r_v_min <= g_v_max and r_v_max >= g_v_min):
                        perimeter_pos.append((sx, sy))
        else:
            # 90° roads: integer-tile 8-directional perimeter
            perimeter_set: set = set()
            for (c, r) in group_tiles:
                for dc in (-1, 0, 1):
                    for dr in (-1, 0, 1):
                        if dc == 0 and dr == 0:
                            continue
                        nb = (c + dc, r + dr)
                        if nb not in group_tiles:
                            perimeter_set.add(nb)
            perimeter_pos = [(float(c), float(r)) for c, r in perimeter_set]

        if not perimeter_pos:
            return

        self._push_undo()
        placed_any = False
        session_polys: list = []
        for (pgx, pgy) in perimeter_pos:
            if self._check_collision(bd, pgx, pgy, road_rot):
                continue
            if self._check_module_radius(bd, pgx, pgy):
                continue
            # 45° roads coexist with each other so _check_collision won't prevent
            # stacking; use a polygon overlap check against already-placed roads.
            if is_45 and session_polys:
                poly_new = self._get_poly_pts(bd, pgx, pgy, road_rot)
                if any(self._polys_overlap(poly_new, p) for p in session_polys):
                    continue
            evict = self._get_roads_to_evict(bd, pgx, pgy, road_rot)
            if evict:
                evict_pbs = {p.instance_id: p for p in self.placed_buildings
                             if p.instance_id in evict}
                self.placed_buildings = [p for p in self.placed_buildings
                                         if p.instance_id not in evict]
                for eid, epb in evict_pbs.items():
                    ebd = self.dm.get_building(epb.guid)
                    if ebd:
                        for t in _get_occupied_tiles(ebd, epb.grid_x, epb.grid_y,
                                                     epb.rotation):
                            ids = self._collision_map.get(t)
                            if ids:
                                ids[:] = [i for i in ids if i != eid]
            pb = PlacedBuilding(self.build_mode_guid, pgx, pgy, road_rot,
                                parent_id=self._module_parent_id)
            self.placed_buildings.append(pb)
            for t in _get_occupied_tiles(bd, pgx, pgy, road_rot):
                self._collision_map.setdefault(t, []).append(pb.instance_id)
            if is_45:
                session_polys.append(self._get_poly_pts(bd, pgx, pgy, road_rot))
            placed_any = True

        if placed_any:
            self._rebuild_collision()
            self._notify_layout_change()

    def _on_left_click(self, event):
        if self._paste_active:
            self._commit_paste()
            self._redraw()
            return

        if self.build_mode_guid is not None:
            bd = self.dm.get_building(self.build_mode_guid)
            if bd and _is_house(bd):
                # Residence block: drag out a max-2-wide, custom-length block of houses
                self._house_drag_active = True
                self._house_drag_anchor = self._ghost_grid_pos
                self._house_drag_positions = (
                    [self._ghost_grid_pos] if self._ghost_grid_pos is not None else [])
                self._redraw()
            elif bd and _is_road_like(bd) and self.line_mode.get():
                # Building click → surround block (takes priority over line tool)
                hit_iid = self._find_building_at(event.x, event.y)
                hit_pb  = next((p for p in self.placed_buildings
                                if p.instance_id == hit_iid), None) if hit_iid else None
                hit_bd  = self.dm.get_building(hit_pb.guid) if hit_pb else None
                if hit_bd and not _is_road_like(hit_bd):
                    self._line_start = None  # cancel any in-progress line
                    self._surround_block_with_roads(hit_pb.grid_x, hit_pb.grid_y)
                else:
                    # Straight-line tool: first click sets the start, second commits the line
                    if self._line_start is None:
                        self._line_start = self._ghost_grid_pos
                    elif self._ghost_grid_pos is not None:
                        positions = self._compute_line_positions(
                            bd, self._line_start, self._ghost_grid_pos, self.build_rotation)
                        self._commit_line(bd, positions)
                        self._line_start = None
                self._redraw()
            elif (bd and bd.width == 1 and bd.height == 1
                  and bd.get_category_english() in _DRAG_PLACEABLE_CATEGORIES
                  and self.module_rect_mode.get()):
                # Rectangle-fill mode for 1x1 modules/fields
                self._module_rect_active = True
                self._module_rect_anchor = self._ghost_grid_pos
                self._module_rect_positions = (
                    [self._ghost_grid_pos] if self._ghost_grid_pos is not None else [])
                self._redraw()
            elif bd and _is_drag_placeable(bd):
                # For road types: if the click lands directly on a non-road building,
                # surround that building's contiguous block with roads instead.
                if _is_road_like(bd):
                    hit_iid = self._find_building_at(event.x, event.y)
                    if hit_iid is not None:
                        hit_pb = next((p for p in self.placed_buildings
                                       if p.instance_id == hit_iid), None)
                        hit_bd = self.dm.get_building(hit_pb.guid) if hit_pb else None
                        if hit_bd and not _is_road_like(hit_bd):
                            self._surround_block_with_roads(hit_pb.grid_x, hit_pb.grid_y)
                            self._redraw()
                            return
                # Road/aqueduct/channel/module: push one undo for the whole drag sequence
                self._push_undo()
                self._road_drag_active = True
                self._road_drag_last_pos = None
                self._road_drag_placed_ids = set()
                if self._ghost_grid_pos is not None:
                    self._try_place_road_at(*self._ghost_grid_pos)
                # _try_place_road_at already drew the first tile via
                # _incremental_add_building.  Just refresh the ghost overlay.
                self.canvas.delete('ghost')
                self._draw_ghost()
            else:
                self._place_building(event)
            return

        if self.delete_mode.get():
            self._delete_at(event.x, event.y)
            return

        # Check if clicking on a building
        hit = self._find_building_at(event.x, event.y)
        if hit is not None:
            if event.state & 0x4:  # Ctrl: toggle in/out of selection
                if hit in self.selected_ids:
                    self.selected_ids.discard(hit)
                else:
                    self.selected_ids.add(hit)
            elif event.state & 0x1:  # Shift: add to selection (chaining)
                self.selected_ids.add(hit)
            else:
                if hit not in self.selected_ids:
                    self.selected_ids = {hit}
            self._drag_start_canvas = (event.x, event.y)
            # Parented modules move along with their parent building.
            self._drag_extra_ids = {
                pb.instance_id for pb in self.placed_buildings
                if pb.parent_id in self.selected_ids
            }
            self._drag_start_grids = {
                pb.instance_id: (pb.grid_x, pb.grid_y)
                for pb in self.placed_buildings
                if pb.instance_id in self.selected_ids or pb.instance_id in self._drag_extra_ids
            }
            self._is_dragging = False
            self._drag_moved = False
            self._drag_last_notify_grids = dict(self._drag_start_grids)
            self._notify_selection()
            self._redraw()
        else:
            # Start box select (clicking empty canvas space)
            self.selected_ids = set()
            self._box_sel_start = (event.x, event.y)
            self._box_sel_cur = (event.x, event.y)
            self._box_sel_rect = None
            self._notify_selection()
            self._redraw()
            bm = getattr(self.app, 'build_menu', None)
            if bm is not None:
                bm._close_all_popups()

    def _on_left_drag(self, event):
        if self.build_mode_guid is not None:
            self._update_ghost(event.x, event.y)
            if self._house_drag_active and self._ghost_grid_pos is not None:
                self._update_house_block()
            elif self._module_rect_active and self._ghost_grid_pos is not None:
                self._update_module_rect()
            elif self._road_drag_active and self._ghost_grid_pos is not None:
                if self._ghost_grid_pos != self._road_drag_last_pos:
                    self._try_place_road_at(*self._ghost_grid_pos)
                    # Incremental draw is done inside _try_place_road_at;
                    # full rebuild (for junctions) fires on drag release.
            return

        if self._drag_start_canvas is not None and self.selected_ids:
            # Move selected buildings (plus any modules parented to them)
            move_ids = self.selected_ids | self._drag_extra_ids
            dx_px = event.x - self._drag_start_canvas[0]
            dy_px = event.y - self._drag_start_canvas[1]
            dx_grid = dx_px / self.tile_size
            dy_grid = dy_px / self.tile_size

            if abs(dx_px) > 3 or abs(dy_px) > 3:
                self._is_dragging = True
                self._drag_moved = True

            if self._is_dragging:
                # Pass 1: compute candidate positions for all moving buildings.
                candidates = {}
                for pb in self.placed_buildings:
                    if pb.instance_id not in move_ids:
                        continue
                    bd = self.dm.get_building(pb.guid)
                    if not bd:
                        continue
                    orig_x, orig_y = self._drag_start_grids[pb.instance_id]
                    raw_x = orig_x + dx_grid
                    raw_y = orig_y + dy_grid
                    rot = pb.rotation % 360
                    if rot in (0, 90, 180, 270):
                        new_x = float(math.floor(raw_x + 0.5))
                        new_y = float(math.floor(raw_y + 0.5))
                    else:
                        nw, nh = _get_45_grid_counts(bd, rot)
                        new_x, new_y = self._snap_45_anchor(raw_x, raw_y, nw, nh)
                    candidates[pb.instance_id] = (new_x, new_y, bd, rot)

                # Pass 2: rigid-block move - all buildings move together or none do.
                # Check every candidate first; only commit if the whole group is clear.
                moving_pbs = [pb for pb in self.placed_buildings
                              if pb.instance_id in candidates]
                all_clear = True
                for pb in moving_pbs:
                    new_x, new_y, bd, rot = candidates[pb.instance_id]
                    if self._check_collision(bd, new_x, new_y, rot, exclude_ids=move_ids):
                        all_clear = False
                        break
                if all_clear:
                    for pb in moving_pbs:
                        new_x, new_y, bd, rot = candidates[pb.instance_id]
                        pb.grid_x = new_x
                        pb.grid_y = new_y
                self._rebuild_collision()
                # Live-refresh the panel only when buildings snapped to a new
                # grid position - skipping sub-tile mouse movements avoids the
                # repeated flash before a full tile has been crossed.
                current_grids = {pb.instance_id: (pb.grid_x, pb.grid_y)
                                 for pb in self.placed_buildings
                                 if pb.instance_id in move_ids}
                if current_grids != self._drag_last_notify_grids:
                    self._drag_last_notify_grids = current_grids
                    self._notify_selection()
                self._redraw()

        elif self._box_sel_start is not None:
            # Update box select
            self._box_sel_cur = (event.x, event.y)
            c = self.canvas
            if self._box_sel_rect:
                c.delete(self._box_sel_rect)
            x0, y0 = self._box_sel_start
            x1, y1 = self._box_sel_cur
            self._box_sel_rect = c.create_rectangle(
                x0, y0, x1, y1,
                outline=FG_GOLD, fill='', dash=(4, 4), tags='boxsel'
            )
            # Highlight buildings within box
            self.selected_ids = self._get_buildings_in_box(x0, y0, x1, y1)
            self._notify_selection()
            self._redraw()

    def _on_left_release(self, event):
        if self._house_drag_active:
            self._commit_house_block()
            self._house_drag_active = False
            self._house_drag_anchor = None
            self._house_drag_positions = []
            self._redraw()
            return

        if self._module_rect_active:
            self._commit_module_rect()
            self._module_rect_active = False
            self._module_rect_anchor = None
            self._module_rect_positions = []
            self._redraw()
            return

        if self._road_drag_active:
            self._road_drag_active = False
            self._road_drag_last_pos = None
            self._road_drag_placed_ids = set()
            # Full rebuild to render correct junction clips for placed roads.
            self._schedule_deferred_redraw(50)
            return

        if self._is_dragging and self._drag_moved:
            self._push_undo()
            self._rebuild_collision()
            self._notify_layout_change()
            self._notify_selection()

        self._drag_start_canvas = None
        self._drag_start_grids = {}
        self._drag_extra_ids = set()
        self._is_dragging = False
        self._drag_moved = False

        if self._box_sel_start is not None:
            self._box_sel_start = None
            self._box_sel_cur = None
            if self._box_sel_rect:
                self.canvas.delete(self._box_sel_rect)
                self._box_sel_rect = None
            self._redraw()

    def _on_right_click(self, event):
        if self.build_mode_guid is not None or self._paste_active:
            if self._line_start is not None:
                self._line_start = None
                self._redraw()
            else:
                self.cancel_build_mode()
        else:
            self.selected_ids = set()
            self._notify_selection()
            self._redraw()

    def _on_double_click(self, event):
        if self.build_mode_guid is not None:
            return
        hit = self._find_building_at(event.x, event.y)
        if hit is not None:
            pb = next((p for p in self.placed_buildings if p.instance_id == hit), None)
            if pb and not self._is_module_building(pb.guid):
                # Enter ghost/build mode for the same building type and rotation,
                # carrying the source building's effects to the first placement.
                self.build_rotation = pb.rotation
                self.set_build_mode(pb.guid)
                self._pending_paste_effects = {
                    'tech':   set(self._active_tech_effects.get(pb.instance_id, set())),
                    'items':  set(self._active_item_effects.get(pb.instance_id, set())),
                    'boosts': set(self._active_item_boosts.get(pb.instance_id, set())),
                }

    def _on_mouse_move(self, event):
        if self._paste_active:
            self._update_paste_ghost(event.x, event.y)
        elif self.build_mode_guid is not None:
            self._update_ghost(event.x, event.y)
        else:
            self._update_dbg_hover(event.x, event.y)

    def _update_dbg_hover(self, cx: float, cy: float):
        c = self.canvas
        for item in self._dbg_hover_items:
            c.delete(item)
        self._dbg_hover_items.clear()

        iid = self._find_building_at(cx, cy)
        if iid is None:
            return
        pb = next((p for p in self.placed_buildings if p.instance_id == iid), None)
        if pb is None:
            return
        bd = self.dm.get_building(pb.guid)
        name = bd.get_name('english') if bd else f'guid {pb.guid}'
        txt = (f"{name}  (guid {pb.guid})\n"
               f"col={pb.grid_x:.4g}  row={pb.grid_y:.4g}  rot={pb.rotation}°")

        tx, ty = cx + 14, cy - 44
        ti = c.create_text(tx, ty, anchor='nw', text=txt, fill='white',
                           font=('Segoe UI', 8), tags='dbg_hover')
        bb = c.bbox(ti)
        if bb:
            ri = c.create_rectangle(bb[0]-3, bb[1]-2, bb[2]+3, bb[3]+2,
                                     fill='#1a1a1a', outline='#888888',
                                     tags='dbg_hover')
            c.lift(ti)
            self._dbg_hover_items = [ri, ti]
        else:
            self._dbg_hover_items = [ti]

    def _on_mouse_leave(self, event):
        for item in self._dbg_hover_items:
            self.canvas.delete(item)
        self._dbg_hover_items.clear()
        changed = False
        if self._ghost_items or self._ghost_grid_pos is not None:
            self._ghost_grid_pos = None
            changed = True
        if self._paste_ghost_pos is not None:
            self._paste_ghost_pos = None
            changed = True
        if changed:
            self._redraw()

    # ------------------------------------------------------------------ #
    #  Ghost / placement
    # ------------------------------------------------------------------ #
    def _update_paste_ghost(self, cx: float, cy: float):
        """Track the cursor with a whole-tile anchor for the multi-paste ghost.

        A whole-tile offset preserves each building's original sub-tile
        alignment (matters for 45°-rotated items whose anchors can be
        half-integer), so no per-building snapping logic is needed here.
        """
        gx_raw, gy_raw = self.canvas_to_grid(cx, cy)
        self._paste_ghost_pos = (float(math.floor(gx_raw)), float(math.floor(gy_raw)))
        self._redraw()

    def _update_ghost(self, cx: float, cy: float):
        gx_raw, gy_raw = self.canvas_to_grid(cx, cy)
        bd = self.dm.get_building(self.build_mode_guid)
        if not bd:
            return
        rot = self.build_rotation % 360
        if rot in (0, 90, 180, 270):
            w = bd.width if rot in (0, 180) else bd.height
            h = bd.height if rot in (0, 180) else bd.width
            gx = float(math.floor(gx_raw - w / 2 + 0.5))
            gy = float(math.floor(gy_raw - h / 2 + 0.5))
        else:
            nw, nh = _get_45_grid_counts(bd, rot)
            bbox_half = (nw + nh) * 0.25
            # Centre the ghost on the cursor, then snap anchor to full 45°-grid tile
            gx, gy = self._snap_45_anchor(gx_raw - bbox_half, gy_raw - bbox_half,
                                          nw, nh)

        self._ghost_grid_pos = (gx, gy)

        if not self._layout_dirty:
            # Ghost-only update: skip the full delete-all + rebuild.
            # Buildings are already on the canvas from the last full redraw;
            # just replace the ghost items tagged 'ghost'.
            c = self.canvas
            c.delete('ghost')
            self._draw_ghost()
            # Ghost radius ring is also tagged 'ghost' (deleted above); redraw it.
            if (not self._house_drag_active and not self._module_rect_active
                    and not self._paste_active and self._line_start is None
                    and len(self.selected_ids) != 1):
                self._draw_radius_rings(bd, gx, gy, self.build_rotation,
                                        canvas_tag='ghost')
            return

        self._redraw()

    def _place_building(self, event):
        bd = self.dm.get_building(self.build_mode_guid)
        if not bd or self._ghost_grid_pos is None:
            return

        gx, gy = self._ghost_grid_pos
        if self._check_collision(bd, gx, gy, self.build_rotation):
            return
        if self._check_module_radius(bd, gx, gy):
            return

        self._push_undo()
        evict = self._get_roads_to_evict(bd, gx, gy, self.build_rotation)
        if evict:
            self.placed_buildings = [p for p in self.placed_buildings
                                     if p.instance_id not in evict]
        pb = PlacedBuilding(self.build_mode_guid, gx, gy, self.build_rotation,
                            parent_id=self._module_parent_id)
        self.placed_buildings.append(pb)
        if self._pending_paste_effects:
            eff = self._pending_paste_effects
            self._pending_paste_effects = None
            if eff.get('tech'):
                self._active_tech_effects[pb.instance_id] = set(eff['tech'])
            if eff.get('items'):
                self._active_item_effects[pb.instance_id] = set(eff['items'])
            if eff.get('boosts'):
                self._active_item_boosts[pb.instance_id] = set(eff['boosts'])
        # Tell _rebuild_collision what actually changed so it can skip
        # re-invalidating caches that are not affected by this placement.
        is_road = _is_road_like(bd)
        is_module = pb.parent_id is not None or pb.nibble
        self._rebuild_collision(is_road_change=False, is_module_change=is_module,
                                preserve_draw_caches=True)
        self._incremental_road_graph_update(pb if is_road else None, evict)
        # Erase canvas items for any roads that were evicted above.
        if evict:
            for eid in evict:
                self.canvas.delete(f'bld_{eid}')
        # Mark layout as modified immediately; defer the expensive stats
        # computation so the building appears on screen without any delay.
        if hasattr(self.app, 'mark_dirty'):
            self.app.mark_dirty()
        self._schedule_deferred_stats_update()
        if self._move_mode_active:
            # The move is done - clear the snapshot first so cancel_build_mode
            # below doesn't try to restore the building we just placed.
            self._move_mode_active = False
            self._move_restore_snapshot = []
            self.cancel_build_mode()
            return
        # Incremental canvas update: draw only the new building immediately so
        # the user sees instant feedback.  A deferred full rebuild follows to
        # correct Z-order, road junctions, and farm-field colours.
        self._incremental_add_building(pb, evict=evict)
        if is_road or is_module:
            # Roads need junction re-rendering; modules need colour conflict
            # re-evaluation.  Schedule a settle redraw after a short idle.
            self._schedule_deferred_redraw(200)
        # Stay in build mode for repeated placement

    def _update_house_block(self):
        """Recompute the previewed house block from anchor to current ghost position.

        The drag's dominant axis (whichever has the larger extent) becomes the
        block's length (any number of houses); the cross axis is the block's
        width, capped at 2 houses.
        """
        bd = self.dm.get_building(self.build_mode_guid)
        if not bd or self._house_drag_anchor is None or self._ghost_grid_pos is None:
            return
        ax, ay = self._house_drag_anchor
        cx, cy = self._ghost_grid_pos
        rot = self.build_rotation % 360

        positions = []
        if rot in (0, 90, 180, 270):
            w = bd.width if rot in (0, 180) else bd.height
            h = bd.height if rot in (0, 180) else bd.width
            cells_x = round((cx - ax) / w) if w else 0
            cells_y = round((cy - ay) / h) if h else 0
            if abs(cells_x) >= abs(cells_y):
                length_n = abs(cells_x) + 1
                length_sign = -1 if cells_x < 0 else 1
                width_n = max(1, min(2, abs(cells_y) + 1))
                width_sign = -1 if cells_y < 0 else 1
                for li in range(length_n):
                    for wi in range(width_n):
                        positions.append((ax + li * w * length_sign, ay + wi * h * width_sign))
            else:
                length_n = abs(cells_y) + 1
                length_sign = -1 if cells_y < 0 else 1
                width_n = max(1, min(2, abs(cells_x) + 1))
                width_sign = -1 if cells_x < 0 else 1
                for li in range(length_n):
                    for wi in range(width_n):
                        positions.append((ax + wi * w * width_sign, ay + li * h * length_sign))
        else:
            # 45°-family rotation: a building's footprint is a diamond spanning
            # nw units along u=(x+y) and nh units along v=(x-y); stepping the
            # diamond's centre by nw/nh along u/v tiles adjacent houses edge-to-edge
            # with no gap or overlap (unlike stepping the anchor in plain x/y).
            nw, nh = _get_45_grid_counts(bd, rot)
            a_cx, a_cy = self._building_center(bd, ax, ay, rot)
            c_cx, c_cy = self._building_center(bd, cx, cy, rot)
            au, av = a_cx + a_cy, a_cx - a_cy
            cu, cv = c_cx + c_cy, c_cx - c_cy
            cells_u = round((cu - au) / nw) if nw else 0
            cells_v = round((cv - av) / nh) if nh else 0
            if abs(cells_u) >= abs(cells_v):
                length_n = abs(cells_u) + 1
                length_sign = -1 if cells_u < 0 else 1
                width_n = max(1, min(2, abs(cells_v) + 1))
                width_sign = -1 if cells_v < 0 else 1
                for li in range(length_n):
                    for wi in range(width_n):
                        nu = au + li * nw * length_sign
                        nv = av + wi * nh * width_sign
                        ncx, ncy = (nu + nv) / 2, (nu - nv) / 2
                        positions.append(self._snap_anchor_from_center(bd, ncx, ncy, rot))
            else:
                length_n = abs(cells_v) + 1
                length_sign = -1 if cells_v < 0 else 1
                width_n = max(1, min(2, abs(cells_u) + 1))
                width_sign = -1 if cells_u < 0 else 1
                for li in range(length_n):
                    for wi in range(width_n):
                        nu = au + wi * nw * width_sign
                        nv = av + li * nh * length_sign
                        ncx, ncy = (nu + nv) / 2, (nu - nv) / 2
                        positions.append(self._snap_anchor_from_center(bd, ncx, ncy, rot))

        self._house_drag_positions = positions
        self._redraw()

    def _commit_house_block(self):
        bd = self.dm.get_building(self.build_mode_guid)
        if not bd or not self._house_drag_positions:
            return
        self._push_undo()
        placed_any = False
        for gx, gy in self._house_drag_positions:
            if self._check_collision(bd, gx, gy, self.build_rotation):
                continue
            evict = self._get_roads_to_evict(bd, gx, gy, self.build_rotation)
            if evict:
                self.placed_buildings = [p for p in self.placed_buildings
                                         if p.instance_id not in evict]
            pb = PlacedBuilding(self.build_mode_guid, gx, gy, self.build_rotation)
            self.placed_buildings.append(pb)
            self._rebuild_collision()
            placed_any = True
        if placed_any:
            self._notify_layout_change()
        else:
            self._undo_stack.pop()

    def _update_module_rect(self):
        """Recompute the previewed fill-rectangle of 1x1 modules from anchor to cursor."""
        bd = self.dm.get_building(self.build_mode_guid)
        if not bd or self._module_rect_anchor is None or self._ghost_grid_pos is None:
            return
        ax, ay = self._module_rect_anchor
        cx, cy = self._ghost_grid_pos
        rot = self.build_rotation % 360

        positions = []
        if rot in (0, 90, 180, 270):
            x0, x1 = sorted((int(ax), int(cx)))
            y0, y1 = sorted((int(ay), int(cy)))
            positions = [
                (float(gx), float(gy))
                for gy in range(y0, y1 + 1)
                for gx in range(x0, x1 + 1)
            ]
        else:
            # 45°-family rotation: fill the rectangle in diagonal (u,v) space
            # (same approach as the house block) so adjacent diamonds tile
            # edge-to-edge instead of overlapping/gapping.
            nw, nh = _get_45_grid_counts(bd, rot)
            a_cx, a_cy = self._building_center(bd, ax, ay, rot)
            c_cx, c_cy = self._building_center(bd, cx, cy, rot)
            au, av = a_cx + a_cy, a_cx - a_cy
            cu, cv = c_cx + c_cy, c_cx - c_cy
            cells_u = round((cu - au) / nw) if nw else 0
            cells_v = round((cv - av) / nh) if nh else 0
            u0, u1 = sorted((0, cells_u))
            v0, v1 = sorted((0, cells_v))
            for ui in range(u0, u1 + 1):
                for vi in range(v0, v1 + 1):
                    nu = au + ui * nw
                    nv = av + vi * nh
                    ncx, ncy = (nu + nv) / 2, (nu - nv) / 2
                    positions.append(self._snap_anchor_from_center(bd, ncx, ncy, rot))

        self._module_rect_positions = positions
        self._redraw()

    def _commit_module_rect(self):
        bd = self.dm.get_building(self.build_mode_guid)
        if not bd or not self._module_rect_positions:
            return
        self._push_undo()
        placed_any = False
        for gx, gy in self._module_rect_positions:
            if self._check_collision(bd, gx, gy, self.build_rotation):
                continue
            if self._check_module_radius(bd, gx, gy):
                continue
            evict = self._get_roads_to_evict(bd, gx, gy, self.build_rotation)
            if evict:
                self.placed_buildings = [p for p in self.placed_buildings
                                         if p.instance_id not in evict]
            pb = PlacedBuilding(self.build_mode_guid, gx, gy, self.build_rotation,
                                parent_id=self._module_parent_id)
            self.placed_buildings.append(pb)
            self._rebuild_collision()
            placed_any = True
        if placed_any:
            self._notify_layout_change()
        else:
            self._undo_stack.pop()

    def _compute_line_positions(self, bd: BuildingData, start: tuple, end: tuple,
                                 rotation: int) -> list:
        """Return anchor positions for a straight run of tiles from start to end,
        snapped to the dominant axis (horizontal/vertical, or the 45° diagonal)."""
        sx, sy = start
        ex, ey = end
        rot = rotation % 360
        positions = []
        if rot in (0, 90, 180, 270):
            w = bd.width if rot in (0, 180) else bd.height
            h = bd.height if rot in (0, 180) else bd.width
            dx_cells = round((ex - sx) / w) if w else 0
            dy_cells = round((ey - sy) / h) if h else 0
            if abs(dx_cells) >= abs(dy_cells):
                n = abs(dx_cells) + 1
                sign = -1 if dx_cells < 0 else 1
                for i in range(n):
                    positions.append((sx + i * w * sign, sy))
            else:
                n = abs(dy_cells) + 1
                sign = -1 if dy_cells < 0 else 1
                for i in range(n):
                    positions.append((sx, sy + i * h * sign))
        else:
            # 45°-family: step along whichever diagonal axis (u=x+y or v=x-y)
            # the two points differ on more.
            nw, nh = _get_45_grid_counts(bd, rot)
            s_cx, s_cy = self._building_center(bd, sx, sy, rot)
            e_cx, e_cy = self._building_center(bd, ex, ey, rot)
            su, sv = s_cx + s_cy, s_cx - s_cy
            eu, ev = e_cx + e_cy, e_cx - e_cy
            cells_u = round((eu - su) / nw) if nw else 0
            cells_v = round((ev - sv) / nh) if nh else 0
            if abs(cells_u) >= abs(cells_v):
                n = abs(cells_u) + 1
                sign = -1 if cells_u < 0 else 1
                for i in range(n):
                    nu = su + i * nw * sign
                    ncx, ncy = (nu + sv) / 2, (nu - sv) / 2
                    positions.append(self._snap_anchor_from_center(bd, ncx, ncy, rot))
            else:
                n = abs(cells_v) + 1
                sign = -1 if cells_v < 0 else 1
                for i in range(n):
                    nv = sv + i * nh * sign
                    ncx, ncy = (su + nv) / 2, (su - nv) / 2
                    positions.append(self._snap_anchor_from_center(bd, ncx, ncy, rot))
        return positions

    def _commit_line(self, bd: BuildingData, positions: list):
        if not positions:
            return
        self._push_undo()
        placed_any = False
        for gx, gy in positions:
            if self._check_collision(bd, gx, gy, self.build_rotation):
                continue
            evict = self._get_roads_to_evict(bd, gx, gy, self.build_rotation)
            if evict:
                self.placed_buildings = [p for p in self.placed_buildings
                                         if p.instance_id not in evict]
            pb = PlacedBuilding(self.build_mode_guid, gx, gy, self.build_rotation)
            self.placed_buildings.append(pb)
            self._rebuild_collision()
            placed_any = True
        if placed_any:
            self._notify_layout_change()
        else:
            self._undo_stack.pop()

    def _commit_paste(self):
        """Stamp the multi-building paste group at the current ghost offset.
        Stays in paste mode afterward so the group can be stamped again
        elsewhere, mirroring the single-building paste/build-mode behaviour
        - except in move mode (see start_move_mode), where anything that
        doesn't fit here is kept in the clipboard for another attempt
        (re-anchored to this position) instead of being lost, and the mode
        exits automatically once everything has been placed."""
        if not self._paste_clipboard or self._paste_ghost_pos is None:
            return
        ox = self._paste_ghost_pos[0] - self._paste_anchor_orig[0]
        oy = self._paste_ghost_pos[1] - self._paste_anchor_orig[1]
        self._push_undo()
        placed_any = False
        remaining = []
        newly_placed: list = []  # PlacedBuilding objects placed in this pass
        for pb in self._paste_clipboard:
            bd = self.dm.get_building(pb.guid)
            if not bd:
                continue
            gx, gy = pb.grid_x + ox, pb.grid_y + oy
            if self._check_collision(bd, gx, gy, pb.rotation):
                remaining.append(pb)
                continue
            evict = self._get_roads_to_evict(bd, gx, gy, pb.rotation)
            if evict:
                self.placed_buildings = [p for p in self.placed_buildings
                                         if p.instance_id not in evict]
            # A module's clipboard-time parent_id points at the parent's
            # clipboard instance_id, not the fresh id it gets placed with.
            # _move_id_remap accumulates clipboard-id -> real-id across
            # every commit attempt in this move session (a multi-building
            # move can partially fail and retry - see remaining/_commit_paste
            # callers), so a module placed after its parent in an *earlier*
            # pass still resolves to the parent's real, already-placed id.
            resolved_parent_id = self._move_id_remap.get(pb.parent_id, pb.parent_id)
            new_pb = PlacedBuilding(pb.guid, gx, gy, pb.rotation, parent_id=resolved_parent_id)
            self.placed_buildings.append(new_pb)
            self._move_id_remap[pb.instance_id] = new_pb.instance_id
            eff = self._clipboard_effects.get(pb.instance_id)
            if eff:
                if eff.get('tech'):
                    self._active_tech_effects[new_pb.instance_id] = set(eff['tech'])
                if eff.get('items'):
                    self._active_item_effects[new_pb.instance_id] = set(eff['items'])
                if eff.get('boosts'):
                    self._active_item_boosts[new_pb.instance_id] = set(eff['boosts'])
            newly_placed.append(new_pb)
            self._rebuild_collision()
            placed_any = True
        # Fix up parent_id for siblings placed in this same pass (a parent
        # processed after its module within this one call, say).
        for new_pb in newly_placed:
            if new_pb.parent_id in self._move_id_remap:
                new_pb.parent_id = self._move_id_remap[new_pb.parent_id]
        if placed_any:
            self._notify_layout_change()
        else:
            self._undo_stack.pop()

        if self._move_mode_active:
            if placed_any and self._move_restore_snapshot:
                # Once any item has actually been committed as a new, real
                # building, the original snapshot is stale - restoring it on
                # a later cancel would duplicate whatever's already placed,
                # not just bring back what's still pending. Anything still
                # in remaining at that point is simply abandoned by cancel,
                # which beats silently duplicating buildings.
                self._move_restore_snapshot = []
            if remaining:
                # Re-anchoring to the just-used ghost position changes the
                # reference frame for "offset from anchor" - translate the
                # remaining items' stored coordinates by the same (ox, oy)
                # everyone else just moved by, so a retry at this same spot
                # (offset 0 from the new anchor) places them near their
                # already-placed siblings instead of back at the original,
                # pre-move location.
                for pb in remaining:
                    pb.grid_x += ox
                    pb.grid_y += oy
                self._paste_clipboard = remaining
                self._paste_anchor_orig = self._paste_ghost_pos
            else:
                self._move_mode_active = False
                self._move_restore_snapshot = []
                self.cancel_build_mode()

    def _reset_placement_modes(self):
        """Clear all transient placement-mode state (house block, module
        rect-fill, line tool, multi-building paste ghost, move mode)."""
        self._house_drag_active = False
        self._house_drag_anchor = None
        self._house_drag_positions = []
        self._module_rect_active = False
        self._module_rect_anchor = None
        self._module_rect_positions = []
        self._line_start = None
        self._paste_active = False
        self._paste_clipboard = []
        self._paste_anchor_orig = None
        self._pending_paste_effects = None
        self._paste_ghost_pos = None
        if self._move_mode_active:
            # Abandoning an in-progress move (explicit cancel, or switching
            # to a different build action) restores the original building(s)
            # untouched, rather than leaving them deleted.
            for d in self._move_restore_snapshot:
                self.placed_buildings.append(PlacedBuilding.from_dict(d))
            self._rebuild_collision()
            self._notify_layout_change()
        self._move_mode_active = False
        self._move_restore_snapshot = []
        self._move_id_remap = {}

    def set_build_mode(self, guid: int, module_parent_id: int = None):
        bd = self.dm.get_building(guid)
        if not bd or not bd.is_placeable():
            return
        self.build_mode_guid = guid
        self._module_parent_id = module_parent_id
        self._ghost_grid_pos = None
        self._reset_placement_modes()
        self.selected_ids = set()
        self.canvas.config(cursor='crosshair')
        self._notify_selection()
        self._redraw()

    def cancel_build_mode(self):
        self.build_mode_guid = None
        self._module_parent_id = None
        self._ghost_grid_pos = None
        self._reset_placement_modes()
        self.canvas.config(cursor='arrow')
        self._redraw()

    # ------------------------------------------------------------------ #
    #  Collision
    # ------------------------------------------------------------------ #
    def _rebuild_collision(self, *, is_road_change: bool = True,
                           is_module_change: bool = True,
                           preserve_draw_caches: bool = False):
        """Rebuild the collision tile map.

        is_road_change  – set False when no road-like building was added/removed;
                          preserves the cached road graph.
        is_module_change – set False when no farm-module / nibble tile changed;
                           preserves the module-touch-pairs and colour-rank caches.
        preserve_draw_caches – set True for incremental single-building placements
                               so _draw_order_cache and _road_pos_cache survive;
                               the caller is responsible for updating them.
        All flags default to True / False (conservative / safe for unknown callers).
        """
        # (col, row) -> list of instance_ids occupying that tile, in placement
        # order. Usually a single occupant, but a road may share a tile with
        # an aqueduct arch or canal it crosses (see _can_coexist).
        # Nibble tiles are cosmetic sub-tile polygons and never block placement.
        self._collision_map = {}
        for pb in self.placed_buildings:
            if pb.nibble:
                continue
            bd = self.dm.get_building(pb.guid)
            if not bd:
                continue
            tiles = _get_occupied_tiles(bd, pb.grid_x, pb.grid_y, pb.rotation)
            for t in tiles:
                self._collision_map.setdefault(t, []).append(pb.instance_id)
        if is_road_change:
            self._road_graph_dirty = True
        if is_module_change:
            self._module_touch_pairs_cache = None
            self._parent_color_ranks_cache = None
        if not preserve_draw_caches:
            self._draw_order_cache = None
            self._road_pos_cache = None
        self._layout_dirty = True   # force a full rebuild before any fast pan

    def _get_module_touch_pairs(self) -> list:
        """Pairs of (module_a, module_b) PlacedBuildings, each belonging to
        a different parent, whose footprints touch. Layout-dependent only
        (no colour data), so it's safe to cache across redraws and only
        rebuild in _rebuild_collision."""
        if self._module_touch_pairs_cache is not None:
            return self._module_touch_pairs_cache

        modules = [pb for pb in self.placed_buildings if pb.parent_id is not None]
        tile_to_modules: dict = {}   # (col, row) -> [instance_ids]
        module_to_tiles: dict = {}   # instance_id -> set of (col, row)  — reverse lookup
        polys: dict = {}
        for pb in modules:
            if pb.nibble:
                # Nibble tile: always use 1×1 square polygon at its grid cell.
                gx, gy = pb.grid_x, pb.grid_y
                polys[pb.instance_id] = ([gx, gy, gx+1, gy, gx+1, gy+1, gx, gy+1], pb)
                tile_key = (round(gx), round(gy))
                tile_to_modules.setdefault(tile_key, []).append(pb.instance_id)
                module_to_tiles[pb.instance_id] = {tile_key}
                continue
            bd = self.dm.get_building(pb.guid)
            if not bd:
                continue
            polys[pb.instance_id] = (self._get_poly_pts(bd, pb.grid_x, pb.grid_y, pb.rotation), pb)
            tiles = _get_occupied_tiles(bd, pb.grid_x, pb.grid_y, pb.rotation)
            module_to_tiles[pb.instance_id] = set(tiles)
            for t in tiles:
                tile_to_modules.setdefault(t, []).append(pb.instance_id)

        pairs = []
        seen = set()
        for pb in modules:
            entry = polys.get(pb.instance_id)
            if entry is None:
                continue
            poly_a, _ = entry
            candidates = set()
            occupied = module_to_tiles.get(pb.instance_id, set())
            for (col, row) in occupied:
                for dc in (-1, 0, 1):
                    for dr in (-1, 0, 1):
                        candidates.update(tile_to_modules.get((col + dc, row + dr), ()))
            for other_id in candidates:
                if other_id == pb.instance_id:
                    continue
                key = tuple(sorted((pb.instance_id, other_id)))
                if key in seen:
                    continue
                other_entry = polys.get(other_id)
                if other_entry is None:
                    continue
                poly_b, other_pb = other_entry
                if other_pb.parent_id == pb.parent_id:
                    continue
                seen.add(key)
                if pb.nibble and other_pb.nibble:
                    # Both are 1×1 axis-aligned unit squares: skip the 48µs SAT
                    # call and do a cheap integer edge-adjacency check instead.
                    (gx1, gy1) = next(iter(occupied))
                    occ_b = module_to_tiles.get(other_pb.instance_id, set())
                    if occ_b:
                        gx2, gy2 = next(iter(occ_b))
                        adx, ady = abs(gx1 - gx2), abs(gy1 - gy2)
                        if (adx == 1 and ady == 0) or (adx == 0 and ady == 1):
                            pairs.append((pb, other_pb))
                elif self._polys_touch(poly_a, poly_b):
                    pairs.append((pb, other_pb))

        self._module_touch_pairs_cache = pairs
        return pairs

    def _get_parent_color_ranks(self) -> dict:
        """Rank (0, 1, 2, ...) of each parent within its colour-conflict
        cluster: the connected group of parents that share their base
        colour AND are linked, directly or transitively, by at least one
        pair of touching modules. Rank 0 - the earliest-placed (lowest
        instance_id) parent in the cluster - keeps the original colour
        unchanged; rank k>0 is lightened by k steps, so three or more
        mutually-touching same-coloured areas each end up a visually
        distinct shade rather than only ever telling two apart."""
        if self._parent_color_ranks_cache is not None:
            return self._parent_color_ranks_cache
        parent_of: dict = {}

        def find(x):
            parent_of.setdefault(x, x)
            root = x
            while parent_of[root] != root:
                root = parent_of[root]
            while parent_of[x] != root:
                parent_of[x], x = root, parent_of[x]
            return root

        def union(a, b):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent_of[ra] = rb

        color_cache: dict = {}
        def parent_color(parent_pb):
            if parent_pb.instance_id not in color_cache:
                bd = self.dm.get_building(parent_pb.guid)
                color_cache[parent_pb.instance_id] = self.dm.get_building_color(bd) if bd else None
            return color_cache[parent_pb.instance_id]

        for mod_a, mod_b in self._get_module_touch_pairs():
            parent_a = self._get_placed_by_id(mod_a.parent_id)
            parent_b = self._get_placed_by_id(mod_b.parent_id)
            if not parent_a or not parent_b:
                continue
            ca, cb = parent_color(parent_a), parent_color(parent_b)
            if ca is None or ca != cb:
                continue
            union(parent_a.instance_id, parent_b.instance_id)

        clusters: dict = {}
        for pid in parent_of:
            clusters.setdefault(find(pid), []).append(pid)

        ranks: dict = {}
        for members in clusters.values():
            if len(members) < 2:
                continue
            for rank, pid in enumerate(sorted(members)):
                if rank > 0:
                    ranks[pid] = rank
        self._parent_color_ranks_cache = ranks
        return ranks

    def _get_placed_by_id(self, instance_id) -> Optional[PlacedBuilding]:
        if instance_id is None:
            return None
        for pb in self.placed_buildings:
            if pb.instance_id == instance_id:
                return pb
        return None

    def _with_carried_modules(self, ids: set) -> set:
        """Expand a set of instance_ids to also include every module whose
        parent is in that set, so an action on a parent (move, delete, ...)
        always carries its modules along too, even if they weren't
        themselves explicitly selected."""
        carried = {pm.instance_id for pm in self.placed_buildings
                   if pm.parent_id in ids}
        return ids | carried

    def _resolve_render_color(self, pb: PlacedBuilding, bd: BuildingData,
                               parent_color_ranks: Optional[dict] = None) -> str:
        """A module always renders in its parent's colour rather than its
        own category colour. If that parent's colour would otherwise be
        indistinguishable from other, same-coloured parents whose modules
        it's touching (directly or transitively), every parent but the
        earliest-placed one in that group renders a progressively lighter
        variant instead, so each stays visually distinct (see
        _get_parent_color_ranks)."""
        if parent_color_ranks is None:
            parent_color_ranks = self._get_parent_color_ranks()

        owner_id = pb.instance_id
        color_bd = bd
        if pb.parent_id is not None:
            parent_pb = self._get_placed_by_id(pb.parent_id)
            if parent_pb is not None:
                parent_bd = self.dm.get_building(parent_pb.guid)
                if parent_bd is not None:
                    owner_id = parent_pb.instance_id
                    color_bd = parent_bd

        base = self.dm.get_building_color(color_bd)
        rank = parent_color_ranks.get(owner_id, 0)
        if rank > 0:
            amount = min(rank * MODULE_CONFLICT_LIGHTEN_STEP, MODULE_CONFLICT_LIGHTEN_MAX)
            return _lighten_color(base, amount)
        return base

    @staticmethod
    def _get_poly_pts(bd: BuildingData, gx: float, gy: float,
                      rotation: int) -> list:
        """Building polygon corners in grid coordinates (flat x0,y0,x1,y1,...)."""
        rot = rotation % 360
        if rot in (0, 90, 180, 270):
            w = bd.width  if rot in (0, 180) else bd.height
            h = bd.height if rot in (0, 180) else bd.width
            return [gx, gy,  gx + w, gy,  gx + w, gy + h,  gx, gy + h]
        else:
            nw, nh = _get_45_grid_counts(bd, rot)
            q = 0.25
            bcx = gx + (nw + nh) * q
            bcy = gy + (nw + nh) * q
            return [
                bcx + (nh - nw) * q, bcy - (nw + nh) * q,
                bcx + (nw + nh) * q, bcy + (nw - nh) * q,
                bcx + (nw - nh) * q, bcy + (nw + nh) * q,
                bcx - (nw + nh) * q, bcy + (nh - nw) * q,
            ]

    @staticmethod
    def _polys_adjacent(pts_a: list, pts_b: list) -> bool:
        """SAT test: True if polygons overlap OR touch at any point (edge or single corner).
        Uses strict < for separation, so exact touching returns True.
        Used for perimeter detection where corner-point contact must be included."""
        n_a = len(pts_a) // 2
        n_b = len(pts_b) // 2
        for pts, n in ((pts_a, n_a), (pts_b, n_b)):
            for i in range(n):
                x1, y1 = pts[2 * i],           pts[2 * i + 1]
                x2, y2 = pts[2 * ((i+1) % n)], pts[2 * ((i+1) % n) + 1]
                nx, ny = -(y2 - y1), (x2 - x1)
                if nx == 0 and ny == 0:
                    continue
                min_a = min(pts_a[j]*nx + pts_a[j+1]*ny for j in range(0, len(pts_a), 2))
                max_a = max(pts_a[j]*nx + pts_a[j+1]*ny for j in range(0, len(pts_a), 2))
                min_b = min(pts_b[j]*nx + pts_b[j+1]*ny for j in range(0, len(pts_b), 2))
                max_b = max(pts_b[j]*nx + pts_b[j+1]*ny for j in range(0, len(pts_b), 2))
                if max_a < min_b or max_b < min_a:   # strictly separated
                    return False
        return True

    @staticmethod
    def _polys_overlap(pts_a: list, pts_b: list) -> bool:
        """SAT convex-polygon overlap. Exact touching (shared edge/vertex) is NOT overlap."""
        n_a = len(pts_a) // 2
        n_b = len(pts_b) // 2
        for pts, n in ((pts_a, n_a), (pts_b, n_b)):
            for i in range(n):
                x1, y1 = pts[2 * i],           pts[2 * i + 1]
                x2, y2 = pts[2 * ((i+1) % n)], pts[2 * ((i+1) % n) + 1]
                nx, ny = -(y2 - y1), (x2 - x1)   # edge normal
                if nx == 0 and ny == 0:
                    continue
                min_a = min(pts_a[j]*nx + pts_a[j+1]*ny for j in range(0, len(pts_a), 2))
                max_a = max(pts_a[j]*nx + pts_a[j+1]*ny for j in range(0, len(pts_a), 2))
                min_b = min(pts_b[j]*nx + pts_b[j+1]*ny for j in range(0, len(pts_b), 2))
                max_b = max(pts_b[j]*nx + pts_b[j+1]*ny for j in range(0, len(pts_b), 2))
                if max_a <= min_b or max_b <= min_a:
                    return False
        return True

    @staticmethod
    def _polys_touch(pts_a: list, pts_b: list, eps: float = 0.01) -> bool:
        """SAT test for a *real* road connection: True if the two convex
        polygons overlap or share a genuine edge-length boundary, False if
        they're separated OR only meet at a single corner point.

        Corner-only contact matters because two 90°-aligned road tiles
        placed diagonally adjacent (e.g. a staircase pattern) touch at
        exactly one point with no pavement actually bridging the gap - the
        other two corners of that intersection are empty. Counting that as
        a connection created false shortcuts in the BFS, letting it skip
        real hops on bent/staircase roads (reported as over-reaching the
        StreetDistance budget). A genuine connection needs one axis where
        the shapes touch (the direction they're adjacent along) AND another
        axis with a clearly positive overlap (an actual shared edge span).
        """
        n_a = len(pts_a) // 2
        n_b = len(pts_b) // 2
        max_overlap = -math.inf
        for pts, n in ((pts_a, n_a), (pts_b, n_b)):
            for i in range(n):
                x1, y1 = pts[2 * i],           pts[2 * i + 1]
                x2, y2 = pts[2 * ((i+1) % n)], pts[2 * ((i+1) % n) + 1]
                nx, ny = -(y2 - y1), (x2 - x1)   # edge normal
                norm = math.hypot(nx, ny)
                if norm == 0:
                    continue
                nx, ny = nx / norm, ny / norm     # normalize so eps is in real grid units
                min_a = min(pts_a[j]*nx + pts_a[j+1]*ny for j in range(0, len(pts_a), 2))
                max_a = max(pts_a[j]*nx + pts_a[j+1]*ny for j in range(0, len(pts_a), 2))
                min_b = min(pts_b[j]*nx + pts_b[j+1]*ny for j in range(0, len(pts_b), 2))
                max_b = max(pts_b[j]*nx + pts_b[j+1]*ny for j in range(0, len(pts_b), 2))
                overlap = min(max_a, max_b) - max(min_a, min_b)
                if overlap < -eps:
                    return False          # real gap on this axis: not touching at all
                max_overlap = max(max_overlap, overlap)
        return max_overlap > eps          # need at least one axis with a real shared span

    def _check_module_radius(self, bd: BuildingData, gx: float, gy: float) -> bool:
        """Return True if placement must be blocked: the building is a module and
        at least one parent exists, but none are within their module_build_radius."""
        guid = bd.guid
        parents = [
            (pb, pbd)
            for pb in self.placed_buildings
            if (pbd := self.dm.get_building(pb.guid)) is not None
            and pbd.module_guid == guid
            and pbd.module_build_radius
        ]
        if not parents:
            return False
        mcx, mcy = gx + 0.5, gy + 0.5
        for pb, pbd in parents:
            pcx, pcy = self._building_center(pbd, pb.grid_x, pb.grid_y, pb.rotation)
            r = pbd.module_build_radius + min(pbd.width, pbd.height) / 2
            if (mcx - pcx) ** 2 + (mcy - pcy) ** 2 <= r * r:
                return False
        return True

    def _check_collision(self, bd: BuildingData, gx: float, gy: float,
                          rotation: int, exclude_ids: set = None) -> bool:
        occupied = _get_occupied_tiles(bd, gx, gy, rotation)
        rot = rotation % 360

        # Island blocking: any tile outside bounds or not in the buildable set blocks placement.
        if self._island_tiles is not None:
            iw, ih = self._island_w, self._island_h
            # River-crossable buildings (river buildings, roads, aqueducts, walls)
            # may be placed on any in-bounds tile, including river/unbuildable land.
            _river_crossable = (
                getattr(bd, 'river_building', None)
                or _is_road_like(bd)
                or bd.get_category_english() == 'Defensive Building'
            )
            # Water buildings (waterBuilding: true or "optional") may also occupy harbour tiles.
            if _river_crossable:
                valid_tiles = {_ISLE_LAND, _ISLE_BUILDABLE, _ISLE_MARSH, _ISLE_HARBOUR}
            elif getattr(bd, 'water_building', None):
                valid_tiles = _ISLE_BUILDABLE_TILES | {_ISLE_HARBOUR}
            else:
                valid_tiles = _ISLE_BUILDABLE_TILES
            # For diagonal buildings pre-compute the polygon once for half-tile precision.
            bld_poly = (self._get_poly_pts(bd, gx, gy, rotation)
                        if rot not in (0, 90, 180, 270) else None)
            for (tx, ty) in occupied:
                if tx < 0 or ty < 0 or tx >= iw or ty >= ih:
                    return True
                idx = ty * iw + tx
                tile_type = self._island_tiles[idx]
                if tile_type not in valid_tiles:
                    # Diagonal buildings: allow a cut LAND tile only if the building
                    # does not actually touch the non-buildable (land-coloured) half.
                    if (tile_type == _ISLE_LAND and bld_poly is not None
                            and self._island_quads is not None):
                        q_mask = self._island_quads[idx]
                        if q_mask and not _overlaps_nonbuildable_half(bld_poly, tx, ty, q_mask):
                            continue
                    return True

        if rot in (0, 90, 180, 270):
            for t in occupied:
                occ_ids = self._collision_map.get(t)
                if not occ_ids:
                    continue
                for occ_id in occ_ids:
                    if exclude_ids and occ_id in exclude_ids:
                        continue
                    occ_pb = next((p for p in self.placed_buildings
                                   if p.instance_id == occ_id), None)
                    if occ_pb:
                        occ_bd = self.dm.get_building(occ_pb.guid)
                        if occ_bd and _can_coexist(bd, occ_bd):
                            continue
                    return True
            return False
        else:
            poly_new = self._get_poly_pts(bd, gx, gy, rotation)
            for pb in self.placed_buildings:
                if exclude_ids and pb.instance_id in exclude_ids:
                    continue
                bd_other = self.dm.get_building(pb.guid)
                if not bd_other:
                    continue
                if _can_coexist(bd, bd_other):
                    continue
                poly_other = self._get_poly_pts(bd_other, pb.grid_x, pb.grid_y,
                                                 pb.rotation)
                if self._polys_overlap(poly_new, poly_other):
                    return True
            return False

    def _get_roads_to_evict(self, bd: BuildingData, gx: float, gy: float,
                             rotation: int) -> set:
        """Return instance_ids of lower-priority roads displaced by placing bd here."""
        new_pri = _road_priority(bd)
        if new_pri == 0:
            return set()
        to_evict: set = set()
        rot = rotation % 360
        if rot in (0, 90, 180, 270):
            tiles = _get_occupied_tiles(bd, gx, gy, rotation)
            for t in tiles:
                occ_ids = self._collision_map.get(t)
                if not occ_ids:
                    continue
                for occ_id in occ_ids:
                    occ_pb = next((p for p in self.placed_buildings
                                   if p.instance_id == occ_id), None)
                    if occ_pb:
                        occ_bd = self.dm.get_building(occ_pb.guid)
                        if occ_bd and 0 < _road_priority(occ_bd) < new_pri:
                            to_evict.add(occ_id)
        else:
            poly_new = self._get_poly_pts(bd, gx, gy, rotation)
            for pb in self.placed_buildings:
                occ_bd = self.dm.get_building(pb.guid)
                if not occ_bd:
                    continue
                occ_pri = _road_priority(occ_bd)
                if not (0 < occ_pri < new_pri):
                    continue
                poly_other = self._get_poly_pts(occ_bd, pb.grid_x, pb.grid_y,
                                                 pb.rotation)
                if self._polys_overlap(poly_new, poly_other):
                    to_evict.add(pb.instance_id)
        return to_evict

    # ------------------------------------------------------------------ #
    #  Hit testing
    # ------------------------------------------------------------------ #
    def _point_in_45_building(self, gx: float, gy: float,
                               pb: 'PlacedBuilding', bd: BuildingData) -> bool:
        """Precise point-in-rotated-rectangle test for a 45°-family building."""
        rot = pb.rotation % 360
        nw, nh = _get_45_grid_counts(bd, rot)
        bbox_half = (nw + nh) * 0.25          # grid units
        bcx = pb.grid_x + bbox_half
        bcy = pb.grid_y + bbox_half
        dx = gx - bcx
        dy = gy - bcy
        # Rotate the click point by -45° CW (undo the building rotation)
        # so we can test against an axis-aligned rectangle.
        local_x = (dx - dy) / SQRT2
        local_y = (dx + dy) / SQRT2
        half_lw = nw * 0.25 * SQRT2           # half side-length along local X
        half_lh = nh * 0.25 * SQRT2           # half side-length along local Y
        return abs(local_x) <= half_lw and abs(local_y) <= half_lh

    def _find_building_at(self, cx: float, cy: float) -> Optional[int]:
        """Return instance_id of topmost building at canvas coords."""
        gx, gy = self.canvas_to_grid(cx, cy)
        col = math.floor(gx)
        row = math.floor(gy)

        # Exact tile lookup handles 90° buildings and the centre tiles of 45° ones.
        # When a tile has multiple occupants (e.g. a road crossing an aqueduct
        # arch), the most recently placed one is treated as topmost.
        occ_ids = self._collision_map.get((col, row))
        iid = occ_ids[-1] if occ_ids else None
        if iid is not None:
            pb = next((p for p in self.placed_buildings
                       if p.instance_id == iid), None)
            if pb and pb.rotation % 90 == 0:
                return iid
            # For a 45° hit from the collision map, verify precisely.
            if pb:
                bd = self.dm.get_building(pb.guid)
                if bd and self._point_in_45_building(gx, gy, pb, bd):
                    return iid

        # For 45° buildings the click may land on a visual edge outside the
        # collision-map tiles. Walk all diagonal buildings and test precisely.
        for pb in reversed(self.placed_buildings):
            if pb.rotation % 90 == 0:
                continue
            bd = self.dm.get_building(pb.guid)
            if bd and self._point_in_45_building(gx, gy, pb, bd):
                return pb.instance_id

        return None

    def _get_buildings_in_box(self, x0: float, y0: float,
                               x1: float, y1: float) -> set:
        """Return instance ids of buildings whose centre is inside the box."""
        gx0, gy0 = self.canvas_to_grid(min(x0, x1), min(y0, y1))
        gx1, gy1 = self.canvas_to_grid(max(x0, x1), max(y0, y1))
        result = set()
        for pb in self.placed_buildings:
            bd = self.dm.get_building(pb.guid)
            if not bd:
                continue
            rot = pb.rotation % 360
            if rot in (0, 90, 180, 270):
                w = bd.width if rot in (0, 180) else bd.height
                h = bd.height if rot in (0, 180) else bd.width
            else:
                nw, nh = _get_45_grid_counts(bd, rot)
                w = h = (nw + nh) * 0.5
            # Centre
            bcx = pb.grid_x + w / 2
            bcy = pb.grid_y + h / 2
            if gx0 <= bcx <= gx1 and gy0 <= bcy <= gy1:
                result.add(pb.instance_id)
        return result

    # ------------------------------------------------------------------ #
    #  Delete
    # ------------------------------------------------------------------ #
    def _delete_at(self, cx: float, cy: float):
        iid = self._find_building_at(cx, cy)
        if iid is not None:
            self._push_undo()
            delete_ids = self._with_carried_modules({iid})
            self.placed_buildings = [p for p in self.placed_buildings
                                      if p.instance_id not in delete_ids]
            self.selected_ids -= delete_ids
            self._rebuild_collision()
            self._notify_layout_change()
            self._notify_selection()
            self._redraw()

    def delete_selected(self):
        if not self.selected_ids:
            return
        self._push_undo()
        delete_ids = self._with_carried_modules(self.selected_ids)
        self.placed_buildings = [p for p in self.placed_buildings
                                  if p.instance_id not in delete_ids]
        self.selected_ids.clear()
        self._rebuild_collision()
        self._notify_layout_change()
        self._notify_selection()
        self._redraw()

    def clear_all(self):
        if not self.placed_buildings:
            return
        self._push_undo()
        self.placed_buildings.clear()
        self.selected_ids.clear()
        self._active_tech_effects.clear()
        self._active_item_effects.clear()
        self._active_item_boosts.clear()
        self._rebuild_collision()   # clears collision map + draw/road caches
        self._notify_layout_change()
        self._notify_selection()
        self._redraw()

    # ------------------------------------------------------------------ #
    #  Road swap  (Shift+U)
    # ------------------------------------------------------------------ #
    def _on_road_swap(self, event):
        """Shift+U: when exactly one road tile is selected, open a dropdown
        to replace every road of that type in the layout with another type."""
        if len(self.selected_ids) != 1:
            return
        iid = next(iter(self.selected_ids))
        pb  = next((p for p in self.placed_buildings if p.instance_id == iid), None)
        if not pb:
            return
        bd = self.dm.get_building(pb.guid)
        if not bd or 'road' not in bd.get_category_english().lower():
            return
        options = self._get_road_swap_options(pb.guid)
        if not options:
            return
        self._show_road_swap_dialog(pb.guid, options)

    def _get_road_swap_options(self, current_guid: int) -> list:
        """Return [(guid, display_name), ...] for other road types in the
        same region family (dirt/paved/marble), excluding the current one."""
        lang = getattr(self.app, 'language', 'english')
        for region in self.dm.get_regions():
            infra = self.dm.get_menu_section(region, 'infrastructure')
            road_guids = [
                it.get('guid') for it in infra.get('items', [])
                if it.get('type') == 'building'
                and (bd := self.dm.get_building(it.get('guid')))
                and 'road' in bd.get_category_english().lower()
            ]
            if current_guid in road_guids:
                return [
                    (g, self.dm.get_building(g).get_name(lang))
                    for g in road_guids if g != current_guid
                ]
        return []

    def _show_road_swap_dialog(self, from_guid: int, options: list):
        """Show a small borderless popup listing road types to swap to."""
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        popup.configure(bg=BORDER_GOLD)          # gold border via 1px bg padding

        inner = tk.Frame(popup, bg=BG_SECTION)
        inner.pack(padx=1, pady=1)

        tk.Label(inner, text="Swap ALL roads to:", bg=BG_SECTION,
                 fg=FG_GOLD, font=FONT_SMALL, padx=10, pady=6).pack(fill=tk.X)
        tk.Frame(inner, height=1, bg=BORDER_COLOR).pack(fill=tk.X, padx=6)

        for guid, name in options:
            def _pick(g=guid):
                try:
                    popup.destroy()
                except Exception:
                    pass
                self._swap_all_roads(from_guid, g)

            btn = tk.Button(inner, text=name, bg=BG_SECTION, fg=FG_MAIN,
                            activebackground=BG_HOVER, activeforeground=FG_GOLD,
                            relief=tk.FLAT, font=FONT_SMALL, anchor='w',
                            padx=12, pady=5, cursor='hand2', command=_pick)
            btn.pack(fill=tk.X, padx=2, pady=1)

        tk.Frame(inner, height=4, bg=BG_SECTION).pack()

        # Position centred over the canvas
        popup.update_idletasks()
        pw = popup.winfo_reqwidth()
        ph = popup.winfo_reqheight()
        rx = self.canvas.winfo_rootx() + (self.canvas.winfo_width()  - pw) // 2
        ry = self.canvas.winfo_rooty() + (self.canvas.winfo_height() - ph) // 2
        popup.geometry(f'+{rx}+{ry}')

        def _close(e=None):
            try:
                popup.destroy()
            except Exception:
                pass

        popup.bind('<Escape>', _close)
        popup.bind('<FocusOut>', lambda e: self.after(50, _close))
        popup.after(10, popup.focus_set)

    def _swap_all_roads(self, from_guid: int, to_guid: int):
        """Replace every placed building of from_guid with to_guid."""
        targets = [pb for pb in self.placed_buildings if pb.guid == from_guid]
        if not targets:
            return
        self._push_undo()
        for pb in targets:
            pb.guid = to_guid
        self._road_graph_dirty = True
        self._rebuild_collision()
        self._notify_layout_change()
        self._redraw()

    # ------------------------------------------------------------------ #
    #  Keyboard handlers
    # ------------------------------------------------------------------ #
    def _on_delete_key(self, event):
        if self.build_mode_guid is not None:
            self.cancel_build_mode()
        else:
            self.delete_selected()

    def _on_rotate_ccw(self, event):
        self.rotate_build(direction=-1)

    def _on_rotate_cw(self, event):
        self.rotate_build(direction=1)

    def rotate_build(self, direction: int = 1):
        """Rotate the current build-mode ghost. direction=1 CW, -1 CCW.

        Rotating an already-placed building/selection in place is handled
        by move mode (hotkey M) instead - see start_move_mode() - which
        re-ghosts the selection (so the normal ghost/paste rotation and
        collision logic applies) rather than trying to transform placed
        buildings' positions analytically."""
        if self.build_mode_guid is not None:
            old_rot = self.build_rotation
            self.build_rotation = (self.build_rotation + 45 * direction) % 360
            self._resnap_ghost(old_rot)
            self._notify_build_rotation()
            self._redraw()
        elif self._paste_active:
            self._rotate_paste_clipboard(direction)
            self._redraw()

    def _rotate_paste_clipboard(self, direction: int):
        """Rotate every item in the paste/move clipboard around the group's
        shared (fixed) anchor point, each by its own family-appropriate
        scale factor (see the removed in-place rotation feature's history
        for why a plain rotation matrix alone leaves gaps when crossing the
        45°/90° grid-family boundary). Unlike that removed feature, nothing
        here needs to "succeed unconditionally" - every item is a symmetric
        clipboard entry, and anything that doesn't fit once placed is simply
        held back for another attempt (see _commit_paste).

        Same-size/-type items (e.g. a block of modules) keep their mutual
        spacing exactly under this transform, since they all round to their
        shared native grid the same way. A differently-sized neighbour -
        typically the parent building touching that block - has a
        different snap-grid parity, so it can land individually-valid but
        no longer touching its former neighbour, opening a gap. That's
        repaired in a second pass below."""
        if not self._paste_clipboard:
            return
        bds = [self.dm.get_building(pb.guid) for pb in self._paste_clipboard]
        has_roads = any(bd and _is_road_like(bd) for bd in bds)
        ax, ay = self._paste_anchor_orig

        if has_roads:
            # When roads are present a 45° step would cross grid families,
            # which the road geometry can't handle.  Upgrade to a full 90°
            # step: both 90°-family (roads) and 45°-family (residences) stay
            # on their own grid, and the scale factor is 1 (no family change).
            theta = math.radians(90 * direction)
            cos_t, sin_t = math.cos(theta), math.sin(theta)
            for pb, bd in zip(self._paste_clipboard, bds):
                if not bd:
                    continue
                mcx, mcy = self._building_center(bd, pb.grid_x, pb.grid_y, pb.rotation)
                dx, dy = mcx - ax, mcy - ay
                ndx = dx * cos_t - dy * sin_t
                ndy = dx * sin_t + dy * cos_t
                new_rot = (pb.rotation + 90 * direction) % 360
                new_gx, new_gy = self._snap_anchor_from_center(bd, ax + ndx, ay + ndy, new_rot)
                pb.grid_x, pb.grid_y = new_gx, new_gy
                pb.rotation = new_rot
            return

        theta = math.radians(45 * direction)
        cos_t, sin_t = math.cos(theta), math.sin(theta)

        # Record which other clipboard items each one touches *before*
        # rotating, so a relationship broken by the transform can be found.
        old_polys = [
            self._get_poly_pts(bd, pb.grid_x, pb.grid_y, pb.rotation) if bd else None
            for pb, bd in zip(self._paste_clipboard, bds)
        ]
        touching_of = [set() for _ in self._paste_clipboard]
        for i, poly_i in enumerate(old_polys):
            if poly_i is None:
                continue
            for j in range(i + 1, len(old_polys)):
                if old_polys[j] is None:
                    continue
                if self._polys_touch(poly_i, old_polys[j]):
                    touching_of[i].add(j)
                    touching_of[j].add(i)

        for pb, bd in zip(self._paste_clipboard, bds):
            if not bd:
                continue
            mcx, mcy = self._building_center(bd, pb.grid_x, pb.grid_y, pb.rotation)
            dx, dy = mcx - ax, mcy - ay
            ndx = dx * cos_t - dy * sin_t
            ndy = dx * sin_t + dy * cos_t
            scale = (1 / SQRT2) if pb.rotation % 360 in (0, 90, 180, 270) else SQRT2
            ndx *= scale
            ndy *= scale
            new_rot = (pb.rotation + 45 * direction) % 360
            new_gx, new_gy = self._snap_anchor_from_center(bd, ax + ndx, ay + ndy, new_rot)
            pb.grid_x, pb.grid_y = new_gx, new_gy
            pb.rotation = new_rot

        # Repair: two passes so earlier fixes influence later items.
        # Pass combines two goals:
        #   (a) restore any touching relationship that the rotation broke
        #   (b) resolve any positional overlaps created by the snap
        # The early-exit skips repair only when genuinely touching a former
        # partner AND not overlapping anyone - _polys_touch returns True even
        # for full overlap, so we must check _polys_overlap separately first.
        #
        # Road-like buildings are skipped entirely: in 45° mode their polygon
        # is artificially inflated to 2×2 so they always overlap neighbouring
        # residences by design.  Running SAT overlap tests on them produces
        # false positives that drive the repair into nonsensical positions.
        for _ in range(2):
            for idx, partners in enumerate(touching_of):
                pb, bd = self._paste_clipboard[idx], bds[idx]
                if not bd:
                    continue
                if _is_road_like(bd):
                    continue
                cur_poly = self._get_poly_pts(bd, pb.grid_x, pb.grid_y, pb.rotation)
                blocker_polys = [
                    self._get_poly_pts(bds[k], self._paste_clipboard[k].grid_x,
                                       self._paste_clipboard[k].grid_y,
                                       self._paste_clipboard[k].rotation)
                    for k in range(len(self._paste_clipboard))
                    if k != idx and bds[k] and not _is_road_like(bds[k])
                ]
                has_overlap = any(self._polys_overlap(cur_poly, bp) for bp in blocker_polys)

                if partners:
                    partner_polys = [
                        self._get_poly_pts(bds[p], self._paste_clipboard[p].grid_x,
                                           self._paste_clipboard[p].grid_y,
                                           self._paste_clipboard[p].rotation)
                        for p in partners if bds[p]
                    ]
                    if not has_overlap and any(self._polys_touch(cur_poly, pp) for pp in partner_polys):
                        continue  # still properly touching a former partner, no overlap
                    fix = self._find_touching_position(bd, pb.grid_x, pb.grid_y, pb.rotation,
                                                       partner_polys, blocker_polys)
                    if fix:
                        pb.grid_x, pb.grid_y = fix
                        continue  # touching repair succeeded

                # No former partners (or touching repair found nothing): resolve overlap
                if has_overlap:
                    fix = self._find_non_overlapping_position(
                        bd, pb.grid_x, pb.grid_y, pb.rotation, blocker_polys)
                    if fix:
                        pb.grid_x, pb.grid_y = fix

    def _find_touching_position(self, bd: BuildingData, naive_gx: float, naive_gy: float,
                                 rotation: int, target_polys: list, blocker_polys: list,
                                 max_radius: int = 4):
        """Search nearby valid anchors (stepped on bd's own native grid for
        `rotation`) for the closest one that touches at least one of
        target_polys without overlapping any of blocker_polys."""
        rot = rotation % 360
        step = 1.0 if rot in (0, 90, 180, 270) else 0.5
        best = None
        best_dist2 = None
        for di in range(-max_radius, max_radius + 1):
            for dj in range(-max_radius, max_radius + 1):
                gx = naive_gx + di * step
                gy = naive_gy + dj * step
                poly = self._get_poly_pts(bd, gx, gy, rotation)
                if any(self._polys_overlap(poly, bp) for bp in blocker_polys):
                    continue
                if not any(self._polys_touch(poly, tp) for tp in target_polys):
                    continue
                dist2 = di * di + dj * dj
                if best_dist2 is None or dist2 < best_dist2:
                    best = (gx, gy)
                    best_dist2 = dist2
        return best

    def _find_non_overlapping_position(self, bd: BuildingData, naive_gx: float, naive_gy: float,
                                        rotation: int, blocker_polys: list,
                                        max_radius: int = 4):
        """Find the nearest valid anchor to (naive_gx, naive_gy) that doesn't
        overlap any of blocker_polys.  Used to de-overlap buildings that have
        no former touching partners but happened to collide after the rotation
        snap."""
        rot = rotation % 360
        step = 1.0 if rot in (0, 90, 180, 270) else 0.5
        best = None
        best_dist2 = None
        for di in range(-max_radius, max_radius + 1):
            for dj in range(-max_radius, max_radius + 1):
                gx = naive_gx + di * step
                gy = naive_gy + dj * step
                poly = self._get_poly_pts(bd, gx, gy, rotation)
                if any(self._polys_overlap(poly, bp) for bp in blocker_polys):
                    continue
                dist2 = di * di + dj * dj
                if best_dist2 is None or dist2 < best_dist2:
                    best = (gx, gy)
                    best_dist2 = dist2
        return best

    def _resnap_ghost(self, old_rot: int):
        """After build_rotation changed from old_rot, re-snap the ghost position."""
        if self._ghost_grid_pos is None or self.build_mode_guid is None:
            return
        bd = self.dm.get_building(self.build_mode_guid)
        if not bd:
            return
        gx, gy = self._ghost_grid_pos
        cx, cy = self._building_center(bd, gx, gy, old_rot)
        self._ghost_grid_pos = self._snap_anchor_from_center(bd, cx, cy, self.build_rotation)

    def _on_undo(self, event):
        self.undo()

    def _on_redo(self, event):
        self.redo()

    def _is_module_building(self, guid: int) -> bool:
        bd = self.dm.get_building(guid)
        return bd is not None and bd.get_category_english() in _DRAG_PLACEABLE_CATEGORIES

    def _on_copy(self, event):
        self._clipboard = []
        self._clipboard_effects = {}
        for pb in self.placed_buildings:
            if pb.instance_id not in self.selected_ids or self._is_module_building(pb.guid):
                continue
            clone = pb.clone()
            self._clipboard.append(clone)
            self._clipboard_effects[clone.instance_id] = {
                'tech':   set(self._active_tech_effects.get(pb.instance_id, set())),
                'items':  set(self._active_item_effects.get(pb.instance_id, set())),
                'boosts': set(self._active_item_boosts.get(pb.instance_id, set())),
            }

    def _on_paste(self, event):
        items = [pb for pb in self._clipboard if not self._is_module_building(pb.guid)]
        if not items:
            return
        if len(items) == 1:
            # Single building: enter placement/ghost mode so the user can choose where to put it.
            # _pending_paste_effects is consumed by the next _place_building call.
            # Must be set AFTER set_build_mode because _reset_placement_modes clears it.
            pb = items[0]
            self.build_rotation = pb.rotation
            self.set_build_mode(pb.guid)
            self._pending_paste_effects = self._clipboard_effects.get(pb.instance_id)
        else:
            # Multiple buildings: enter a group ghost mode that follows the
            # mouse as a unit; click to stamp it down (mirrors single-paste).
            self.cancel_build_mode()
            # Re-clone the clipboard items and carry their effects under the new instance ids.
            new_clipboard = []
            for pb in items:
                clone = pb.clone()
                new_clipboard.append(clone)
                src_eff = self._clipboard_effects.get(pb.instance_id)
                if src_eff:
                    self._clipboard_effects[clone.instance_id] = {
                        'tech':   set(src_eff['tech']),
                        'items':  set(src_eff['items']),
                        'boosts': set(src_eff['boosts']),
                    }
            self._paste_clipboard = new_clipboard
            min_gx = min(pb.grid_x for pb in items)
            min_gy = min(pb.grid_y for pb in items)
            self._paste_anchor_orig = (float(math.floor(min_gx)), float(math.floor(min_gy)))
            self._paste_active = True
            self._paste_ghost_pos = None
            self.selected_ids = set()
            self.canvas.config(cursor='crosshair')
            self._notify_selection()
            self._redraw()

    def start_move_mode(self):
        """Hotkey M: pick up the current selection (copy, delete the
        originals, re-ghost the copy in place) so it can be repositioned
        and/or rotated using the normal ghost/paste placement and collision
        logic before being placed back down. Cancelling restores the
        original(s) untouched; placing commits the new position/rotation."""
        if self.build_mode_guid is not None or self._paste_active:
            return
        self._move_id_remap = {}
        # Modules attached to a selected parent are carried along
        # automatically, matching the existing drag-to-move behaviour, even
        # if not themselves explicitly selected.
        move_ids = self._with_carried_modules(self.selected_ids)
        selected = [pb for pb in self.placed_buildings if pb.instance_id in move_ids]
        if not selected:
            return

        # Keep the snapshot in a local until after set_build_mode()/paste
        # setup below: those call _reset_placement_modes() internally,
        # which unconditionally clears self._move_restore_snapshot (it's
        # only restored-from if _move_mode_active was already True, but
        # it's wiped either way) - so assigning it to self before that
        # would just have it erased again.
        restore_snapshot = [pb.to_dict() for pb in selected]
        self.placed_buildings = [
            pb for pb in self.placed_buildings if pb.instance_id not in move_ids
        ]
        self.selected_ids = set()
        self._rebuild_collision()

        if len(selected) == 1:
            pb = selected[0]
            bd = self.dm.get_building(pb.guid)
            if not bd or not bd.is_placeable():
                for d in restore_snapshot:
                    self.placed_buildings.append(PlacedBuilding.from_dict(d))
                self._rebuild_collision()
                self._notify_layout_change()
                self._notify_selection()
                self._redraw()
                return
            self.set_build_mode(pb.guid, module_parent_id=pb.parent_id)
            self._pending_paste_effects = {
                'tech':   set(self._active_tech_effects.get(pb.instance_id, set())),
                'items':  set(self._active_item_effects.get(pb.instance_id, set())),
                'boosts': set(self._active_item_boosts.get(pb.instance_id, set())),
            }
            self.build_rotation = pb.rotation
            self._ghost_grid_pos = (pb.grid_x, pb.grid_y)
            self._move_mode_active = True
            self._move_restore_snapshot = restore_snapshot
        else:
            # Clone with fresh instance ids, remapping parent_id references
            # so a module moved together with its parent stays linked to
            # the parent's new instance instead of the deleted original.
            id_map: dict = {}
            clones = []
            self._clipboard_effects = {}
            for pb in selected:
                c = PlacedBuilding(pb.guid, pb.grid_x, pb.grid_y, pb.rotation,
                                   instance_id=None, parent_id=pb.parent_id)
                id_map[pb.instance_id] = c.instance_id
                clones.append(c)
                self._clipboard_effects[c.instance_id] = {
                    'tech':   set(self._active_tech_effects.get(pb.instance_id, set())),
                    'items':  set(self._active_item_effects.get(pb.instance_id, set())),
                    'boosts': set(self._active_item_boosts.get(pb.instance_id, set())),
                }
            for c in clones:
                if c.parent_id in id_map:
                    c.parent_id = id_map[c.parent_id]

            self._paste_clipboard = clones
            min_gx = min(c.grid_x for c in clones)
            min_gy = min(c.grid_y for c in clones)
            self._paste_anchor_orig = (float(math.floor(min_gx)), float(math.floor(min_gy)))
            self._paste_active = True
            self._paste_ghost_pos = self._paste_anchor_orig
            self.canvas.config(cursor='crosshair')
            self._move_mode_active = True
            self._move_restore_snapshot = restore_snapshot

        self._notify_layout_change()
        self._notify_selection()
        self._redraw()

    def _on_select_all(self, event):
        self.selected_ids = {pb.instance_id for pb in self.placed_buildings}
        self._notify_selection()
        self._redraw()

    # ------------------------------------------------------------------ #
    #  Undo / Redo
    # ------------------------------------------------------------------ #
    def _push_undo(self):
        # Shallow copy: PlacedBuilding objects are immutable after placement so
        # a pointer-list copy is a valid snapshot.  ~0.5 ms for 9000 buildings
        # vs ~45 ms for the old [pb.to_dict() for pb in ...] serialisation.
        snapshot = list(self.placed_buildings)
        self._undo_stack.append(snapshot)
        if len(self._undo_stack) > self._max_history:
            self._undo_stack.pop(0)
        self._redo_stack.clear()

    def undo(self):
        if not self._undo_stack:
            return
        self._redo_stack.append(list(self.placed_buildings))
        snapshot = self._undo_stack.pop()
        self._restore_snapshot(snapshot)

    def redo(self):
        if not self._redo_stack:
            return
        self._undo_stack.append(list(self.placed_buildings))
        snapshot = self._redo_stack.pop()
        self._restore_snapshot(snapshot)

    def _restore_snapshot(self, snapshot: list):
        # Handle both new-style (PlacedBuilding objects) and old-style (dicts)
        # snapshots so undo/redo survives mixed stacks after a code update.
        if snapshot and isinstance(snapshot[0], dict):
            self.placed_buildings = [PlacedBuilding.from_dict(d) for d in snapshot]
        else:
            self.placed_buildings = list(snapshot)
        self.selected_ids.clear()
        self._rebuild_collision()
        self._notify_layout_change()
        self._notify_selection()
        self._redraw()

    # ------------------------------------------------------------------ #
    #  Save / Load
    # ------------------------------------------------------------------ #
    def get_layout_dict(self) -> dict:
        tech = {str(iid): list(guids)
                for iid, guids in self._active_tech_effects.items() if guids}
        items = {str(iid): list(guids)
                 for iid, guids in self._active_item_effects.items() if guids}
        boosts = {str(iid): list(guids)
                  for iid, guids in self._active_item_boosts.items() if guids}
        d = {
            'version': 1,
            'buildings': [pb.to_dict() for pb in self.placed_buildings],
            'active_tech_effects': tech,
            'active_item_effects': items,
            'active_item_boosts': boosts,
        }
        if self._island_name:
            d['island'] = self._island_name
        return d

    def load_layout_dict(self, data: dict):
        self._push_undo()
        raw = data.get('buildings', [])
        self.placed_buildings = [PlacedBuilding.from_dict(d) for d in raw]
        if self.placed_buildings:
            max_id = max(pb.instance_id for pb in self.placed_buildings)
            PlacedBuilding._next_id = max_id + 1
        self._active_tech_effects = {
            int(iid): set(guids)
            for iid, guids in data.get('active_tech_effects', {}).items()
        }
        self._active_item_effects = {
            int(iid): set(guids)
            for iid, guids in data.get('active_item_effects', {}).items()
        }
        self._active_item_boosts = {
            int(iid): set(guids)
            for iid, guids in data.get('active_item_boosts', {}).items()
        }
        self.selected_ids.clear()
        # Restore island if the layout was saved with one
        island_name = data.get('island')
        if island_name:
            self._load_island_data_only(island_name)
        else:
            self.clear_island()
        self._rebuild_collision()
        self._notify_layout_change()
        self._notify_selection()
        self.fit_view()

    def fit_view(self, margin_tiles: int = 2):
        """Zoom and pan so the entire layout is centred and visible."""
        if self._island_tiles is not None:
            self._fit_island_view()
            return
        if not self.placed_buildings:
            self._center_view()
            return
        self.update_idletasks()
        cw = self.canvas.winfo_width()  or 800
        ch = self.canvas.winfo_height() or 600

        # Bounding box in grid tiles
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        for pb in self.placed_buildings:
            bd = self.dm.get_building(pb.guid)
            if not bd:
                continue
            rot = pb.rotation % 360
            if rot in (0, 90, 180, 270):
                w = bd.width  if rot in (0, 180) else bd.height
                h = bd.height if rot in (0, 180) else bd.width
            else:
                nw, nh = _get_45_grid_counts(bd, rot)
                w = h = (nw + nh) * 0.5
            min_x = min(min_x, pb.grid_x)
            min_y = min(min_y, pb.grid_y)
            max_x = max(max_x, pb.grid_x + w)
            max_y = max(max_y, pb.grid_y + h)

        layout_w = max_x - min_x
        layout_h = max_y - min_y
        if layout_w <= 0 or layout_h <= 0:
            self._center_view()
            return

        # Tile size that fits the layout plus margin on each side
        avail_w = cw - 2 * margin_tiles * DEFAULT_TILE_SIZE
        avail_h = ch - 2 * margin_tiles * DEFAULT_TILE_SIZE
        ts = min(avail_w / layout_w, avail_h / layout_h)
        ts = max(MIN_TILE_SIZE, min(MAX_TILE_SIZE, ts))

        # Centre the layout in the canvas
        cx_grid = (min_x + max_x) / 2
        cy_grid = (min_y + max_y) / 2
        self.tile_size = ts
        self.pan_x = cw / 2 - cx_grid * ts
        self.pan_y = ch / 2 - cy_grid * ts
        self._photo_cache.clear()
        self._redraw()

    # ------------------------------------------------------------------ #
    #  Island rotation
    # ------------------------------------------------------------------ #

    def rotate_layout(self, degrees: int):
        """Rotate the entire layout (island + all buildings) by degrees (multiple of 90).
        Positive = clockwise, negative = counter-clockwise."""
        if self._island_tiles is None:
            return
        steps = (degrees // 90) % 4
        for _ in range(steps):
            self._rotate_90_cw()

    def _rotate_90_cw(self):
        """Rotate island tiles, quads, and all placed buildings 90° clockwise in-place."""
        W = self._island_w   # old width  (columns)
        H = self._island_h   # old height (rows)

        # ── Rotate tile/quad arrays ──────────────────────────────────────
        n = W * H
        new_tiles = bytearray(n)
        new_quads = bytearray(n) if self._island_quads is not None else None

        for old_row in range(H):
            for old_col in range(W):
                # 90° CW: new_col = H-1-old_row, new_row = old_col
                # New grid is H wide and W tall.
                new_col = H - 1 - old_row
                new_row = old_col
                old_idx = old_row * W + old_col
                new_idx = new_row * H + new_col
                new_tiles[new_idx] = self._island_tiles[old_idx]
                if new_quads is not None:
                    new_quads[new_idx] = _rotate_quad_90cw(self._island_quads[old_idx])

        self._island_tiles = bytes(new_tiles)
        self._island_quads = bytes(new_quads) if new_quads is not None else None
        self._island_w, self._island_h = H, W

        # PIL rotate(-90) = 90° CW in screen coordinates (y-down)
        if self._island_base_img_dark is not None:
            self._island_base_img_dark  = self._island_base_img_dark.rotate(-90, expand=True)
        if self._island_base_img_light is not None:
            self._island_base_img_light = self._island_base_img_light.rotate(-90, expand=True)
        self._island_photo_ref = None
        self._island_bg_cache_key = None
        self._island_chunk_photos.clear()
        self._drawn_island_chunks.clear()

        # ── Transform building positions ──────────────────────────────────
        # For 90° CW rotation of an H-row grid:
        #   new_gx = H - old_gy - eff_height_in_old_orientation
        #   new_gy = old_gx
        #   new_rotation = (old_rotation + 90) % 360
        new_placed = []
        for pb in self.placed_buildings:
            bd  = self.dm.get_building(pb.guid)
            rot = pb.rotation % 360
            if bd:
                if rot in (0, 180):
                    eff_h = bd.height
                elif rot in (90, 270):
                    eff_h = bd.width
                else:
                    nw, nh = _get_45_grid_counts(bd, rot)
                    eff_h = (nw + nh) * 0.5
            else:
                eff_h = 1.0

            new_placed.append(PlacedBuilding(
                pb.guid,
                H - pb.grid_y - eff_h,   # new_gx
                pb.grid_x,                # new_gy
                (rot + 90) % 360,
                pb.instance_id,
                parent_id=pb.parent_id,
            ))
        self.placed_buildings = new_placed

        # ── Rebuild derived state ─────────────────────────────────────────
        self._rebuild_collision()
        self._road_graph_dirty = True
        self._module_touch_pairs_cache = None
        self._parent_color_ranks_cache = None
        self._draw_order_cache = None
        self._notify_layout_change()
        self._redraw()

    # ------------------------------------------------------------------ #
    #  Island import
    # ------------------------------------------------------------------ #

    def clear_island(self):
        """Remove the active island overlay and unblock all island tiles."""
        self._island_name = None
        self._island_w = 0
        self._island_h = 0
        self._island_tiles = None
        self._island_quads = None
        self._island_base_img_dark = None
        self._island_base_img_light = None
        self._island_photo_ref = None
        self._island_bg_cache_key = None
        self._island_chunk_photos.clear()
        self._drawn_island_chunks.clear()

    def _unpack_island_tiles(self, raw: bytes):
        """
        Unpack island tile bytes from island_data.json.
        Version 3 format: lower nibble = tile type (0-4), upper nibble = quadrant mask.
        Version 2 format: full byte = tile type (no quads).

        Data is stored with X=0 at the east edge; we flip each row left-right here
        so col=0 matches the west edge as shown on canvas (matches building import coords).
        """
        version = self.dm.get_island_version()
        iw = self._island_w
        if version >= 3:
            raw_tiles = bytearray(b & 0x0F for b in raw)
            raw_quads = bytearray((b >> 4) & 0x0F for b in raw)
        else:
            raw_tiles = bytearray(raw)
            raw_quads = None

        # Reverse each row (left-right mirror) and mirror quad cut masks:
        # NE(3)↔NW(6), SE(9)↔SW(12) — W and E bits swap, N and S are unchanged.
        _QUAD_LR = {3: 6, 6: 3, 9: 12, 12: 9}
        n = len(raw_tiles)
        for rs in range(0, n, iw):
            raw_tiles[rs:rs + iw] = raw_tiles[rs:rs + iw][::-1]
            if raw_quads is not None:
                rq = raw_quads[rs:rs + iw]
                raw_quads[rs:rs + iw] = bytearray(_QUAD_LR.get(q, q) for q in reversed(rq))

        self._island_tiles = bytes(raw_tiles)
        self._island_quads = bytes(raw_quads) if raw_quads is not None else None

    def load_island(self, name: str):
        """Load island outline from data_manager and apply it to the canvas."""
        import tkinter.messagebox as _mb
        data = self.dm.get_island(name)
        if not data:
            _mb.showerror("Island", f"Island '{name}' not found in data.")
            return

        self._island_name = name
        self._island_w    = data['width']
        self._island_h    = data['height']
        self._unpack_island_tiles(base64.b64decode(data['tiles']))

        if PIL_AVAILABLE:
            self._island_base_img_dark  = self._make_island_base_image(dark=True)
            self._island_base_img_light = self._make_island_base_image(dark=False)
        else:
            self._island_base_img_dark = None
            self._island_base_img_light = None
        self._island_photo_ref = None
        self._island_bg_cache_key = None
        self._island_chunk_photos.clear()
        self._drawn_island_chunks.clear()

        # Clear buildings so the user starts fresh on the new island
        self.placed_buildings.clear()
        self._active_tech_effects.clear()
        self._active_item_effects.clear()
        self._active_item_boosts.clear()
        self._undo_stack.clear()
        self._redo_stack.clear()
        self.selected_ids.clear()
        self._rebuild_collision()
        self._notify_layout_change()
        self._notify_selection()
        self._fit_island_view()

    def _load_island_data_only(self, name: str):
        """Load island geometry without clearing buildings (used by load_layout_dict)."""
        data = self.dm.get_island(name)
        if not data:
            return
        self._island_name = name
        self._island_w    = data['width']
        self._island_h    = data['height']
        self._unpack_island_tiles(base64.b64decode(data['tiles']))
        if PIL_AVAILABLE:
            self._island_base_img_dark  = self._make_island_base_image(dark=True)
            self._island_base_img_light = self._make_island_base_image(dark=False)
        else:
            self._island_base_img_dark = None
            self._island_base_img_light = None
        self._island_photo_ref = None
        self._island_bg_cache_key = None

    def _make_island_base_image(self, dark: bool):
        """Return a PIL Image at 1px/tile with island tile colours."""
        if not PIL_AVAILABLE:
            return None
        palette = _ISLE_COLORS[not dark]   # keyed by light_mode bool

        def _hex_rgb(h):
            h = h.lstrip('#')
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

        # Pre-build colour table indexed by tile value
        color_table = {tile: _hex_rgb(hex_col)
                       for tile, hex_col in palette.items()}
        sea_rgb = color_table[_ISLE_SEA]

        tiles = self._island_tiles
        pixels = [color_table.get(t, sea_rgb) for t in tiles]

        img = Image.new('RGB', (self._island_w, self._island_h))
        img.putdata(pixels)
        return img

    def _draw_island_bg(self):
        """Draw the island background as a grid of small cached chunks.

        Each chunk covers _island_chunk_size × _island_chunk_size tiles.  At
        ts≈7 each chunk is ~224×224 px (~2 ms to create via _get_island_chunk)
        vs the old single-image approach (~200 ms for a full-canvas PIL resize
        + ImageTk.PhotoImage conversion).  During panning, _fill_island_chunks()
        is called on every pan event so newly revealed edges are immediately
        filled with the correct terrain colour — no leading-edge artifacts.
        """
        if not PIL_AVAILABLE or self._island_tiles is None:
            return
        c   = self.canvas
        cw  = c.winfo_width()
        ch  = c.winfo_height()
        ts  = self.tile_size
        cs  = self._island_chunk_size
        px0 = self.pan_x
        py0 = self.pan_y

        cx_lo = max(0, int(math.floor(-px0 / (ts * cs))))
        cx_hi = int(math.ceil((cw - px0) / (ts * cs)))
        cy_lo = max(0, int(math.floor(-py0 / (ts * cs))))
        cy_hi = int(math.ceil((ch - py0) / (ts * cs)))

        for cx in range(cx_lo, cx_hi + 1):
            for cy in range(cy_lo, cy_hi + 1):
                photo = self._get_island_chunk(cx, cy)
                if photo is None:
                    continue
                px = cx * cs * ts + px0
                py = cy * cs * ts + py0
                c.create_image(px, py, anchor='nw', image=photo, tags='island_bg')
                self._drawn_island_chunks.add((cx, cy))

    def _render_island_quads(self, img, left: int, top: int,
                              right: int, bottom: int, ts: float):
        """
        Overlay the 45° diagonal cuts encoded in _island_quads onto the
        already-NEAREST-scaled PIL image.

        Each Anno tile is divided into four compass triangles (N/E/S/W) by its
        two diagonals (TL→BR and BL→TR) meeting at the tile centre.  The
        TileQuadrants mask (1=W 2=S 4=E 8=N) says which two triangles are
        KEPT in the tile's own colour; the complementary two are the CUT area,
        filled here with the colour of the tile in the diagonal cut direction.

        Cut shapes — each is a quadrilateral covering the two cut compass
        triangles (through the tile centre C):

          NE cut (0b0011, W+S kept): TL-TR-BR-C  (N triangle + E triangle)
          NW cut (0b0110, S+E kept): TR-TL-BL-C  (N triangle + W triangle)
          SE cut (0b1001, W+N kept): TR-BR-BL-C  (E triangle + S triangle)
          SW cut (0b1100, E+N kept): TL-BL-BR-C  (W triangle + S triangle)
        """
        draw = ImageDraw.Draw(img)
        iw, ih = self._island_w, self._island_h
        quads  = self._island_quads
        tiles  = self._island_tiles
        light  = self.light_mode.get()

        def _hex_rgb(h):
            h = h.lstrip('#')
            return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

        color_table = {t: _hex_rgb(col) for t, col in _ISLE_COLORS[light].items()}

        def color_at(tx, ty):
            if 0 <= tx < iw and 0 <= ty < ih:
                return color_table.get(tiles[ty * iw + tx], color_table[_ISLE_SEA])
            return color_table[_ISLE_SEA]

        for ty in range(top, bottom):
            for tx in range(left, right):
                i = ty * iw + tx
                if i >= len(quads):
                    continue
                q = quads[i]
                if not q:
                    continue

                # Pixel corners and centre of this tile in the scaled image
                px0 = round((tx - left)     * ts)
                py0 = round((ty - top)      * ts)
                px1 = round((tx - left + 1) * ts)
                py1 = round((ty - top  + 1) * ts)
                cx  = (px0 + px1) // 2
                cy  = (py0 + py1) // 2

                # Both open sides share the same type, so use either cardinal neighbour.
                if q == 0b0011:   # NE cut — fill N+E with north neighbour colour
                    c = color_at(tx, ty - 1)
                    draw.polygon([(px0, py0), (px1, py0), (px1, py1), (cx, cy)], fill=c)
                elif q == 0b0110: # NW cut — fill N+W with north neighbour colour
                    c = color_at(tx, ty - 1)
                    draw.polygon([(px1, py0), (px0, py0), (px0, py1), (cx, cy)], fill=c)
                elif q == 0b1001: # SE cut — fill S+E with south neighbour colour
                    c = color_at(tx, ty + 1)
                    draw.polygon([(px1, py0), (px1, py1), (px0, py1), (cx, cy)], fill=c)
                elif q == 0b1100: # SW cut — fill S+W with south neighbour colour
                    c = color_at(tx, ty + 1)
                    draw.polygon([(px0, py0), (px0, py1), (px1, py1), (cx, cy)], fill=c)

    def _fit_island_view(self, margin_px: int = 20):
        """Fit the view to show the whole island (tight bbox of non-sea tiles)."""
        if not self._island_tiles:
            self._center_view()
            return
        self.update_idletasks()
        cw = self.canvas.winfo_width()  or 800
        ch = self.canvas.winfo_height() or 600

        # Tight bounding box of non-sea tiles
        iw, ih = self._island_w, self._island_h
        min_col, min_row = iw, ih
        max_col, max_row = -1, -1
        for row in range(ih):
            for col in range(iw):
                if self._island_tiles[row * iw + col] != _ISLE_SEA:
                    if col < min_col: min_col = col
                    if col > max_col: max_col = col
                    if row < min_row: min_row = row
                    if row > max_row: max_row = row
        if max_col < 0:
            min_col, min_row, max_col, max_row = 0, 0, iw - 1, ih - 1

        bbox_w = max_col + 1 - min_col
        bbox_h = max_row + 1 - min_row
        ts = min(
            (cw - 2 * margin_px) / bbox_w,
            (ch - 2 * margin_px) / bbox_h,
        )
        ts = max(1.0, min(MAX_TILE_SIZE, ts))
        self.tile_size = ts
        self.pan_x = cw / 2 - (min_col + bbox_w / 2) * ts
        self.pan_y = ch / 2 - (min_row + bbox_h / 2) * ts
        self._photo_cache.clear()
        self._redraw()

    # ------------------------------------------------------------------ #
    #  Layout stats (called by right panel)
    # ------------------------------------------------------------------ #
    def get_layout_stats(self) -> dict:
        if not self.placed_buildings:
            return {
                'bbox_w': 0, 'bbox_h': 0, 'bbox_area': 0,
                'compact_area': 0, 'efficiency': 0.0,
                'building_counts': {},
                'total_construction': {},
                'total_maintenance': {},
                'effect_bonuses': [],
            }
        dm = self.dm

        # Build tile → winning road instance map.
        # Winner = highest priority; first-placed wins among equal priority.
        road_tile_owner: dict = {}  # tile -> (priority, instance_id)
        for pb in self.placed_buildings:
            bd = dm.get_building(pb.guid)
            if not bd:
                continue
            pri = _road_priority(bd)
            if pri == 0:
                continue
            for t in _get_occupied_tiles(bd, pb.grid_x, pb.grid_y, pb.rotation):
                existing = road_tile_owner.get(t)
                if existing is None or pri > existing[0]:
                    road_tile_owner[t] = (pri, pb.instance_id)

        # Precompute UV rects for all 45°-family road tiles.
        # Used to detect geometrically-dominated middle tiles in diagonal staircases.
        road_45_uvs: dict = {}  # instance_id -> (u0, u1, v0, v1, priority)
        for _pb in self.placed_buildings:
            _bd = dm.get_building(_pb.guid)
            if not _bd:
                continue
            _pri = _road_priority(_bd)
            if _pri == 0:
                continue
            if _pb.rotation % 360 not in (0, 90, 180, 270):
                _uv = _road_45_uv(_bd, _pb)
                road_45_uvs[_pb.instance_id] = (*_uv, _pri)

        # Bounding box
        min_x = min_y = float('inf')
        max_x = max_y = float('-inf')
        compact_area = 0.0
        road_area    = 0.0

        const_totals: dict = {}
        maint_totals: dict = {}
        building_counts: dict = {}
        building_guids:  dict = {}   # name → representative guid for icon lookup

        for pb in self.placed_buildings:
            bd = dm.get_building(pb.guid)
            if not bd:
                continue

            # Skip road instances that don't own any tile (fully overlaid or same-priority overlap)
            pri = _road_priority(bd)
            if pri > 0:
                tiles = _get_occupied_tiles(bd, pb.grid_x, pb.grid_y, pb.rotation)
                if not any(road_tile_owner.get(t, (0, None))[1] == pb.instance_id
                           for t in tiles):
                    continue
                # For 45° roads: skip if UV footprint is fully covered by other
                # same-or-higher priority 45° roads (handles diagonal staircase gaps).
                if pb.rotation % 360 not in (0, 90, 180, 270) and pb.instance_id in road_45_uvs:
                    u0, u1, v0, v1, _ = road_45_uvs[pb.instance_id]
                    covering = [
                        (d[0], d[1], d[2], d[3])
                        for iid, d in road_45_uvs.items()
                        if iid != pb.instance_id and d[4] >= pri
                    ]
                    if _uv_fully_covered(u0, u1, v0, v1, covering):
                        continue

            rot = pb.rotation % 360
            if rot in (0, 90, 180, 270):
                w = bd.width if rot in (0, 180) else bd.height
                h = bd.height if rot in (0, 180) else bd.width
            else:
                nw, nh = _get_45_grid_counts(bd, rot)
                w = h = (nw + nh) * 0.5

            min_x = min(min_x, pb.grid_x)
            min_y = min(min_y, pb.grid_y)
            max_x = max(max_x, pb.grid_x + w)
            max_y = max(max_y, pb.grid_y + h)
            tile_area = w * h
            compact_area += tile_area
            if pri > 0:
                road_area += tile_area

            # Count
            name = bd.get_name(self.app.language)
            building_counts[name] = building_counts.get(name, 0) + 1
            building_guids.setdefault(name, bd.guid)

            # Costs
            for cc in bd.construction_costs:
                pid = cc['product']
                const_totals[pid] = const_totals.get(pid, 0) + cc['amount']
            for mc in bd.maintenance_costs:
                pid = mc['product']
                maint_totals[pid] = maint_totals.get(pid, 0) + mc['amount']

        bbox_w = max_x - min_x
        bbox_h = max_y - min_y
        bbox_area = bbox_w * bbox_h
        non_road_area = compact_area - road_area
        efficiency = (non_road_area / bbox_area * 100) if bbox_area > 0 else 0.0

        # Effect bonus aggregation across all placed buildings
        effect_attr_totals: dict = {}   # attr -> total value
        effect_attr_icons:  dict = {}   # attr -> icon path (first seen)
        for pb in self.placed_buildings:
            bd = dm.get_building(pb.guid)
            if not bd or not bd.radius or not isinstance(bd.radius, dict):
                continue
            active_tech = self._active_tech_effects.get(pb.instance_id, set())
            active_items_s = self._active_item_effects.get(pb.instance_id, set())
            active_boosts_s = self._active_item_boosts.get(pb.instance_id, set())
            if not bd.functional_effects and not bd.public_service_effect and not active_tech and not active_items_s:
                continue
            in_range = self.get_in_range_guids(
                bd, pb.grid_x, pb.grid_y, pb.rotation, exclude_id=pb.instance_id,
                active_tech_guids=active_tech)
            if not in_range:
                continue
            for bonus in dm.compute_radius_bonuses(bd.guid, in_range, active_tech,
                                                   active_items_s, active_boosts_s):
                attr = bonus['attr']
                effect_attr_totals[attr] = effect_attr_totals.get(attr, 0.0) + bonus['total']
                effect_attr_icons.setdefault(attr, bonus['icon'])

        # Item bonuses (direct per-building, not radius-based)
        for pb in self.placed_buildings:
            active_items = self._active_item_effects.get(pb.instance_id, set())
            if not active_items:
                continue
            active_boosts = self._active_item_boosts.get(pb.instance_id, set())
            for bonus in dm.compute_item_bonuses(pb.guid, active_items, active_boosts):
                attr = bonus['attr']
                effect_attr_totals[attr] = effect_attr_totals.get(attr, 0.0) + bonus['total']
                effect_attr_icons.setdefault(attr, bonus['icon'])

        return {
            'bbox_w': round(bbox_w, 1),
            'bbox_h': round(bbox_h, 1),
            'bbox_area': round(bbox_area, 1),
            'compact_area': round(compact_area, 1),
            'efficiency': round(efficiency, 1),
            'building_counts': building_counts,
            'building_guids':  building_guids,
            'total_construction': const_totals,
            'total_maintenance': maint_totals,
            'effect_bonuses': [
                {'attr': k, 'total': v, 'icon': effect_attr_icons[k]}
                for k, v in sorted(effect_attr_totals.items())
            ],
        }

    # ------------------------------------------------------------------ #
    #  Notifications to other panels
    # ------------------------------------------------------------------ #
    def _notify_layout_change(self):
        if hasattr(self.app, 'mark_dirty'):
            self.app.mark_dirty()
        if hasattr(self.app, 'info_panel'):
            self.app.info_panel.update_stats(self.get_layout_stats())

    def upgrade_building(self, instance_id: int, new_guid: int):
        """Replace a placed building's GUID in-place (upgrade to next stage)."""
        pb = next((p for p in self.placed_buildings if p.instance_id == instance_id), None)
        if not pb:
            return
        new_bd = self.dm.get_building(new_guid)
        if not new_bd:
            return
        self._push_undo()
        pb.guid = new_guid
        self._rebuild_collision()
        self._notify_layout_change()
        self._redraw()
        if hasattr(self.app, 'building_info_panel'):
            free_tiles = None
            if new_bd.free_area_productivity:
                free_tiles = self._count_free_tiles_in_radius(pb, new_bd)
            self.app.building_info_panel.show_building(new_bd, pb.rotation,
                                                       free_tiles, placed_building=pb)

    def _count_free_tiles_in_radius(self, pb: 'PlacedBuilding',
                                     bd: BuildingData) -> int:
        """Count empty grid tiles within influenceRadius using quarter-triangle sampling.

        Each tile is divided into 4 diagonal triangles; the centroid of each
        triangle is tested against the radius.  Total in-radius centroids are
        divided by 4 and rounded - matching how the game calculates the value.
        Quarter-triangle centroids (relative to tile col, row):
          top:   (+0.5, +1/6)   right: (+5/6, +0.5)
          bottom:(+0.5, +5/6)   left:  (+1/6, +0.5)
        """
        fap = bd.free_area_productivity
        if not fap or not isinstance(fap, dict):
            return 0
        radius = fap.get('influenceRadius', 0)
        if not radius:
            return 0
        gcx, gcy = self._building_center(bd, pb.grid_x, pb.grid_y, pb.rotation)
        r_int = int(math.ceil(radius)) + 1
        cx0 = int(math.floor(gcx))
        cy0 = int(math.floor(gcy))
        _S = 1 / 6
        _L = 5 / 6
        quarters = 0
        for dy in range(-r_int, r_int + 1):
            for dx in range(-r_int, r_int + 1):
                col = cx0 + dx
                row = cy0 + dy
                if (col, row) in self._collision_map:
                    continue
                for qx, qy in ((col + 0.5, row + _S), (col + _L, row + 0.5),
                               (col + 0.5, row + _L), (col + _S, row + 0.5)):
                    if math.hypot(qx - gcx, qy - gcy) <= radius:
                        quarters += 1
        return round(quarters / 4)

    def _notify_selection(self):
        if hasattr(self.app, 'building_info_panel'):
            # Find selected building data
            if len(self.selected_ids) == 1:
                iid = next(iter(self.selected_ids))
                pb = next((p for p in self.placed_buildings
                           if p.instance_id == iid), None)
                if pb:
                    bd = self.dm.get_building(pb.guid)
                    free_tiles = None
                    if bd and bd.free_area_productivity:
                        free_tiles = self._count_free_tiles_in_radius(pb, bd)
                    self.app.building_info_panel.show_building(bd, pb.rotation,
                                                               free_tiles,
                                                               placed_building=pb)
                    return
            self.app.building_info_panel.clear()

    def _notify_build_rotation(self):
        if self.build_mode_guid is not None and hasattr(self.app, 'building_info_panel'):
            bd = self.dm.get_building(self.build_mode_guid)
            if bd:
                self.app.building_info_panel.show_building(bd, self.build_rotation)

    # ------------------------------------------------------------------ #
    #  Export to PNG
    # ------------------------------------------------------------------ #
    def export_png(self, path: str, padding: int = 1,
                   include_info_stats: Optional[dict] = None):
        """Export current layout to PNG with icons and optional info side-panel."""
        if not PIL_AVAILABLE:
            messagebox.showerror("Export Error",
                                 "Pillow is not installed. Cannot export PNG.\n"
                                 "Run: pip install Pillow")
            return

        has_island  = self._island_tiles is not None
        has_buildings = bool(self.placed_buildings)

        if not has_buildings and not has_island:
            messagebox.showinfo("Export", "No buildings placed.")
            return

        dm = self.dm
        if has_island:
            # Tight bounding box of non-sea tiles, with a 1-tile sea border.
            iw, ih = self._island_w, self._island_h
            min_col, min_row = iw, ih
            max_col, max_row = -1, -1
            for row in range(ih):
                for col in range(iw):
                    if self._island_tiles[row * iw + col] != _ISLE_SEA:
                        if col < min_col: min_col = col
                        if col > max_col: max_col = col
                        if row < min_row: min_row = row
                        if row > max_row: max_row = row
            if max_col < 0:  # all sea — fallback to full extent
                min_col, min_row, max_col, max_row = 0, 0, iw - 1, ih - 1
            _border = 1
            min_x = float(max(0, min_col - _border))
            min_y = float(max(0, min_row - _border))
            max_x = float(min(iw, max_col + 1 + _border))
            max_y = float(min(ih, max_row + 1 + _border))
            padding = 0
        elif has_buildings:
            min_x = min_y = float('inf')
            max_x = max_y = float('-inf')
            for pb in self.placed_buildings:
                bd = dm.get_building(pb.guid)
                if not bd:
                    continue
                rot = pb.rotation % 360
                if rot in (0, 90, 180, 270):
                    w = bd.width if rot in (0, 180) else bd.height
                    h = bd.height if rot in (0, 180) else bd.width
                else:
                    nw, nh = _get_45_grid_counts(bd, rot)
                    w = h = (nw + nh) * 0.5
                min_x = min(min_x, pb.grid_x)
                min_y = min(min_y, pb.grid_y)
                max_x = max(max_x, pb.grid_x + w)
                max_y = max(max_y, pb.grid_y + h)

        ts = 40
        if has_island:
            # Scale down tile size so the image stays under ~6000 px on each side.
            max_extent = max(max_x - min_x, max_y - min_y)
            if max_extent * ts > 6000:
                ts = max(4, int(6000 / max_extent))
        dark = (11, 25, 44)
        light = (255, 255, 255)
        lm = self.light_mode.get()
        if has_island:
            def _hex_int(h):
                h = h.lstrip('#')
                return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))
            bg_col = _hex_int(_ISLE_COLORS[lm][_ISLE_SEA])
        else:
            bg_col = light if lm else dark
        grid_col = (26, 26, 26) if lm else (30, 53, 80)

        canvas_w = int((max_x - min_x + 2 * padding) * ts)
        canvas_h = int((max_y - min_y + 2 * padding) * ts)

        # ── Info side-panel ──────────────────────────────────────────────
        INFO_W = 260
        info_img = None
        if include_info_stats:
            info_img = self._render_info_panel_image(include_info_stats, INFO_W, canvas_h)

        total_w = canvas_w + (INFO_W if info_img else 0)
        img  = Image.new('RGB', (total_w, canvas_h), color=bg_col)
        draw = ImageDraw.Draw(img)

        # ── Island background ─────────────────────────────────────────────
        if has_island:
            base = self._island_base_img_light if lm else self._island_base_img_dark
            if base is not None:
                # Crop to the region shown in this export (in island tile coords)
                cx0 = max(0, int(min_x) - padding)
                cy0 = max(0, int(min_y) - padding)
                cx1 = min(self._island_w, int(math.ceil(max_x)) + padding)
                cy1 = min(self._island_h, int(math.ceil(max_y)) + padding)
                if cx1 > cx0 and cy1 > cy0:
                    crop = base.crop((cx0, cy0, cx1, cy1))
                    # Scale to export pixels
                    sc_w = int((cx1 - cx0) * ts)
                    sc_h = int((cy1 - cy0) * ts)
                    if sc_w > 0 and sc_h > 0:
                        scaled = crop.resize((sc_w, sc_h), Image.NEAREST)
                        # Paste offset: where (cx0, cy0) lands in the export image
                        ox = int((cx0 - (min_x - padding)) * ts)
                        oy = int((cy0 - (min_y - padding)) * ts)
                        img.paste(scaled, (ox, oy))

        # Grid
        for col in range(int(min_x) - padding, int(max_x) + padding + 1):
            x = int((col - min_x + padding) * ts)
            draw.line([(x, 0), (x, canvas_h)], fill=grid_col)
        for row in range(int(min_y) - padding, int(max_y) + padding + 1):
            y = int((row - min_y + padding) * ts)
            draw.line([(0, y), (canvas_w, y)], fill=grid_col)

        # Snapshot view settings for consistent export
        exp_road_outline  = self.road_show_outline.get()
        exp_road_icon     = self.road_show_icon.get()
        exp_module_icon   = self.module_show_icon.get()

        def _hex_to_rgb_tuple(h):
            h = h.lstrip('#')
            return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))

        # Buildings
        lang = getattr(self.app, 'language', 'english')
        parent_color_ranks = self._get_parent_color_ranks()
        for pb in self.placed_buildings:
            bd = dm.get_building(pb.guid)
            if not bd:
                continue
            cat_hex  = self._resolve_render_color(pb, bd, parent_color_ranks)
            fill_rgb = _hex_to_rgb_tuple(cat_hex)
            rot = pb.rotation % 360
            bx  = int((pb.grid_x - min_x + padding) * ts)
            by  = int((pb.grid_y - min_y + padding) * ts)

            is_road   = _is_road_like(bd)
            is_module = bd.get_category_english() in _DRAG_PLACEABLE_CATEGORIES
            # Road solid-fill override
            if is_road and not exp_road_icon:
                eng_name = bd.get_name('english')
                road_hex = ROAD_FILL_COLORS.get(eng_name, ROAD_FILL_DEFAULT)
                fill_rgb = _hex_to_rgb_tuple(road_hex)
            # Outline: suppress border for roads when outline is off
            outline_col = fill_rgb if (is_road and not exp_road_outline) else (0, 0, 0)
            show_icon = not (is_road and not exp_road_icon) and \
                        not (is_module and not exp_module_icon)

            if rot in (0, 90, 180, 270):
                bw_t = bd.width  if rot in (0, 180) else bd.height
                bh_t = bd.height if rot in (0, 180) else bd.width
                bw   = int(bw_t * ts)
                bh   = int(bh_t * ts)
                draw.rectangle([bx, by, bx + bw, by + bh],
                               fill=fill_rgb, outline=outline_col, width=1)
                cx, cy = bx + bw // 2, by + bh // 2
                if show_icon:
                    icon_px = max(8, int(min(bw, bh) * 0.7))
                    icon    = self._load_icon_pil(bd, icon_px)
                    if icon:
                        img.paste(icon, (cx - icon_px // 2, cy - icon_px // 2),
                                  icon if icon.mode == 'RGBA' else None)
                    elif bw >= 20:
                        draw.text((cx, cy), bd.get_name(lang)[:6],
                                  fill=(255, 255, 255), anchor='mm')
            else:
                nw, nh = _get_45_grid_counts(bd, rot)
                half   = 0.25 * ts
                bbox_px = (nw + nh) * half
                cx = bx + bbox_px
                cy = by + bbox_px
                pts = [
                    (cx + (nh - nw) * half, cy - (nw + nh) * half),
                    (cx + (nw + nh) * half, cy + (nw - nh) * half),
                    (cx + (nw - nh) * half, cy + (nw + nh) * half),
                    (cx - (nw + nh) * half, cy + (nh - nw) * half),
                ]
                draw.polygon(pts, fill=fill_rgb, outline=outline_col)
                if show_icon:
                    icon_px = max(8, int(bbox_px * 0.9))
                    icon = self._load_icon_pil(bd, icon_px)
                    if icon:
                        img.paste(icon, (int(cx - icon_px // 2), int(cy - icon_px // 2)),
                                  icon if icon.mode == 'RGBA' else None)

        # Paste info panel
        if info_img:
            # Separator line
            draw.line([(canvas_w, 0), (canvas_w, canvas_h)], fill=(60, 90, 120), width=1)
            img.paste(info_img, (canvas_w, 0))

        img.save(path)
        messagebox.showinfo("Export", f"Exported to:\n{path}")

    def _load_icon_pil(self, bd: 'BuildingData', size: int) -> Optional['Image.Image']:
        """Return a PIL Image for a building icon (no tkinter cache)."""
        if not PIL_AVAILABLE or not bd.icon_path:
            return None
        full = resource_path(bd.icon_path)
        try:
            if os.path.exists(full):
                return Image.open(full).convert('RGBA').resize((size, size), Image.LANCZOS)
        except Exception:
            pass
        return None

    def _render_info_panel_image(self, stats: dict, width: int, height: int) -> 'Image.Image':
        """Render layout statistics as a PIL image for PNG export."""
        from PIL import ImageFont
        bg = (22, 42, 69)
        fg_main  = (255, 255, 255)
        fg_gold  = (241, 196, 15)
        fg_dim   = (170, 170, 170)
        panel = Image.new('RGB', (width, height), color=bg)
        draw  = ImageDraw.Draw(panel)

        try:
            font_path = resource_path('data/fonts/Marcellus-Regular.ttf')
            font_h = ImageFont.truetype(font_path, 20)
            font_s = ImageFont.truetype(font_path, 17)
        except Exception:
            font_h = ImageFont.load_default()
            font_s = font_h

        y = 14
        lh = 24  # line height
        icon_size = lh - 2

        def _icon_path(path: str):
            """Load a PIL RGBA image from a relative resource path, or None."""
            if not path:
                return None
            full = resource_path(path)
            try:
                if os.path.exists(full):
                    return Image.open(full).convert('RGBA').resize(
                        (icon_size, icon_size), Image.LANCZOS)
            except Exception:
                pass
            return None

        def line(text, color=fg_main, font=font_s, indent=0):
            nonlocal y
            draw.text((10 + indent, y), text, fill=color, font=font)
            y += lh

        def icon_line(icon_img, text, color=fg_main, font=font_s):
            """Draw icon + text on one row."""
            nonlocal y
            if icon_img:
                panel.paste(icon_img, (10, y + 1),
                            icon_img if icon_img.mode == 'RGBA' else None)
            draw.text((10 + icon_size + 4, y), text, fill=color, font=font)
            y += lh

        def sep():
            nonlocal y
            draw.line([(8, y + 2), (width - 8, y + 2)], fill=(60, 90, 120))
            y += 8

        line("Layout Info", color=fg_gold, font=font_h)
        sep()

        line(f"Bounding Box:  {stats['bbox_w']} × {stats['bbox_h']}", color=fg_main)
        line(f"Area:          {stats['bbox_area']} tiles", color=fg_dim)
        line(f"Minimum Area:     {stats['compact_area']} tiles", color=fg_main)
        line(f"Efficiency:    {stats['efficiency']:.1f}%",
             color=fg_gold if stats['efficiency'] >= 70 else fg_dim)
        sep()

        line("Buildings:", color=fg_gold, font=font_h)
        guids = stats.get('building_guids', {})
        for name, cnt in sorted(stats['building_counts'].items()):
            if y > height - 60:
                icon_line(None, "…", color=fg_dim)
                break
            bd_icon = self.dm.get_building(guids.get(name, -1))
            icon = self._load_icon_pil(bd_icon, icon_size) if bd_icon else None
            icon_line(icon, f"×{cnt}  {name}")
        sep()

        if stats.get('total_construction'):
            line("Construction Cost:", color=fg_gold, font=font_h)
            for pid, amt in sorted(stats['total_construction'].items()):
                if y > height - 40:
                    break
                pd   = self.dm.get_product(pid)
                icon = _icon_path(pd.icon_path if pd else '')
                icon_line(icon, f"{amt}  {self.dm.get_product_name(pid)}")
            sep()

        if stats.get('total_maintenance'):
            line("Maintenance:", color=fg_gold, font=font_h)
            for pid, amt in sorted(stats['total_maintenance'].items()):
                if y > height - 20:
                    break
                pd   = self.dm.get_product(pid)
                icon = _icon_path(pd.icon_path if pd else '')
                icon_line(icon, f"{amt}  {self.dm.get_product_name(pid)}")

        return panel
