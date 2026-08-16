"""
savegame_parser.py – Parse Anno 117 .a8s savegame files.

Pipeline (from Anno117.cs by oliversaggau):
  1. RdaConsole  extracts .a8s → data.a7s  (single file, still named .a7s)
  2. Zlib-decompress data.a7s  → raw FileDB V3 bytes
  3. FileDBReader converts FileDB → outer XML
  4. Stream outer XML:  find MetaGameManager/GameSessions/None/SessionData/BinaryData
  5. Decode the BinaryData hex blob → inner FileDB binary
  6. FileDBReader converts inner FileDB → session XML
  7. Stream session XML:
       MapTemplate/TemplateElement/Element  → island positions
       AreaInfo                             → player island IDs (OwnerProfile == 41)
       AreaManagers/AreaManager_N/AreaObjectManager/GameObject/Objects
                                            → player-placed game objects

Critical facts confirmed from reference C# implementation:
  - GUID_PROFILE_HUMAN = 41  (OwnerProfile attribute value for player islands)
  - Blueprint flag: StateBits & 102 != 0
  - Island sizes are exact (from .a7minfo files), NOT derived from name categories
  - Buildings are in AreaObjectManager/GameObject/Objects  (NOT AreaObjectManager/objects)
  - Building GUID attribute name in FileDB is "Guid" (capital G, may appear either case in XML)
"""

import math
import re
import shutil
import struct
import subprocess
import tempfile
import zlib
from dataclasses import dataclass, field
from data_manager import _snap_to_half_sqrt2_count, _get_45_grid_counts, _snap_45_anchor, _ROAD_LIKE_INFRA_NAMES
from pathlib import Path
from typing import Optional


# ── Constants ─────────────────────────────────────────────────────────────────

SESSION_REGIONS: dict[int, str] = {
    3245: "Latium",
    6627: "Albion",
}

# Player profile GUID – OwnerProfile == 41 means this island belongs to the player.
GUID_PROFILE_HUMAN = 41

# Blueprint / ghost-building bitmask (from Anno117.cs: (state & 102) != 0)
BLUEPRINT_MASK = 102

# GUID remapping: savegame uses alternate GUIDs for some road types
# 81354 (Aqueduct MaxGround variant) → 19723 (Aqueduct Segment)
# 82038 (road variant)               → 29525
GUID_REMAP: dict[int, int] = {81354: 19723, 82038: 29525}

# Exact island sizes extracted from .a7minfo files (Anno117.cs IslandSizes dict).
# Keys are map-file stems; values are (width, height) in tiles.
ISLAND_SIZES: dict[str, tuple[int, int]] = {
    "roman_island_extralarge_01": (512, 512),
    "roman_island_extralarge_02": (448, 448),
    "roman_island_extralarge_03": (512, 512),
    "roman_island_extralarge_04": (512, 512),
    "roman_island_large_01":     (512, 512),
    "roman_island_large_02":     (512, 512),
    "roman_island_large_03":     (512, 512),
    "roman_island_large_04":     (512, 512),
    "roman_island_large_05":     (512, 512),
    "roman_island_large_06":     (384, 384),
    "roman_island_large_07":     (512, 512),
    "roman_island_large_09":     (512, 512),
    "roman_island_medium_01":    (320, 320),
    "roman_island_medium_02":    (256, 256),
    "roman_island_medium_03":    (320, 320),
    "roman_island_medium_04":    (320, 320),
    "roman_island_medium_05":    (256, 256),
    "roman_island_medium_06":    (320, 320),
    "roman_island_medium_07":    (320, 320),
    "roman_island_medium_08":    (320, 320),
    "roman_island_small_01":     (256, 256),
    "roman_island_small_02":     (192, 192),
    "roman_island_small_03":     (256, 256),
    "roman_island_small_04":     (256, 256),
    "roman_island_small_05":     (256, 256),
    "roman_island_small_06":     (256, 256),
    "roman_island_small_07":     (256, 256),
    "celtic_island_large_01":    (512, 512),
    "celtic_island_large_02":    (512, 512),
    "celtic_island_large_03":    (384, 384),
    "celtic_island_large_04":    (512, 512),
    "celtic_island_large_05":    (512, 512),
    "celtic_island_large_06":    (384, 384),
    "celtic_island_large_07":    (320, 320),
    "celtic_island_large_08":    (384, 384),
    "celtic_island_medium_01":   (256, 256),
    "celtic_island_medium_02":   (256, 256),
    "celtic_island_medium_03":   (256, 256),
    "celtic_island_medium_04":   (256, 256),
    "celtic_island_medium_05":   (320, 320),
    "celtic_island_medium_06":   (320, 320),
    "celtic_island_medium_07":   (256, 256),
    "celtic_island_small_01":    (256, 256),
    "celtic_island_small_02":    (256, 256),
    "celtic_island_small_03":    (256, 256),
    "celtic_island_small_04":    (256, 256),
    "celtic_island_small_05":    (256, 256),
    "celtic_island_small_06":    (192, 192),
    "celtic_island_small_07":    (192, 192),
}


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class BuildingImport:
    guid:         int
    col:          float  # island-relative grid column (NW corner; float for 45°-grid tiles)
    row:          float  # island-relative grid row    (NW corner; float for 45°-grid tiles)
    direction:    int    # rotation: 0 / 45 / 90 / 135 / 180 / 225 / 270 / 315
    is_blueprint: bool = False
    nibble:       int  = 0   # SubTilesGrid polygon tile: 4-bit bitmask (0 = normal building)
    # Grid position of the matched parent farm building (nibble tiles only).
    # Used to link nibble tiles to their parent PlacedBuilding after import.
    parent_col:   Optional[float] = None
    parent_row:   Optional[float] = None


@dataclass
class IslandImport:
    name:       str      # city name or map stem
    island_key: str      # map-file stem, e.g. "roman_island_extralarge_01"
    region:     str      # e.g. "Latium" or "Albion"
    session_id: int
    world_x:    float    # island tile origin X
    world_z:    float    # island tile origin Z
    rotation90: int = 0
    buildings:  list[BuildingImport] = field(default_factory=list)


class ParseError(Exception):
    """Raised when the savegame cannot be parsed."""


# ── Binary helpers ────────────────────────────────────────────────────────────

def _le_uint16(hex4: str) -> int:
    return struct.unpack('<H', bytes.fromhex(hex4))[0]

def _le_uint32(hex8: str) -> int:
    return struct.unpack('<I', bytes.fromhex(hex8))[0]

def _le_uint32_pair(hex16: str) -> tuple[int, int]:
    return struct.unpack('<II', bytes.fromhex(hex16))

def _le_float(hex8: str) -> float:
    return struct.unpack('<f', bytes.fromhex(hex8))[0]

def _le_int32(hex8: str) -> int:
    return struct.unpack('<i', bytes.fromhex(hex8))[0]

def _le_floats(hex_str: str) -> list[float]:
    b = bytes.fromhex(hex_str)
    n = len(b) // 4
    return list(struct.unpack(f'<{n}f', b[:n * 4]))

def _hex_to_utf16(hex_str: str) -> str:
    try:
        return bytes.fromhex(hex_str).decode('utf-16-le').rstrip('\x00')
    except Exception:
        return ''

def _get_island_size(name: str) -> Optional[tuple[int, int]]:
    return ISLAND_SIZES.get(name.lower())


# ── Tool wrappers ─────────────────────────────────────────────────────────────

def _rda_extract(rda_exe: Path, a8s_path: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [str(rda_exe), 'extract', '-f', str(a8s_path), '-o', str(out_dir), '-y', '-n'],
        capture_output=True, text=True, timeout=90,
    )
    if result.returncode != 0:
        raise ParseError(f"RdaConsole failed (exit {result.returncode}):\n{result.stderr[:500]}")


def _filedb_to_xml(fdb_exe: Path, input_path: Path, timeout: int = 300) -> Path:
    result = subprocess.run(
        [str(fdb_exe), 'decompress', '-f', input_path.name, '-y'],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(input_path.parent),
    )
    xml_path = input_path.with_suffix('.xml')
    if not xml_path.exists() or xml_path.stat().st_size == 0:
        raise ParseError(
            f"FileDBReader produced no XML for '{input_path.name}'.\n"
            f"Exit {result.returncode}\n"
            f"stdout: {(result.stdout or '').strip()[:300]}\n"
            f"stderr: {(result.stderr or '').strip()[:300]}"
        )
    return xml_path


def _zlib_decompress(src: Path, dst: Path) -> None:
    data = src.read_bytes()
    try:
        dst.write_bytes(zlib.decompress(data))
    except zlib.error as exc:
        raise ParseError(f"zlib decompression of '{src.name}' failed: {exc}") from exc


def _find_file(search_dir: Path, patterns: tuple[str, ...]) -> Optional[Path]:
    for pat in patterns:
        matches = [p for p in search_dir.rglob(pat) if p.is_file()]
        if matches:
            return max(matches, key=lambda p: p.stat().st_size)
    return None


def _list_files(d: Path) -> str:
    files = [p for p in d.rglob('*') if p.is_file()]
    names = [p.name for p in files[:12]]
    return ', '.join(names) + (f' (+{len(files)-12} more)' if len(files) > 12 else '') or '(none)'


# ── Outer XML: extract session BinaryData blobs ───────────────────────────────

def _extract_all_session_binaries(outer_xml_path: Path, work_dir: Path,
                                   progress_cb) -> list[Path]:
    """
    Stream the outer XML and collect ALL BinaryData blobs found under
    MetaGameManager/GameSessions (one per game-world session, e.g. Latium + Albion).
    Returns a list of Paths to the extracted binary files.
    """
    if progress_cb:
        progress_cb("Locating session BinaryData in outer XML…")

    _RE_BINARY = re.compile(r'<BinaryData>([0-9A-Fa-f]+)</BinaryData>')
    in_game_sessions = False
    results: list[Path] = []

    with open(outer_xml_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            s = line.strip()
            if '<GameSessions>' in s:
                in_game_sessions = True
            elif '</GameSessions>' in s:
                break
            if in_game_sessions:
                m = _RE_BINARY.search(s)
                if m:
                    idx = len(results)
                    bin_path = work_dir / f'session_{idx}.bin'
                    bin_path.write_bytes(bytes.fromhex(m.group(1)))
                    results.append(bin_path)
    return results


# ── Session XML streaming parser ──────────────────────────────────────────────
#
# FileDBReader outputs one tag/value per line.  We exploit this to parse
# the 200+ MB session XML without loading it all into memory.
#
# Sections used:
#   MapTemplate/TemplateElement/Element  → island positions & file paths
#   AreaInfo                             → player area IDs (OwnerProfile == 41)
#   AreaManagers/AreaManager_N           → buildings per island
#     └─ AreaObjectManager/GameObject/Objects
#          └─ <None> elements: Guid, Position, Direction, StateBits
#
# IMPORTANT: AreaObjectManager contains two child sections:
#   1. <objects> (lowercase) – world/background objects – SKIP these
#   2. <GameObject><Objects> – player-placed game objects – READ these
# ─────────────────────────────────────────────────────────────────────────────

# Regex patterns
_RE_AM_OPEN   = re.compile(r'^<AreaManager_(\d+)>$')
_RE_AM_CLOSE  = re.compile(r'^</AreaManager_(\d+)>$')
# Building fields – FileDB may output Guid (capital) or guid (lower); match both
_RE_FIELD     = re.compile(
    r'^<(Guid|guid|Position|Direction|StateBits)>'
    r'([0-9A-Fa-f]+)'
    r'</(?:Guid|guid|Position|Direction|StateBits)>$'
)
# MapTemplate island position (16 hex = 2×uint32)
_RE_POS_HDR   = re.compile(r'^<Position>([0-9A-Fa-f]{16})</Position>$')
_RE_PATH_HDR  = re.compile(r'^<MapFilePath>([0-9A-Fa-f]+)</MapFilePath>$')
_RE_ROT_HDR   = re.compile(r'^<Rotation90>([0-9A-Fa-f]+)</Rotation90>$')
# AreaInfo key (2-byte LE uint16 → 4 hex chars)
_RE_AI_KEY    = re.compile(r'^<None>([0-9A-Fa-f]{4})</None>$')
_RE_AI_OWNER  = re.compile(r'^<OwnerProfile>([0-9A-Fa-f]{8})</OwnerProfile>$')
# Road/aqueduct graph edge fields
# Polygon GUIDs use <GUID> (all-caps); road edge GUIDs use <guid> (lower); include both.
_RE_ROAD_GUID   = re.compile(r'^<(?:GUID|Guid|guid)>([0-9A-Fa-f]+)</(?:GUID|Guid|guid)>$')
_RE_ROAD_POSMIN = re.compile(r'^<PosMin>([0-9A-Fa-f]{16})</PosMin>$')
_RE_ROAD_POSMAX = re.compile(r'^<PosMax>([0-9A-Fa-f]{16})</PosMax>$')
# Polygon object (farm field) fields
# GridOriginWS is serialised as a single 8-byte (16 hex char) inline value:
#   <GridOriginWS>9800000014070000</GridOriginWS>  → X=0x98, Y=0x0714
_RE_POLY_ORIGIN = re.compile(r'^<GridOriginWS>([0-9A-Fa-f]{16})</GridOriginWS>$')
# x/y/bits tags are lowercase in the FileDBReader output; also accept the capital
# variants for resilience.  <x> = bits-per-row (divide by 4 for nibble columns),
# <y> = row count, <bits> = raw nibble-packed byte data.
_RE_POLY_XY     = re.compile(r'^<([xyXY])>([0-9A-Fa-f]+)</\1>$')
_RE_POLY_BITS   = re.compile(r'^<(?:bits|Bits)>([0-9A-Fa-f]+)</(?:bits|Bits)>$')

# Road-graph manager tag sets (street, aqueduct, canal, hedge, wall)
_ROAD_MGR_OPEN  = frozenset((
    '<AreaStreetManager>', '<AreaAqueductManager>',
    '<AreaCanalManager>',  '<AreaHedgeManager>', '<AreaWallManager>',
))
_ROAD_MGR_CLOSE = frozenset((
    '</AreaStreetManager>', '</AreaAqueductManager>',
    '</AreaCanalManager>',  '</AreaHedgeManager>', '</AreaWallManager>',
))


def _parse_session_xml(session_xml_path: Path, progress_cb):
    """
    Stream-parse the session XML.

    Returns:
        island_templates      : list of dicts  {x, z, w, h, rot, name}
        area_buildings        : dict  area_id → list of (guid, wx, wy, wz, dir_rad, is_blueprint)
        area_road_edges       : dict  area_id → list of (guid, wx1, wz1, wx2, wz2)
        area_polygon_objects  : dict  area_id → list of (guid, origin_x, origin_y, bits:bytes, rows)
    """
    if progress_cb:
        progress_cb("Streaming session XML for buildings…")

    island_templates: list[dict]  = []
    area_buildings:   dict[int, list] = {}

    # ── MapTemplate state ─────────────────────────────────────────────────────
    in_map_template   = False
    in_templ_element  = False
    in_element        = False     # <Element> inside <TemplateElement>
    t_pos = t_path = t_rot = None

    # ── AreaInfo state ────────────────────────────────────────────────────────
    in_area_info      = False
    ai_pending_key: Optional[int] = None
    in_ai_block       = False
    ai_block_depth    = 0
    ai_block_owner: Optional[int] = None

    # ── AreaManager / building state ──────────────────────────────────────────
    current_am_id: Optional[int] = None
    in_area_om    = False   # inside <AreaObjectManager>
    in_game_obj   = False   # inside <GameObject> within AreaObjectManager
    in_objects    = False   # inside <Objects> within GameObject
    none_depth    = 0

    # per-object accumulators
    o_guid = o_pos = o_dir = o_state = None

    # ── Road / canal / hedge / wall / aqueduct graph state ───────────────────
    in_road_mgr     = False
    in_road_graph   = False
    in_road_edges   = False
    road_none_depth = 0
    road_in_edge    = False
    r_guid = r_posmin = r_posmax = None
    area_road_edges: dict[int, list] = {}

    # ── Polygon object (farm field) state ─────────────────────────────────────
    in_polygon_mgr  = False
    in_polygons     = False
    poly_none_depth = 0
    in_origin_ws    = False
    in_subtiles     = False
    p_guid = p_ox = p_oy = p_bits = p_rows = p_cols = None
    area_polygon_objects: dict[int, list] = {}

    # ── Diagnostic: unknown Area*Manager tags (for farm-field tag discovery) ──
    _known_area_tags = frozenset((
        '<AreaObjectManager>', '</AreaObjectManager>',
        '<AreaStreetManager>', '</AreaStreetManager>',
        '<AreaAqueductManager>', '</AreaAqueductManager>',
        '<AreaCanalManager>', '</AreaCanalManager>',
        '<AreaHedgeManager>', '</AreaHedgeManager>',
        '<AreaWallManager>', '</AreaWallManager>',
        '<AreaPolygonObjectManager>', '</AreaPolygonObjectManager>',
    ))
    _unknown_area_tags: set[str] = set()

    with open(session_xml_path, 'r', encoding='utf-8', errors='replace') as f:
        for raw_line in f:
            line = raw_line.strip()

            # ── MapTemplate ───────────────────────────────────────────────────
            if line == '<MapTemplate>':
                in_map_template = True
                continue
            if line == '</MapTemplate>':
                in_map_template = False
                continue

            if in_map_template:
                if '<TemplateElement>' in line:
                    in_templ_element = True
                    in_element = False
                    t_pos = t_path = t_rot = None
                    continue
                if '</TemplateElement>' in line:
                    in_templ_element = False
                    in_element = False
                    t_pos = t_path = t_rot = None
                    continue
                if in_templ_element:
                    # The actual data lives one level deeper in <Element>
                    if line == '<Element>':
                        in_element = True
                        t_pos = t_path = t_rot = None
                        continue
                    if line == '</Element>':
                        if t_pos and t_path:
                            try:
                                px, pz = _le_uint32_pair(t_pos)
                                island_name = Path(_hex_to_utf16(t_path)).stem.lower()
                                rot = int(t_rot, 16) if t_rot else 0
                                sz = _get_island_size(island_name)
                                if sz:
                                    island_templates.append({
                                        'x': px, 'z': pz,
                                        'w': sz[0], 'h': sz[1],
                                        'rot': rot, 'name': island_name,
                                    })
                            except Exception:
                                pass
                        in_element = False
                        t_pos = t_path = t_rot = None
                        continue
                    if in_element:
                        m = _RE_POS_HDR.match(line)
                        if m: t_pos = m.group(1); continue
                        m = _RE_PATH_HDR.match(line)
                        if m: t_path = m.group(1); continue
                        m = _RE_ROT_HDR.match(line)
                        if m: t_rot = m.group(1); continue
                    # Fallback: some versions may place Position/MapFilePath directly
                    # under TemplateElement (without an Element wrapper)
                    if not in_element:
                        m = _RE_POS_HDR.match(line)
                        if m: t_pos = m.group(1); continue
                        m = _RE_PATH_HDR.match(line)
                        if m: t_path = m.group(1); continue
                        m = _RE_ROT_HDR.match(line)
                        if m: t_rot = m.group(1); continue
                continue

            # ── AreaInfo (player island identification) ───────────────────────
            if line == '<AreaInfo>':
                in_area_info = True
                continue
            if line == '</AreaInfo>':
                in_area_info = False
                continue

            if in_area_info:
                if not in_ai_block:
                    m = _RE_AI_KEY.match(line)
                    if m:
                        try:
                            ai_pending_key = _le_uint16(m.group(1))
                        except Exception:
                            ai_pending_key = None
                    elif line == '<None>' and ai_pending_key is not None:
                        in_ai_block    = True
                        ai_block_depth = 0
                        ai_block_owner = None
                else:
                    if line == '<None>':
                        ai_block_depth += 1
                    elif line == '</None>':
                        if ai_block_depth > 0:
                            ai_block_depth -= 1
                        else:
                            # End of this AreaInfo block
                            if (ai_pending_key is not None
                                    and ai_block_owner == GUID_PROFILE_HUMAN):
                                area_buildings.setdefault(ai_pending_key, [])
                            ai_pending_key = None
                            ai_block_owner = None
                            in_ai_block    = False
                    elif ai_block_depth == 0:
                        mo = _RE_AI_OWNER.match(line)
                        if mo:
                            try:
                                ai_block_owner = _le_uint32(mo.group(1))
                            except Exception:
                                pass
                continue

            # ── AreaManager open/close ────────────────────────────────────────
            m = _RE_AM_OPEN.match(line)
            if m:
                am_id = int(m.group(1))
                if am_id in area_buildings:
                    current_am_id = am_id
                else:
                    current_am_id = None
                in_area_om  = False
                in_game_obj = False
                in_objects  = False
                none_depth  = 0
                in_road_mgr = False
                in_road_graph = in_road_edges = road_in_edge = False
                road_none_depth = 0
                in_polygon_mgr = in_polygons = False
                poly_none_depth = 0
                in_origin_ws = in_subtiles = False
                p_guid = p_ox = p_oy = p_bits = p_rows = p_cols = None
                continue

            m = _RE_AM_CLOSE.match(line)
            if m:
                current_am_id = None
                in_area_om    = False
                in_game_obj   = False
                in_objects    = False
                none_depth    = 0
                in_road_mgr = False
                in_road_graph = in_road_edges = road_in_edge = False
                road_none_depth = 0
                in_polygon_mgr = in_polygons = False
                poly_none_depth = 0
                in_origin_ws = in_subtiles = False
                p_guid = p_ox = p_oy = p_bits = p_rows = p_cols = None
                continue

            if current_am_id is None:
                continue

            # ── AreaObjectManager ─────────────────────────────────────────────
            if line == '<AreaObjectManager>':
                in_area_om  = True
                in_game_obj = False
                in_objects  = False
                none_depth  = 0
                continue
            if line == '</AreaObjectManager>':
                in_area_om  = False
                in_game_obj = False
                in_objects  = False
                none_depth  = 0
                continue

            # ── Collect unknown Area*Manager tags for diagnostics ─────────────
            if (line.startswith('<Area') and line.endswith('>')
                    and line not in _known_area_tags):
                _unknown_area_tags.add(line)

            # ── Road-graph managers (street, aqueduct, canal, hedge, wall) ──
            if line in _ROAD_MGR_OPEN:
                in_road_mgr   = True
                in_polygon_mgr = False
                in_road_graph = in_road_edges = road_in_edge = False
                road_none_depth = 0
                continue
            if line in _ROAD_MGR_CLOSE:
                in_road_mgr   = False
                in_road_graph = in_road_edges = road_in_edge = False
                road_none_depth = 0
                continue

            if in_road_mgr:
                if line == '<Graph>':
                    in_road_graph = True
                    continue
                if line == '</Graph>':
                    in_road_graph = False
                    in_road_edges = False
                    continue
                if not in_road_graph:
                    continue
                if line == '<Edges>':
                    in_road_edges = True
                    road_none_depth = 0
                    continue
                if line == '</Edges>':
                    in_road_edges = False
                    road_none_depth = 0
                    continue
                if not in_road_edges:
                    continue
                if line == '<None>':
                    road_none_depth += 1
                    if road_none_depth == 1:
                        r_guid = r_posmin = r_posmax = None
                        road_in_edge = False
                    continue
                if line == '</None>':
                    if road_none_depth == 1 and r_guid and r_posmin and r_posmax:
                        try:
                            guid_int = _le_uint32(r_guid[:8])
                            x1, z1 = _le_uint32_pair(r_posmin)
                            x2, z2 = _le_uint32_pair(r_posmax)
                            area_road_edges.setdefault(current_am_id, []).append(
                                (guid_int, x1 / 2.0, z1 / 2.0, x2 / 2.0, z2 / 2.0)
                            )
                        except Exception:
                            pass
                        r_guid = r_posmin = r_posmax = None
                        road_in_edge = False
                    road_none_depth = max(0, road_none_depth - 1)
                    continue
                if road_none_depth != 1:
                    continue
                m = _RE_ROAD_GUID.match(line)
                if m:
                    r_guid = m.group(1)
                    continue
                if line == '<Edge>':
                    road_in_edge = True
                    continue
                if line == '</Edge>':
                    road_in_edge = False
                    continue
                if road_in_edge:
                    m = _RE_ROAD_POSMIN.match(line)
                    if m:
                        r_posmin = m.group(1)
                        continue
                    m = _RE_ROAD_POSMAX.match(line)
                    if m:
                        r_posmax = m.group(1)
                        continue
                continue

            # ── AreaPolygonObjectManager (farm field SubTilesGrid polygons) ────
            if line == '<AreaPolygonObjectManager>':
                in_polygon_mgr = True
                in_road_mgr    = False
                in_polygons    = False
                poly_none_depth = 0
                in_origin_ws = in_subtiles = False
                p_guid = p_ox = p_oy = p_bits = p_rows = p_cols = None
                continue
            if line == '</AreaPolygonObjectManager>':
                in_polygon_mgr = False
                in_polygons    = False
                continue

            if in_polygon_mgr:
                if line == '<Polygons>':
                    in_polygons = True
                    poly_none_depth = 0
                    continue
                if line == '</Polygons>':
                    in_polygons = False
                    continue
                if not in_polygons:
                    continue
                if line == '<None>':
                    poly_none_depth += 1
                    if poly_none_depth == 1:
                        p_guid = p_ox = p_oy = p_bits = p_rows = p_cols = None
                        in_origin_ws = in_subtiles = False
                    continue
                if line == '</None>':
                    if poly_none_depth == 1 and p_guid and p_ox is not None and p_oy is not None and p_bits and p_rows is not None:
                        try:
                            guid_int = _le_uint32(p_guid[:8])
                            origin_x = _le_int32(p_ox[:8])
                            origin_y = _le_int32(p_oy[:8])
                            rows_int = _le_uint32(p_rows[:8])
                            # cols: x attribute is bits-per-row; divide by 4 for nibbles.
                            # Fall back to full stride (no column limit) if x was absent.
                            cols_int = (_le_uint32(p_cols[:8]) // 4) if p_cols else 0
                            if guid_int and rows_int > 0:
                                area_polygon_objects.setdefault(current_am_id, []).append(
                                    (guid_int, origin_x, origin_y,
                                     bytes.fromhex(p_bits), int(rows_int), int(cols_int))
                                )
                        except Exception:
                            pass
                        p_guid = p_ox = p_oy = p_bits = p_rows = p_cols = None
                        in_origin_ws = in_subtiles = False
                    poly_none_depth = max(0, poly_none_depth - 1)
                    continue
                if poly_none_depth != 1:
                    continue
                # GridOriginWS: may be an inline 8-byte hex value OR separate <X>/<Y> elements.
                m = _RE_POLY_ORIGIN.match(line)
                if m:
                    p_ox = m.group(1)[:8]
                    p_oy = m.group(1)[8:]
                    continue
                if line == '<GridOriginWS>':
                    in_origin_ws = True;  continue
                if line == '</GridOriginWS>':
                    in_origin_ws = False; continue
                if line == '<SubTilesGrid>':
                    in_subtiles = True;  continue
                if line == '</SubTilesGrid>':
                    in_subtiles = False; continue
                m = _RE_ROAD_GUID.match(line)
                if m and p_guid is None:
                    p_guid = m.group(1); continue
                if in_origin_ws:
                    # Fallback: FileDBReader expands Point2D into <X>/<Y> sub-elements
                    m = _RE_POLY_XY.match(line)
                    if m:
                        tag = m.group(1).upper()
                        if tag == 'X': p_ox = m.group(2)
                        else:          p_oy = m.group(2)
                        continue
                if in_subtiles:
                    m = _RE_POLY_BITS.match(line)
                    if m:
                        p_bits = m.group(1); continue
                    m = _RE_POLY_XY.match(line)
                    if m:
                        tag = m.group(1).lower()
                        if tag == 'y': p_rows = m.group(2)
                        else:          p_cols = m.group(2)   # <x> = bits per row
                        continue
                continue

            if not in_area_om:
                continue

            # ── GameObject wrapper (player-placed objects live here) ───────────
            # NOTE: AreaObjectManager also has a bare <objects> section that
            # contains world/background objects (GUIDs like 37676).  We must
            # specifically enter the <GameObject> → <Objects> path.
            if line == '<GameObject>':
                in_game_obj = True
                in_objects  = False
                none_depth  = 0
                continue
            if line == '</GameObject>':
                in_game_obj = False
                in_objects  = False
                none_depth  = 0
                continue

            if not in_game_obj:
                continue

            # ── Objects section (inside GameObject) ───────────────────────────
            if line in ('<Objects>', '<objects>'):
                in_objects = True
                none_depth = 0
                continue
            if line in ('</Objects>', '</objects>'):
                in_objects = False
                none_depth = 0
                continue

            if not in_objects:
                continue

            # ── Building <None> element parsing ───────────────────────────────
            if line == '<None>':
                none_depth += 1
                if none_depth == 1:
                    o_guid = o_pos = o_dir = o_state = None
                continue

            if line == '</None>':
                if none_depth == 1:
                    if o_guid and o_pos and len(o_pos) >= 24:
                        try:
                            floats   = _le_floats(o_pos)
                            dir_rad  = _le_float(o_dir[:8]) if o_dir else 0.0
                            state    = _le_uint32(o_state[:8]) if o_state else 0
                            guid     = _le_uint32(o_guid[:8])
                            if guid and len(floats) >= 3:
                                is_blueprint = bool(state & BLUEPRINT_MASK)
                                area_buildings[current_am_id].append(
                                    (guid, floats[0], floats[1], floats[2],
                                     dir_rad, is_blueprint)
                                )
                        except Exception:
                            pass
                    o_guid = o_pos = o_dir = o_state = None
                none_depth = max(0, none_depth - 1)
                continue

            if none_depth != 1:
                continue

            m = _RE_FIELD.match(line)
            if m:
                fname, fval = m.group(1).lower(), m.group(2)
                if   fname == 'guid':      o_guid  = fval
                elif fname == 'position':  o_pos   = fval
                elif fname == 'direction': o_dir   = fval
                elif fname == 'statebits': o_state = fval

    # Drop player areas where we collected no buildings (AreaManager had no objects)
    area_buildings = {k: v for k, v in area_buildings.items() if v}
    return island_templates, area_buildings, area_road_edges, area_polygon_objects, _unknown_area_tags


# ── Island template matching ──────────────────────────────────────────────────

def _match_template(buildings: list, templates: list[dict]) -> Optional[dict]:
    """Return the template whose bounding box contains the most buildings."""
    if not buildings or not templates:
        return None
    best, best_score = None, -1
    for tmpl in templates:
        tx, tz, tw, th = tmpl['x'], tmpl['z'], tmpl['w'], tmpl['h']
        score = sum(
            1 for (_, wx, _wy, wz, *_rest) in buildings
            if tx <= wx <= tx + tw and tz <= wz <= tz + th
        )
        if score > best_score:
            best_score, best = score, tmpl
    return best


# ── Coordinate conversion ─────────────────────────────────────────────────────
#
# Anno 117 position format: [X, Z_elevation, Y_horizontal]
#   floats[0] = X  (first ground-plane axis → canvas col)
#   floats[1] = Z  (elevation, discarded)
#   floats[2] = Y  (second ground-plane axis → canvas row)
#
# Island template Position = two uint32 (X, Y) in the same ground-plane axes.
#
# Formula (NiHoel):
#   tl_x = island_X + island_W,  tl_z = island_Y + island_H
#   col  = tl_x - round(world_x + rotated_W / 2)
#   row  = tl_z - round(world_z + rotated_H / 2)
#   [world_z here = Y-axis = floats[2]]
#
# Island rotation: Rotation90 = N means the terrain is N×90° CCW from its
# .a7minfo default.  After computing col/row in the game (rotated) frame we
# apply N×90° CW as the inverse, so positions match the default-orientation
# terrain the canvas loads.
# ─────────────────────────────────────────────────────────────────────────────

def _convert_to_grid(buildings: list,
                     tmpl: dict,
                     data_manager,
                     diag_samples: Optional[list] = None) -> list[BuildingImport]:
    tl_x = tmpl['x'] + tmpl['w']
    tl_z = tmpl['z'] + tmpl['h']
    iw   = tmpl['w']      # island col-size (X direction) in game frame
    ih   = tmpl['h']      # island row-size (Y direction) in game frame
    rot90 = tmpl.get('rot', 0)   # 0-3 CW 90° steps
    out: list[BuildingImport] = []

    for (guid, world_x, _wy, world_z, dir_rad, is_blueprint) in buildings:
        guid = GUID_REMAP.get(guid, guid)
        bd = data_manager.get_building(guid) if data_manager else None
        if bd is None:
            continue

        # 8-step rotation gives 45° resolution (0,45,90,…,315)
        step8 = int(round(dir_rad / math.pi * 4)) % 8

        w = bd.width
        h = bd.height
        if step8 in (2, 6):   # 90° or 270° → swap w/h
            w, h = h, w

        # Diagonal buildings (45°/135°/225°/315°): anchor is the bounding-box NW corner
        # at (tl - world - 1.0) so the rendered diamond centre lands on the world centre.
        # Orthogonal buildings: NW corner = anchor − RotationCenter, where RotationCenter
        # is (x0, z0) from the buildBlocker data (offset from NW to the game's anchor).
        # For centred buildings x0=w/2, z0=h/2 (same result as the old formula).
        # For water/harbour buildings x0≈1 (anchor 1 tile from water-facing edge).
        # When the building is rotated (step8=2/4/6) the offset rotates with it.
        is_diag = (step8 % 2 == 1)
        bb = bd.build_blocker
        _dbg_rc_x = _dbg_rc_y = None

        if is_diag:
            # Bounding-box half-size in grid tiles: (nw+nh)*0.25.
            # Roads and road-like infra (Aqueduct, Drainage Channel) always use
            # nw=nh=2 (bbox_half=1.0) and bypass the buildBlocker formula so
            # their anchor matches the road-graph formula (tl_x - wx - 1.0).
            _road_like = ('Road' in bd.get_category_english() or
                          (bd.get_category_english() == 'Infrastructure Building' and
                           bd.get_name('english') in _ROAD_LIKE_INFRA_NAMES))
            if _road_like:
                _bbox_half = 1.0
            else:
                _snw = _snap_to_half_sqrt2_count(bd.width)
                _snh = _snap_to_half_sqrt2_count(bd.height)
                _bbox_half = (_snw + _snh) * 0.25
            if bb is not None and not _road_like:
                # Water/river building: anchor is the buildBlocker pivot.
                # Rotate the canonical anchor into the 45°-grid frame.
                # C = anchor-to-NW on the water/river-facing side (large)
                # D = anchor-to-NW on the land-facing side         (small)
                _bb_w = bd.width
                _bb_h = bd.height
                _x0   = bb['x0']
                _z0   = bb['z0']
                _sq2  = math.sqrt(2)
                if bd.river_building:
                    # Diagonal rc = vector-sum of the two adjacent orthogonal rc
                    # values divided by √2 (same derivation as harbour C/D).
                    # Orthogonal river rc: step8=0→(x0, bb_h-z0),
                    #   step8=2→(z0, x0), step8=4→(bb_w-x0, z0),
                    #   step8=6→(bb_h-z0, bb_w-x0).
                    _ra, _rb = _x0, _bb_h - _z0  # step8=0: (col, row)
                    _rc, _rd = _z0, _bb_w - _x0  # step8=2 col, step8=4 col
                    if step8 == 1:   # neighbours: step8=0 and step8=2
                        diag_rc_col, diag_rc_row = (_ra + _rc) / _sq2, (_rb + _ra) / _sq2
                    elif step8 == 3: # neighbours: step8=2 and step8=4
                        diag_rc_col, diag_rc_row = (_rc + _rd) / _sq2, (_ra + _rc) / _sq2
                    elif step8 == 5: # neighbours: step8=4 and step8=6
                        diag_rc_col, diag_rc_row = (_rd + _rb) / _sq2, (_rc + _rd) / _sq2
                    else:            # step8=7: neighbours step8=6 and step8=0
                        diag_rc_col, diag_rc_row = (_rb + _ra) / _sq2, (_rd + _rb) / _sq2
                else:
                    C = (_bb_w - _x0 + _z0) / _sq2
                    D = (_x0 + _z0) / _sq2
                    # step8=1→(C,C), step8=3→(D,C), step8=5→(D,D), step8=7→(C,D)
                    diag_rc_col = C if step8 in (1, 7) else D
                    diag_rc_row = C if step8 in (1, 3) else D
                _raw_col = tl_x - world_x - diag_rc_col
                _raw_row = tl_z - world_z - diag_rc_row
                if bd.river_building:
                    # Snap to quarter-diagonal grid (√2/4 per axis)
                    _q = _sq2 / 4
                    col: float = round(_raw_col / _q) * _q
                    row: float = round(_raw_row / _q) * _q
                else:
                    col: float = _raw_col
                    row: float = _raw_row
            else:
                col: float = tl_x - world_x - _bbox_half
                row: float = tl_z - world_z - _bbox_half
        else:
            if bb is not None:
                # bb_w/bb_h are the PRE-ROTATION dimensions.
                # w/h (already potentially swapped above) are the POST-ROTATION dimensions
                # used by the LR-flip and island-rotation steps below.
                bb_w = bd.width
                bb_h = bd.height
                x0   = bb['x0']
                z0   = bb['z0']
                if bd.river_building:
                    # River buildings: z0 is measured from the land-facing edge to the
                    # anchor (analogous to harbour x0 from the water-facing edge).
                    # The NW-corner offset toward river = bb_h - z0 (the complement).
                    # x0 is centred along the bank (symmetric), so rc = x0 directly.
                    if step8 == 0:
                        rc_x, rc_y = x0,           bb_h - z0
                    elif step8 == 2:
                        rc_x, rc_y = z0,           x0
                    elif step8 == 4:
                        rc_x, rc_y = bb_w - x0,   z0
                    elif step8 == 6:
                        rc_x, rc_y = bb_h - z0,   bb_w - x0
                    else:
                        rc_x, rc_y = w / 2,        h / 2
                elif bd.water_building:
                    # Harbour: x0 from water-facing (East at step8=0) edge → NW offset = bb_w-x0.
                    # z0 is symmetric (bb_h/2) for all known harbour buildings, so z0=bb_h-z0.
                    if step8 == 0:
                        rc_x, rc_y = bb_w - x0,   z0
                    elif step8 == 2:
                        rc_x, rc_y = z0,           bb_w - x0
                    elif step8 == 4:
                        rc_x, rc_y = x0,           bb_h - z0
                    elif step8 == 6:
                        rc_x, rc_y = bb_h - z0,   x0
                    else:
                        rc_x, rc_y = w / 2,        h / 2
                else:
                    # Regular land buildings: game stores x0 from East edge, z0 from South edge.
                    # NW-corner offsets: rc_x = bb_w-x0 (from West), rc_y = bb_h-z0 (from North).
                    # For symmetric buildings (x0=bb_w/2, z0=bb_h/2) this equals the center.
                    if step8 == 0:
                        rc_x, rc_y = bb_w - x0,   bb_h - z0
                    elif step8 == 2:
                        rc_x, rc_y = bb_h - z0,   bb_w - x0
                    elif step8 == 4:
                        rc_x, rc_y = x0,           z0
                    elif step8 == 6:
                        rc_x, rc_y = z0,           x0
                    else:
                        rc_x, rc_y = w / 2,        h / 2
                _dbg_rc_x, _dbg_rc_y = rc_x, rc_y
                col = int(tl_x - round(world_x + rc_x))
                row = int(tl_z - round(world_z + rc_y))
            else:
                col = int(tl_x - round(world_x + w / 2))
                row = int(tl_z - round(world_z + h / 2))

        # Un-rotate from game frame to default .a7minfo frame (inverse of island rotation).
        # For orthogonal buildings: NW corner offset = W or H (the full dimension extent).
        # For diagonal buildings: the bounding-box extent = 2*bbox_half (not bd.width/height).
        _ext_w = 2 * _bbox_half if is_diag else w
        _ext_h = 2 * _bbox_half if is_diag else h
        if rot90 == 1:          # 90°CCW terrain → apply 90°CW
            col, row = ih - row - _ext_h, col
            step8 = (step8 + 2) % 8
        elif rot90 == 2:        # 180°
            col, row = iw - col - _ext_w, ih - row - _ext_h
            step8 = (step8 + 4) % 8
        elif rot90 == 3:        # 90°CCW
            col, row = row, iw - col - _ext_w
            step8 = (step8 + 6) % 8

        # Left-right mirror: the savegame X axis is inverted relative to the island
        # image (col=0 = east edge in game frame = right in the island image).
        # After un-rotation, col dimension = iw for rot90∈{0,2}, ih for rot90∈{1,3}.
        # For a W-wide orthogonal building at col, the new NW is (col_size - col - ext).
        _col_size: int = iw if rot90 in (0, 2) else ih
        if is_diag:
            col = _col_size - col - 2 * _bbox_half
        else:
            _col_ext: int = w if rot90 in (0, 2) else h
            col = _col_size - col - _col_ext
        # Mirror direction for left-right flip: E↔W, NE↔NW, SE↔SW (N and S unchanged)
        step8 = (8 - step8) % 8

        # Mountain buildings are placed at 45° angles in the game; snap their
        # anchor to the 45° grid so internal cell lines align with canvas grid.
        if is_diag and bd.mountain_building:
            _nw, _nh = _get_45_grid_counts(bd, step8 * 45)
            col, row = _snap_45_anchor(col, row, _nw, _nh)

        if bd.water_building and diag_samples is not None:
            raw_step8 = int(round(dir_rad / math.pi * 4)) % 8
            diag_samples.append(
                f"  WATER guid={guid} name={bd.get_name('english')!r}"
                f" w={bd.width} h={bd.height} raw_step8={raw_step8}"
                f" world_z={world_z:.2f} tl_z={tl_z} (Δ={world_z - tl_z:.2f})"
                f" rc_x={_dbg_rc_x} rc_y={_dbg_rc_y}"
                f" → col={col} row={row} (island_h={ih})"
            )

        if bd.river_building and diag_samples is not None:
            raw_step8 = int(round(dir_rad / math.pi * 4)) % 8
            diag_samples.append(
                f"  RIVER guid={guid} name={bd.get_name('english')!r}"
                f" w={bd.width} h={bd.height} raw_step8={raw_step8}"
                f" world_x={world_x:.2f} world_z={world_z:.2f}"
                f" tl_x={tl_x} tl_z={tl_z}"
                f" rc_x={_dbg_rc_x} rc_y={_dbg_rc_y}"
                f" → col={col} row={row} (island_h={ih})"
            )

        if diag_samples is not None and 'Road' not in bd.get_category_english():
            raw_step8 = int(round(dir_rad / math.pi * 4)) % 8
            diag_samples.append(
                f"  BLDG guid={guid} name={bd.get_name('english')!r}"
                f" cat={bd.get_category_english()!r}"
                f" w={bd.width} h={bd.height} raw_step8={raw_step8} final_step8={step8}"
                f" world_x={world_x:.2f} world_z={world_z:.2f}"
                f" rc_x={_dbg_rc_x} rc_y={_dbg_rc_y}"
                f" → col={col} row={row}"
            )

        out.append(BuildingImport(
            guid=guid, col=col, row=row,
            direction=step8 * 45,
            is_blueprint=is_blueprint,
        ))
    return out


def _road_tiles_between(col1: int, row1: int,
                        col2: int, row2: int) -> list[tuple[int, int]]:
    """Rasterize a road edge segment into individual 1×1 tile positions."""
    if col1 == col2 and row1 == row2:
        return [(col1, row1)]
    tiles: list[tuple[int, int]] = []
    dc = abs(col2 - col1)
    dr = abs(row2 - row1)
    if dc >= dr:
        step = 1 if col2 >= col1 else -1
        for c in range(col1, col2 + step, step):
            t = (c - col1) / (col2 - col1) if col1 != col2 else 0.0
            r = round(row1 + t * (row2 - row1))
            tiles.append((c, r))
    else:
        step = 1 if row2 >= row1 else -1
        for r in range(row1, row2 + step, step):
            t = (r - row1) / (row2 - row1) if row1 != row2 else 0.0
            c = round(col1 + t * (col2 - col1))
            tiles.append((c, r))
    return tiles


def _build_footprint_set(building_imports: list[BuildingImport],
                         data_manager) -> set[tuple[int, int]]:
    """Return set of (col, row) occupied by all orthogonal buildings."""
    occupied: set[tuple[int, int]] = set()
    if not building_imports or data_manager is None:
        return occupied
    for b in building_imports:
        if b.direction % 90 != 0:
            continue  # diagonal buildings (aqueduct arches etc.) coexist with roads
        bd = data_manager.get_building(b.guid)
        if bd is None:
            continue
        rot = b.direction
        w = bd.width  if rot in (0, 180) else bd.height
        h = bd.height if rot in (0, 180) else bd.width
        for dc in range(w):
            for dr in range(h):
                occupied.add((int(b.col) + dc, int(b.row) + dr))
    return occupied


def _convert_roads_to_grid(road_edges: list,
                            tmpl: dict,
                            data_manager=None,
                            building_imports: Optional[list] = None,
                            ) -> list[BuildingImport]:
    """Convert road-graph edges (street/aqueduct/canal/hedge/wall) to BuildingImport tiles.

    Only 1×1 tiles are placed; large buildings appearing in the graph are skipped
    (they come from AreaObjectManager).  Diagonal edges produce 45°-grid tiles;
    orthogonal road tiles that fall inside a building footprint are suppressed.
    """
    tl_x  = tmpl['x'] + tmpl['w']
    tl_z  = tmpl['z'] + tmpl['h']
    iw    = tmpl['w']
    ih    = tmpl['h']
    rot90 = tmpl.get('rot', 0)

    # Build set of grid cells occupied by buildings to filter road tile overlap
    occupied = _build_footprint_set(building_imports or [], data_manager)

    seen: set = set()
    out:  list[BuildingImport] = []

    for (guid, wx1, wz1, wx2, wz2) in road_edges:
        guid = GUID_REMAP.get(guid, guid)
        if data_manager is not None:
            bd = data_manager.get_building(guid)
            if bd is None or bd.width != 1 or bd.height != 1:
                continue

        # Determine edge orientation from raw (×2) integer values to avoid float error
        raw_dx = abs(round(wx2 * 2) - round(wx1 * 2))
        raw_dz = abs(round(wz2 * 2) - round(wz1 * 2))
        is_diagonal = (raw_dx == raw_dz and raw_dx > 0)

        if is_diagonal:
            # Diagonal edge: each step is 1 unit in both x and z.
            # Tile centre in world: (wx1 + k*sx, wz1 + k*sz) for k=0..n
            # Canvas anchor for 45°-road (nw=nh=2, bbox=1 tile):
            #   grid_x = tl_x - wx - 1.0,  grid_y = tl_z - wz - 1.0
            n = int(raw_dx // 2)  # number of tile steps (raw units are ×2)
            sx = 1.0 if wx2 >= wx1 else -1.0
            sz = 1.0 if wz2 >= wz1 else -1.0
            _col_size_d: int = iw if rot90 in (0, 2) else ih
            for k in range(n + 1):
                wx = wx1 + k * sx
                wz = wz1 + k * sz
                gx = tl_x - wx - 1.0
                gy = tl_z - wz - 1.0
                if rot90 == 1:
                    gx, gy = ih - 1 - gy, gx
                elif rot90 == 2:
                    gx, gy = iw - 1 - gx, ih - 1 - gy
                elif rot90 == 3:
                    gx, gy = gy, iw - 1 - gx
                # Left-right mirror (see _convert_to_grid for explanation)
                gx = _col_size_d - 1.0 - gx
                key = (gx, gy)
                if key in seen:
                    continue
                seen.add(key)
                out.append(BuildingImport(
                    guid=guid, col=gx, row=gy,
                    direction=45, is_blueprint=False,
                ))
        else:
            # Orthogonal edge: rasterize with Bresenham into integer grid cells.
            col1 = int(tl_x - round(wx1 + 0.5))
            row1 = int(tl_z - round(wz1 + 0.5))
            col2 = int(tl_x - round(wx2 + 0.5))
            row2 = int(tl_z - round(wz2 + 0.5))
            _col_size_o: int = iw if rot90 in (0, 2) else ih

            for (col, row) in _road_tiles_between(col1, row1, col2, row2):
                if rot90 == 1:
                    col, row = ih - 1 - row, col
                elif rot90 == 2:
                    col, row = iw - 1 - col, ih - 1 - row
                elif rot90 == 3:
                    col, row = row, iw - 1 - col
                # Left-right mirror
                col = _col_size_o - 1 - col
                if (col, row) in occupied:
                    continue  # suppress road tiles inside building footprints
                key = (col, row)
                if key in seen:
                    continue
                seen.add(key)
                out.append(BuildingImport(
                    guid=guid, col=col, row=row,
                    direction=0, is_blueprint=False,
                ))
    return out


def _transform_nibble(n: int, rot90: int) -> int:
    """Remap nibble direction bits (T/R/B/L = N/E/S/W) to canvas directions.

    The parser always applies rot90 + LR mirror when placing island tiles.
    Nibble bits: bit3=T, bit2=R, bit1=B, bit0=L (world N/E/S/W).
    After the combined transform the canvas directions differ:
      rot90=0 + LR: East↔West  → swap bit0↔bit2
      rot90=1 + LR: N→E,E→S,S→W,W→N → 90° CW bit rotation
      rot90=2 + LR: North↔South → swap bit1↔bit3
      rot90=3 + LR: N↔E, S↔W   → swap adjacent bit pairs
    """
    if rot90 == 0:
        return (n & 0xA) | ((n & 0x1) << 2) | ((n & 0x4) >> 2)
    if rot90 == 1:
        return ((n >> 1) | (n << 3)) & 0xF
    if rot90 == 2:
        return (n & 0x5) | ((n & 0x8) >> 2) | ((n & 0x2) << 2)
    # rot90 == 3
    return ((n & 0xA) >> 1) | ((n & 0x5) << 1)


def _convert_polygon_tiles(polygons: list,
                            tmpl: dict,
                            data_manager=None,
                            bldg_imports: list = None) -> list[BuildingImport]:
    """Convert SubTilesGrid farm-field polygons to 1×1 BuildingImport tiles.

    Each non-zero nibble in the bit grid represents one farm-field sub-tile.
    Nibbles are packed low-first: byte 0xF3 → nibble[0]=3, nibble[1]=F.
    World position: (origin_x + nibble_col + 0.5, origin_y + row + 0.5).
    Canvas formula: col = tl_x - (origin_x + nibble_col + 1).
    cols (from <x>/4) limits how many nibbles per row are actual data vs padding.

    Each polygon entry comes from ONE farm's field polygon.  Tiles from adjacent
    farms can share a boundary cell with complementary nibble bitmasks.  We emit
    them as SEPARATE BuildingImport objects (no OR-merge) so each portion can
    carry its own parent_id and be coloured independently.

    When bldg_imports is provided, each polygon entry is matched to the nearest
    farm building (via module_guid) and tagged with that building's grid position
    in parent_col/parent_row for later parent_id assignment.
    """
    tl_x  = tmpl['x'] + tmpl['w']
    tl_z  = tmpl['z'] + tmpl['h']
    iw    = tmpl['w']
    ih    = tmpl['h']
    rot90 = tmpl.get('rot', 0)

    # Build lookup: module_guid → list of (col, row) for farm buildings in bldg_imports.
    # Includes additional_module_guid so secondary field types are also matched.
    farm_by_module_guid: dict[int, list] = {}
    if bldg_imports and data_manager:
        for _b in bldg_imports:
            if _b.nibble:
                continue
            _bbd = data_manager.get_building(_b.guid)
            if _bbd:
                if _bbd.module_guid:
                    farm_by_module_guid.setdefault(_bbd.module_guid, []).append((_b.col, _b.row))
                if _bbd.additional_module_guid:
                    farm_by_module_guid.setdefault(_bbd.additional_module_guid, []).append((_b.col, _b.row))

    out: list[BuildingImport] = []

    for entry in polygons:
        guid, origin_x, origin_y, bits_bytes, rows = entry[0], entry[1], entry[2], entry[3], entry[4]
        cols = entry[5] if len(entry) > 5 else 0   # nibble columns (0 = use full stride)
        if not bits_bytes or rows <= 0:
            continue
        # Skip road-like polygon entries — they produce junction artifacts where
        # the 90° and 45° grids meet.  Farm-field GUIDs may not be in the DB at
        # all (handled below), so a missing lookup means keep the entry.
        if data_manager:
            _bd = data_manager.get_building(guid)
            if _bd is not None:
                _cat = _bd.get_category_english()
                if ('Road' in _cat or
                        (_cat == 'Infrastructure Building' and
                         _bd.get_name('english') in _ROAD_LIKE_INFRA_NAMES)):
                    continue
        # Don't require a database entry for polygon tiles — field tile GUIDs
        # may not be catalogued as buildings, but the tile still needs placing.

        # Unpack bytes into nibbles (low nibble first, then high — matches C# ToNibbles())
        nibbles: list[int] = []
        for b in bits_bytes:
            nibbles.append(b & 0x0F)
            nibbles.append((b >> 4) & 0x0F)

        n_nibbles = len(nibbles)
        stride = n_nibbles // rows      # nibble stride per row (may include padding)
        if stride <= 0:
            continue
        n_cols = cols if cols > 0 else stride   # actual data columns per row

        # Compute all grid positions for this polygon entry (one farm's contribution).
        entry_tiles: list[tuple] = []   # (col, row, transformed_nibble)
        for row_idx in range(rows):
            for col_idx in range(n_cols):
                if col_idx >= stride:
                    break
                nibble = nibbles[row_idx * stride + col_idx]
                if nibble == 0:
                    continue

                col = tl_x - (origin_x + col_idx + 1)
                row = tl_z - (origin_y + row_idx  + 1)

                if rot90 == 1:
                    col, row = ih - 1 - row, col
                elif rot90 == 2:
                    col, row = iw - 1 - col, ih - 1 - row
                elif rot90 == 3:
                    col, row = row, iw - 1 - col
                # Left-right mirror
                col = (iw if rot90 in (0, 2) else ih) - 1 - col

                entry_tiles.append((col, row, _transform_nibble(nibble, rot90)))

        if not entry_tiles:
            continue

        # Match this polygon entry to the nearest farm building by centroid distance.
        # All nibble tiles from one entry belong to ONE farm, so we match at entry
        # level rather than per-tile to get correct boundaries.
        parent_col: Optional[float] = None
        parent_row: Optional[float] = None
        farms = farm_by_module_guid.get(guid, [])
        if farms:
            c_col = sum(t[0] for t in entry_tiles) / len(entry_tiles)
            c_row = sum(t[1] for t in entry_tiles) / len(entry_tiles)
            nearest = min(farms, key=lambda f: abs(f[0] - c_col) + abs(f[1] - c_row))
            parent_col, parent_row = nearest

        # Emit one BuildingImport per nibble tile — NO OR-merge with other entries.
        # Tiles at a shared boundary between two farms get two separate objects with
        # complementary nibble bitmasks and different parent_col/parent_row values.
        for (col, row, transformed) in entry_tiles:
            out.append(BuildingImport(
                guid=guid, col=col, row=row,
                direction=0, is_blueprint=False,
                nibble=transformed,
                parent_col=parent_col, parent_row=parent_row,
            ))
    return out


# ── Main entry point ──────────────────────────────────────────────────────────

def parse_savegame(a8s_path: Path,
                   tool_paths: dict,
                   data_manager,
                   progress_cb=None) -> list[IslandImport]:
    """
    Parse an Anno 117 .a8s savegame file.

    Parameters
    ----------
    a8s_path     : Path to the .a8s savegame file.
    tool_paths   : Dict with 'RdaConsole' and 'FileDBReader' Path values.
    data_manager : DataManager instance for building dimension lookups.
    progress_cb  : Optional callable(str) for UI progress messages.

    Returns
    -------
    List of IslandImport, one per player-owned island.

    Raises
    ------
    ParseError on hard failures.
    """
    rda_exe = Path(tool_paths['RdaConsole'])
    fdb_exe = Path(tool_paths['FileDBReader'])

    def _progress(msg: str):
        if progress_cb:
            progress_cb(msg)

    work_dir = Path(tempfile.mkdtemp(prefix='sg_parse_'))
    try:
        # Step 1 – extract .a8s archive
        _progress("Extracting savegame archive…")
        outer_dir = work_dir / 'outer'
        _rda_extract(rda_exe, a8s_path, outer_dir)

        # Step 2 – locate data.a7s (still named .a7s inside the .a8s)
        a7s_file = _find_file(outer_dir, ('data.a7s', '*.a7s'))
        if a7s_file is None:
            raise ParseError(
                f"No .a7s file found in savegame archive.\n"
                f"Extracted: {_list_files(outer_dir)}"
            )

        # Step 3 – zlib-decompress data.a7s → raw FileDB bytes
        _progress(f"Decompressing {a7s_file.name}…")
        raw_fdb = work_dir / 'data_raw.bin'
        _zlib_decompress(a7s_file, raw_fdb)

        # Step 4 – FileDBReader: raw FileDB → outer XML
        _progress("Decoding outer FileDB (may take a minute)…")
        outer_xml = _filedb_to_xml(fdb_exe, raw_fdb, timeout=300)

        # Step 5 – extract ALL session BinaryData blobs (one per region: Latium, Albion…)
        session_bins = _extract_all_session_binaries(outer_xml, work_dir, _progress)
        if not session_bins:
            raise ParseError(
                "No BinaryData found under GameSessions in outer XML.\n"
                "The savegame may be in an unsupported format."
            )

        # Step 6 & 7 – for each session: FileDBReader → XML → parse
        island_templates:     list[dict]     = []
        area_buildings:       dict[int, list] = {}
        area_road_edges:      dict[int, list] = {}
        area_polygon_objects: dict[int, list] = {}
        all_unknown_area_tags: set[str]        = set()

        for i, session_bin in enumerate(session_bins):
            _progress(f"Decoding session {i + 1}/{len(session_bins)} FileDB…")
            session_xml = _filedb_to_xml(fdb_exe, session_bin, timeout=600)
            _progress(f"Parsing session {i + 1}/{len(session_bins)}…")
            tmplts, area_blds, area_roads, area_polys, unk_tags = _parse_session_xml(session_xml, None)
            island_templates.extend(tmplts)
            area_buildings.update(area_blds)
            area_road_edges.update(area_roads)
            area_polygon_objects.update(area_polys)
            all_unknown_area_tags.update(unk_tags)

        if not island_templates:
            raise ParseError(
                "No island templates found in session XML.\n"
                "The savegame may be corrupt or in an unsupported version."
            )

        if not area_buildings:
            raise ParseError(
                "No player-owned islands found (OwnerProfile == 41 not present in AreaInfo).\n"
                "Ensure you select a savegame where you own at least one island."
            )

        # Step 8 – match areas to island templates, convert to grid coordinates
        _progress("Building island layout data…")
        results: list[IslandImport] = []
        diag_per_island: dict[str, list[str]] = {}

        for area_id, blist in area_buildings.items():
            if not blist:
                continue

            tmpl = _match_template(blist, island_templates)
            if tmpl is None:
                continue

            samples: list[str] = []
            bldg_imports = _convert_to_grid(blist, tmpl, data_manager, samples)
            if not bldg_imports:
                continue

            road_imports = _convert_roads_to_grid(
                area_road_edges.get(area_id, []), tmpl, data_manager,
                building_imports=bldg_imports,
            )

            poly_imports = _convert_polygon_tiles(
                area_polygon_objects.get(area_id, []), tmpl, data_manager,
                bldg_imports=bldg_imports,
            )

            diag_per_island[tmpl['name']] = samples

            # Remove isolated stray nibble tiles (no 4-connected nibble neighbour).
            if poly_imports:
                _dirs = ((0, -1), (1, 0), (0, 1), (-1, 0))
                surv_pos = {(round(p.col), round(p.row)) for p in poly_imports}
                poly_imports = [
                    p for p in poly_imports
                    if any((round(p.col)+dc, round(p.row)+dr) in surv_pos
                           for dc, dr in _dirs)
                ]

            results.append(IslandImport(
                name=tmpl['name'],
                island_key=tmpl['name'],
                region='Unknown',
                session_id=area_id,
                world_x=float(tmpl['x']),
                world_z=float(tmpl['z']),
                rotation90=tmpl.get('rot', 0),
                buildings=bldg_imports + road_imports + poly_imports,
            ))

        # Write diagnostics to %TEMP% for debugging
        try:
            dbg: list[str] = [
                f"=== Anno 117 Import Diagnostics ===",
                f"File: {a8s_path}",
                f"Sessions processed: {len(session_bins)}",
                f"Island templates found: {len(island_templates)}",
                f"  {[t['name'] for t in island_templates]}",
                f"",
                f"Island template details (x=worldX, z=worldY, w=colSize, h=rowSize, rot=Rotation90):",
            ]
            for t in island_templates:
                dbg.append(f"  {t['name']}: x={t['x']} z={t['z']} w={t['w']} h={t['h']} rot={t.get('rot',0)}")
            dbg.append(f"")
            dbg.append(f"Player area IDs with buildings: {sorted(area_buildings.keys())}")
            for area_id, blist in area_buildings.items():
                freq: dict[int, int] = {}
                for b in blist:
                    freq[b[0]] = freq.get(b[0], 0) + 1
                top = sorted(freq.items(), key=lambda kv: -kv[1])[:8]
                road_cnt = len(area_road_edges.get(area_id, []))
                poly_cnt = len(area_polygon_objects.get(area_id, []))
                dbg.append(f"  area {area_id}: {len(blist)} bldgs, {road_cnt} road edges, {poly_cnt} polygons, top GUIDs={top}")
            dbg.append(f"")
            dbg.append(f"Road edge samples per area:")
            for area_id, edges in area_road_edges.items():
                tmpl_for_area = None
                for r in results:
                    if r.session_id == area_id:
                        # find matching template
                        for t in island_templates:
                            if t['name'] == r.island_key:
                                tmpl_for_area = t
                                break
                        break
                if tmpl_for_area is None:
                    continue
                _tl_x = tmpl_for_area['x'] + tmpl_for_area['w']
                _tl_z = tmpl_for_area['z'] + tmpl_for_area['h']
                _iw   = tmpl_for_area['w']
                _ih   = tmpl_for_area['h']
                _rot  = tmpl_for_area.get('rot', 0)
                dbg.append(f"  area {area_id} ({tmpl_for_area['name']}) tl=({_tl_x},{_tl_z}) rot={_rot}:")
                for (g, wx1, wz1, wx2, wz2) in edges[:5]:
                    c1 = int(_tl_x - round(wx1 + 0.5))
                    r1 = int(_tl_z - round(wz1 + 0.5))
                    c2 = int(_tl_x - round(wx2 + 0.5))
                    r2 = int(_tl_z - round(wz2 + 0.5))
                    raw_x1 = round(wx1 * 2); raw_y1 = round(wz1 * 2)
                    raw_x2 = round(wx2 * 2); raw_y2 = round(wz2 * 2)
                    parity = f"X1={'odd' if raw_x1%2 else 'even'} Y1={'odd' if raw_y1%2 else 'even'}"
                    dbg.append(
                        f"    guid={g} raw1=({raw_x1},{raw_y1}) raw2=({raw_x2},{raw_y2})"
                        f" {parity}"
                        f" world1=({wx1:.1f},{wz1:.1f}) world2=({wx2:.1f},{wz2:.1f})"
                        f" pre-rot: ({c1},{r1})→({c2},{r2})"
                    )
            dbg.append(f"")
            if all_unknown_area_tags:
                dbg.append(f"Unknown <Area*Manager> tags (check these for farm-field polygon tag):")
                for t in sorted(all_unknown_area_tags):
                    dbg.append(f"  {t}")
            else:
                dbg.append(f"Unknown <Area*Manager> tags: (none — all area managers are known types)")
            dbg.append(f"")
            dbg.append(f"Results: {len(results)} island(s)")
            for isl in results:
                known = sum(1 for b in isl.buildings)
                road_cnt = sum(1 for b in isl.buildings if not b.is_blueprint and b.guid in {23996, 24355, 19723, 19691, 19753})
                dbg.append(f"  {isl.island_key}: {known} total tiles placed, rotation90={isl.rotation90}")
                dbg.append(f"    (approx {road_cnt} road/aqueduct tiles)")
                for line in diag_per_island.get(isl.island_key, []):
                    dbg.append(line)
            import pathlib
            log = pathlib.Path(tempfile.gettempdir()) / 'anno117_import_debug.txt'
            log.write_text('\n'.join(dbg), encoding='utf-8')
        except Exception:
            pass

        _progress(f"Done — {len(results)} player island(s) found.")
        return results

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
