"""
Anno 117 Layout Tool - Data Manager
Loads and provides access to building-data.json and construction-menu.json
"""
import json
import os
import math
from typing import Optional
from config import resource_path, REGION_DISPLAY, get_category_color


ATTRIBUTE_ICONS: dict = {
    'Belief':       'data/ui/fhd/base/icon_content/religion/icon_2d_religion.png',
    'FireSafety':   'data/ui/fhd/base/icon_content/attributes/icon_2d_fire_safety.png',
    'Happiness':    'data/ui/fhd/base/icon_content/attributes/icon_2d_happiness.png',
    'Health':       'data/ui/fhd/base/icon_content/attributes/icon_2d_health.png',
    'Knowledge':    'data/ui/fhd/base/icon_content/attributes/icon_2d_techtree_knowledge.png',
    'Money':        'data/ui/fhd/base/icon_content/attributes/icon_2d_income.png',
    'Population':   'data/ui/fhd/base/icon_content/attributes/icon_2d_population.png',
    'Prestige':     'data/ui/fhd/base/icon_content/attributes/icon_2d_prestige.png',
    'Productivity': 'data/ui/fhd/base/icon_content/generic/icon_2d_productivity.png',
    'Maintenance':  'data/ui/fhd/base/icon_content/generic/icon_2d_consumption.png',
}

ATTRIBUTE_DISPLAY_NAMES: dict = {
    'FireSafety': 'Fire Safety',
}

ITEM_RARITY_ORDER: dict = {
    'Common': 0, 'Rare': 1, 'Epic': 2, 'Legendary': 3, 'Unique': 4,
}


class BuildingData:
    """Represents a single building definition from building-data.json."""
    __slots__ = (
        'guid', 'name', 'building_category', 'associated_regions',
        'width', 'height', 'icon_path',
        'construction_costs', 'maintenance_costs',
        'module_guid', 'module_limit', 'module_build_radius',
        'additional_module_guid', 'radius', 'free_area_productivity',
        'upgrade_guid', 'functional_effects', 'public_service_effect',
    )

    def __init__(self, raw: dict):
        self.guid: int = raw['guid']
        self.name: dict = raw.get('name', {})
        self.building_category: dict = raw.get('buildingCategory', {})
        self.associated_regions: list = raw.get('associatedRegions') or []
        self.width: int = raw.get('width', 1)
        self.height: int = raw.get('height', 1)
        self.icon_path: str = raw.get('iconPath', '')
        self.construction_costs: list = raw.get('constructionCosts', [])
        self.maintenance_costs: list = raw.get('maintenanceCosts', [])
        self.module_guid: Optional[int] = raw.get('moduleGUID')
        self.module_limit: Optional[int] = raw.get('moduleLimit')
        self.module_build_radius: Optional[int] = raw.get('moduleBuildRadius')
        self.additional_module_guid: Optional[int] = raw.get('additionalModuleGUID')
        self.radius: Optional[dict] = raw.get('radius')
        self.free_area_productivity: Optional[dict] = raw.get('freeAreaProductivity')
        self.upgrade_guid: Optional[int] = raw.get('upgradeGUID')
        self.functional_effects: list = raw.get('functionalEffects') or []
        self.public_service_effect: Optional[int] = raw.get('publicServiceEffect')

    def get_name(self, lang: str = 'english') -> str:
        if isinstance(self.name, dict):
            return self.name.get(lang) or self.name.get('english', f'GUID {self.guid}')
        return str(self.name)

    def get_category(self, lang: str = 'english') -> str:
        if isinstance(self.building_category, dict):
            return self.building_category.get(lang) or self.building_category.get('english', '')
        return str(self.building_category)

    def get_category_english(self) -> str:
        if isinstance(self.building_category, dict):
            return self.building_category.get('english', '')
        return str(self.building_category)

    def is_placeable(self) -> bool:
        """False for product/placeholder entries that have no building category."""
        cat = self.get_category_english()
        return bool(cat) and cat != 'None'

    def is_ornament(self) -> bool:
        return 'ornament' in self.get_category_english().lower()

    def get_rotated_size(self, rotation: int):
        """
        Return (w, h) tile footprint for the given rotation.
        Rotation 0/180 -> (width, height)
        Rotation 90/270 -> (height, width)
        Rotation 45/135/225/315 -> diamond footprint in 45-grid tiles
        """
        rot = rotation % 360
        if rot in (0, 180):
            return (self.width, self.height)
        elif rot in (90, 270):
            return (self.height, self.width)
        else:
            # 45° rotated: adjust side lengths to nearest 0.5 step of √2
            w45 = _snap_to_half_sqrt2(self.width)
            h45 = _snap_to_half_sqrt2(self.height)
            return (w45, h45)

    def get_45deg_grid_size(self):
        """Return the grid size in 45° sub-grid tiles when rotated 45°."""
        w45 = _snap_to_half_sqrt2(self.width)
        h45 = _snap_to_half_sqrt2(self.height)
        return (w45, h45)


def _snap_to_half_sqrt2(n: float) -> float:
    """
    Snap a tile count n to the nearest 0.5 × √2 multiple.
    Returns the snapped value (in normal grid tile units).
    """
    sqrt2 = math.sqrt(2)
    unit = 0.5 * sqrt2          # ≈ 0.7071
    steps = round(n / unit)     # nearest integer number of half-√2 steps
    steps = max(1, steps)
    return steps * unit         # value in normal grid tile units


def _snap_to_half_sqrt2_count(n: float) -> int:
    """Return the number of 45°-grid tile units (each of size unit = 0.5√2)."""
    sqrt2 = math.sqrt(2)
    unit = 0.5 * sqrt2
    steps = round(n / unit)
    return max(1, steps)


def _get_45_grid_counts(bd: 'BuildingData', rotation: int) -> tuple:
    """
    Return (nw, nh) in 45°-grid tile units for a 45°-family rotation.
    Roads are always 2×2 in the 45° grid (special game rule).
    For other buildings: 45°/225° uses (width, height); 135°/315° swaps them.
    """
    if 'Road' in bd.get_category_english():
        return (2, 2)
    rot = rotation % 360
    if rot in (45, 225):
        return (_snap_to_half_sqrt2_count(bd.width),
                _snap_to_half_sqrt2_count(bd.height))
    else:
        return (_snap_to_half_sqrt2_count(bd.height),
                _snap_to_half_sqrt2_count(bd.width))


class ProductData:
    """Represents a single product entry from product-data.json."""
    __slots__ = ('guid', 'name', 'icon_path', 'is_workforce')

    def __init__(self, raw: dict):
        self.guid: int = raw['guid']
        self.name: dict = raw.get('name', {})
        self.icon_path: str = raw.get('icon', '')
        self.is_workforce: bool = bool(raw.get('isWorkforce', False))

    def get_name(self, lang: str = 'english') -> str:
        if isinstance(self.name, dict):
            return self.name.get(lang) or self.name.get('english', f'#{self.guid}')
        return str(self.name)


class DataManager:
    """Loads and indexes building, construction menu and product data."""

    def __init__(self):
        self.buildings: dict[int, BuildingData] = {}
        self.construction_menu: dict = {}
        self.products: dict[int, ProductData] = {}
        self.effects: dict[str, dict] = {}         # GUID str -> effect dict
        self.buffs: dict[str, dict] = {}           # GUID str -> buff dict (tech)
        self.asset_pools: dict[str, dict] = {}     # GUID str -> pool dict
        self.needs: dict[str, dict] = {}           # GUID str -> need dict
        self.items: dict[str, dict] = {}           # GUID str -> item dict
        self.item_buffs: dict[str, dict] = {}      # GUID str -> buff dict (items)
        self._items_by_building: dict = {}         # building GUID -> [item dicts]
        self._loaded = False

        # User-customized colours (persisted in settings.json by the app)
        self.building_color_overrides: dict[int, str] = {}
        self.category_color_overrides: dict[str, str] = {}

    def load(self):
        if self._loaded:
            return
        self._load_buildings()
        self._load_construction_menu()
        self._load_products()
        self._load_effects()
        self._load_asset_pools()
        self._load_needs()
        self._load_items()
        self._apply_menu_overrides()
        self._loaded = True

    def _load_buildings(self):
        path = resource_path('data/building-data.json')
        if not os.path.exists(path):
            print(f"WARNING: building-data.json not found at {path}")
            return
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        for guid_str, data in raw.items():
            bd = BuildingData(data)
            self.buildings[bd.guid] = bd

    def _load_construction_menu(self):
        path = resource_path('data/construction-menu.json')
        if not os.path.exists(path):
            print(f"WARNING: construction-menu.json not found at {path}")
            return
        with open(path, encoding='utf-8') as f:
            self.construction_menu = json.load(f)

    def _apply_menu_overrides(self):
        """Inject tool-specific menu entries not present in the generated JSON."""
        overrides = [
            # (region, guid_to_insert)
            ('Roman',  3402),   # Trading Post → Roman  Harbour Buildings
            ('Celtic', 7037),   # Trading Post → Celtic Harbour Buildings
        ]
        for region, guid in overrides:
            try:
                infra = self.construction_menu.get(region, {}).get('infrastructure', {})
                for cat in infra.get('items', []):
                    if (cat.get('type') == 'category'
                            and isinstance(cat.get('name'), dict)
                            and cat['name'].get('english') == 'Harbour Buildings'):
                        items = cat.setdefault('items', [])
                        if not any(x.get('guid') == guid for x in items):
                            bd = self.buildings.get(guid)
                            entry: dict = {'type': 'building', 'guid': guid}
                            if bd:
                                entry['name'] = bd.name
                            items.insert(0, entry)
                        break
            except (KeyError, TypeError, AttributeError):
                pass

        # Tier residences: the game only lists the basic residence under Infrastructure, not under its own tier tab. Show it as the first item of that tier's build list too, for quick access.
        tier_residences = {
            'Roman':  {'Liberti': 3087, 'Plebeians': 3141, 'Equites': 3142, 'Patricians': 3145},
            'Celtic': {'Waders': 6414, 'Smiths': 6471, 'Aldermen': 6472, 'Mercators': 6475, 'Nobles': 6514},
        }
        for region, by_tier in tier_residences.items():
            try:
                tiers = self.construction_menu.get(region, {}).get('tiers', [])
                for tier in tiers:
                    tier_name = (tier.get('name') or {}).get('english')
                    guid = by_tier.get(tier_name)
                    if guid is None:
                        continue
                    items = tier.setdefault('items', [])
                    if not any(x.get('guid') == guid for x in items):
                        bd = self.buildings.get(guid)
                        entry: dict = {'type': 'building', 'guid': guid}
                        if bd:
                            entry['name'] = bd.name
                        items.insert(0, entry)
            except (KeyError, TypeError, AttributeError):
                pass

    def _load_products(self):
        path = resource_path('data/product-data.json')
        if not os.path.exists(path):
            print(f"WARNING: product-data.json not found at {path}")
            return
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        for guid_str, data in raw.items():
            pd = ProductData(data)
            self.products[pd.guid] = pd

    def _load_effects(self):
        path = resource_path('data/effect-data.json')
        if not os.path.exists(path):
            return
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        self.effects = raw.get('effects', {})
        self.buffs = raw.get('buffs', {})

    def _load_asset_pools(self):
        path = resource_path('data/asset-pool-data.json')
        if not os.path.exists(path):
            return
        with open(path, encoding='utf-8') as f:
            self.asset_pools = json.load(f)

    def _load_needs(self):
        path = resource_path('data/need-data.json')
        if not os.path.exists(path):
            return
        with open(path, encoding='utf-8') as f:
            self.needs = json.load(f)

    def _load_items(self):
        path = resource_path('data/item-data.json')
        if not os.path.exists(path):
            return
        with open(path, encoding='utf-8') as f:
            raw = json.load(f)
        self.item_buffs = {str(g): d for g, d in raw.get('buffs', {}).items()}
        self.items = {
            str(g): d for g, d in raw.get('items', {}).items()
            if any((d.get('name') or {}).values())
            and any((d.get('infoDescription') or {}).values())
        }
        by_building: dict = {}
        for item in self.items.values():
            # Targets may be direct building GUIDs or asset pool GUIDs; resolve both.
            for building_guid in self.resolve_targets(item.get('targets', [])):
                by_building.setdefault(building_guid, []).append(item)
        self._items_by_building = {
            g: sorted(items, key=lambda x: -ITEM_RARITY_ORDER.get(x.get('rarity', 'Common'), 0))
            for g, items in by_building.items()
        }

    def get_available_items(self, building_guid: int) -> list:
        """Return items applicable to this building, sorted rarest-first."""
        return self._items_by_building.get(building_guid, [])

    def _extract_buff_bonuses(self, buff_guids: list, icon_fallback: str) -> dict:
        """Extract attribute bonuses from a list of item buff GUIDs into a bonus dict.
        Keys are internal attr names; values have: total, icon, display, radius (bool).
        radius=True entries come from AdditionalFunctionalEffect and are per-building-in-range."""
        bonuses: dict = {}

        def _add(attr_key: str, amount: float, display: str, icon: str, *, radius: bool = False):
            if attr_key not in bonuses:
                bonuses[attr_key] = {
                    'total': 0.0,
                    'icon': ATTRIBUTE_ICONS.get(attr_key, icon),
                    'display': display,
                    'radius': radius,
                }
            bonuses[attr_key]['total'] += amount

        for buff_guid in buff_guids:
            buff = self.item_buffs.get(str(buff_guid))
            if not buff:
                continue
            props = buff.get('properties', {})
            icon = buff.get('icon', '') or icon_fallback

            # BuildingUpgrade.AdditionalAttributes — flat per-attribute bonus
            for attr, data in props.get('BuildingUpgrade', {}).get('AdditionalAttributes', {}).items():
                _add(attr, data.get('AmountOrPercent', 0),
                     ATTRIBUTE_DISPLAY_NAMES.get(attr, attr), icon)

            # BuildingUpgrade.AdditionalFunctionalEffect — adds a radius effect to the building.
            # Extract per-building bonuses from the referenced effect (stored in effect-data.json).
            add_fe = props.get('BuildingUpgrade', {}).get('AdditionalFunctionalEffect')
            if add_fe is not None:
                effect = self.effects.get(str(add_fe))
                if effect and effect.get('effectScope') in ('Radius', 'StreetDistance'):
                    for eff_bg in effect.get('buffs', []):
                        eff_buff = self.buffs.get(str(eff_bg))
                        if not eff_buff:
                            continue
                        eff_icon = eff_buff.get('icon', '') or icon
                        for attr, data in (eff_buff.get('properties', {})
                                                    .get('BuildingUpgrade', {})
                                                    .get('AdditionalAttributes', {}).items()):
                            _add(f'_radius_{attr}', data.get('AmountOrPercent', 0),
                                 ATTRIBUTE_DISPLAY_NAMES.get(attr, attr), eff_icon, radius=True)

            # ResidenceUpgrade.NeedProvidedNeedAttributes.AdditionalNeedAttributes
            need_attrs = (props.get('ResidenceUpgrade', {})
                               .get('NeedProvidedNeedAttributes', {})
                               .get('AdditionalNeedAttributes', {}))
            for attr, data in need_attrs.items():
                _add(attr, data.get('AmountOrPercent', 0),
                     ATTRIBUTE_DISPLAY_NAMES.get(attr, attr), icon)

            # FactoryUpgrade.ProductivityUpgrade — productivity bonus (flat or %)
            pu = props.get('FactoryUpgrade', {}).get('ProductivityUpgrade', {})
            if pu and pu.get('value') is not None:
                is_pct = pu.get('percental', False)
                key = 'ProductivityPct' if is_pct else 'Productivity'
                display = 'Productivity %' if is_pct else 'Productivity'
                if key not in bonuses:
                    bonuses[key] = {'total': 0.0,
                                    'icon': ATTRIBUTE_ICONS.get('Productivity', icon),
                                    'display': display, 'radius': False}
                bonuses[key]['total'] += pu['value']

            # MaintenanceUpgrade — maintenance cost modifier (usually %)
            for mu_key, mu_display in (('MaintenanceFactorUpgrade', 'Maintenance %'),
                                       ('WorkforceMaintenanceFactorUpgrade', 'Workforce Maint. %')):
                mu = props.get('MaintenanceUpgrade', {}).get(mu_key, {})
                if mu and mu.get('value') is not None:
                    is_pct = mu.get('percental', False)
                    key = mu_key + ('Pct' if is_pct else '')
                    disp = mu_display if is_pct else mu_display.replace(' %', '')
                    if key not in bonuses:
                        bonuses[key] = {'total': 0.0,
                                        'icon': ATTRIBUTE_ICONS.get('Maintenance', icon),
                                        'display': disp, 'radius': False}
                    bonuses[key]['total'] += mu['value']

        return bonuses

    # Bonus keys that are local to the building (maintenance/productivity) and must not
    # appear in the "Effect bonuses" summary; they are applied to the maintenance display instead.
    _ITEM_LOCAL_KEYS: frozenset = frozenset({
        'Productivity', 'ProductivityPct',
        'MaintenanceFactorUpgradePct', 'MaintenanceFactorUpgrade',
        'WorkforceMaintenanceFactorUpgradePct', 'WorkforceMaintenanceFactorUpgrade',
    })

    def compute_item_bonuses(self, building_guid: int, active_item_guids: set,
                             boosted_item_guids: set = None) -> list:
        """Compute flat attribute bonuses from active item effects for the effect-bonus display.
        Excludes radius bonuses (handled in compute_radius_bonuses) and local-only bonuses
        (maintenance/productivity, handled separately in the maintenance display).
        Returns list of dicts with keys: attr, total, icon."""
        if not active_item_guids:
            return []
        combined: dict = {}
        for item_guid in active_item_guids:
            item = self.items.get(str(item_guid))
            if not item:
                continue
            icon_fb = item.get('icon', '')
            use_boost = (boosted_item_guids and item_guid in boosted_item_guids
                         and item.get('boostBuffs'))
            buff_guids = item.get('boostBuffs') if use_boost else item.get('buffs', [])
            for attr, data in self._extract_buff_bonuses(buff_guids, icon_fb).items():
                if data.get('radius') or attr in self._ITEM_LOCAL_KEYS:
                    continue
                if attr not in combined:
                    combined[attr] = {'total': 0.0, 'icon': data['icon'], 'display': data['display']}
                combined[attr]['total'] += data['total']
        return [{'attr': b['display'], 'total': b['total'], 'icon': b['icon']}
                for b in combined.values()]

    def get_item_maintenance_modifiers(self, active_item_guids: set,
                                       boosted_item_guids: set = None) -> dict:
        """Return maintenance percentage modifiers from active items.
        Keys: 'maint_pct' (applies to non-workforce costs), 'workforce_pct' (workforce costs).
        Values are additive percentages, e.g. -50.0 means 50% cheaper."""
        maint_pct = 0.0
        workforce_pct = 0.0
        for item_guid in active_item_guids:
            item = self.items.get(str(item_guid))
            if not item:
                continue
            use_boost = (boosted_item_guids and item_guid in boosted_item_guids
                         and item.get('boostBuffs'))
            buff_guids = item.get('boostBuffs') if use_boost else item.get('buffs', [])
            for buff_guid in buff_guids:
                buff = self.item_buffs.get(str(buff_guid))
                if not buff:
                    continue
                mu = buff.get('properties', {}).get('MaintenanceUpgrade', {})
                mf = mu.get('MaintenanceFactorUpgrade', {})
                if mf and mf.get('value') is not None:
                    maint_pct += mf['value']
                wf = mu.get('WorkforceMaintenanceFactorUpgrade', {})
                if wf and wf.get('value') is not None:
                    workforce_pct += wf['value']
        return {'maint_pct': maint_pct, 'workforce_pct': workforce_pct}

    def get_item_effect_preview(self, item_guid: int) -> tuple:
        """Return (regular_bonuses, boost_bonuses) for a single item.
        Each list contains dicts with: attr, total, icon, radius (bool).
        boost_bonuses is None if the item has no boostBuffs.
        radius=True entries should be labelled 'per bldg. in radius' in the UI."""
        item = self.items.get(str(item_guid))
        if not item:
            return [], None
        icon_fb = item.get('icon', '')

        def _to_list(d: dict) -> list:
            return [{'attr': b['display'], 'total': b['total'],
                     'icon': b['icon'], 'radius': b.get('radius', False)}
                    for b in d.values()]

        reg = _to_list(self._extract_buff_bonuses(item.get('buffs', []), icon_fb))
        if not item.get('boostBuffs'):
            return reg, None
        boost = _to_list(self._extract_buff_bonuses(item.get('boostBuffs', []), icon_fb))
        return reg, boost

    def get_range_multiplier(self, building_guid: int, active_tech_guids: set) -> float:
        """Return the radius multiplier from active AreaBuff tech effects (1.0 = no change). RadiusEffectRangeUpgrade is stored as a percentage (e.g. 25.0 = +25%)."""
        if not active_tech_guids:
            return 1.0
        multiplier = 1.0
        for tech_guid in active_tech_guids:
            effect = self.effects.get(str(tech_guid))
            if not effect:
                continue
            for buff_guid in effect.get('buffs', []):
                buff = self.buffs.get(str(buff_guid))
                if not buff or buff.get('template') != 'AreaBuff':
                    continue
                upgrade_pct = (buff.get('properties', {})
                               .get('AreaBuff', {})
                               .get('RadiusEffectRangeUpgrade'))
                if upgrade_pct is not None:
                    multiplier += upgrade_pct / 100.0
        return multiplier

    def get_effect(self, guid: int) -> Optional[dict]:
        return self.effects.get(str(guid))

    def get_buff(self, guid: int) -> Optional[dict]:
        return self.buffs.get(str(guid))

    def resolve_targets(self, target_guids: list, _visited: set = None) -> set:
        """Recursively resolve a list of GUIDs to a flat set of building GUIDs. Asset pool GUIDs are expanded recursively; building GUIDs are returned as-is."""
        if _visited is None:
            _visited = set()
        result = set()
        for guid in target_guids:
            guid = int(guid)
            if guid in _visited:
                continue
            _visited.add(guid)
            if guid in self.buildings:
                result.add(guid)
            elif str(guid) in self.asset_pools:
                pool = self.asset_pools[str(guid)]
                result |= self.resolve_targets(pool.get('assets', []), _visited)
        return result

    def get_available_tech_effects(self, building_guid: int) -> list:
        """Return all tech effects whose target pool includes building_guid.
        Handles both regular effects (targets list) and AreaBuff effects (RadiusEffectRangeTarget in the buff, which is also an asset pool)."""
        result = []
        for effect in self.effects.values():
            if effect.get('sourceCategory') != 'Tech':
                continue
            raw_targets = effect.get('targets') or []
            if raw_targets:
                targets = self.resolve_targets(raw_targets)
            else:
                # AreaBuff: the applicable buildings are in the buff's RadiusEffectRangeTarget, which is an asset pool reference.
                targets = set()
                for buff_guid in effect.get('buffs', []):
                    buff = self.buffs.get(str(buff_guid))
                    if not buff or buff.get('template') != 'AreaBuff':
                        continue
                    rrt = (buff.get('properties', {})
                           .get('AreaBuff', {})
                           .get('RadiusEffectRangeTarget', []))
                    pool_guids = [x.get('Target') for x in rrt if x.get('Target')]
                    if pool_guids:
                        targets |= self.resolve_targets(pool_guids)
            if building_guid in targets:
                result.append(effect)
        return result

    def compute_radius_bonuses(self, building_guid: int, in_range_guids: list,
                               active_tech_guids: set = None,
                               active_item_guids: set = None,
                               boosted_item_guids: set = None) -> list:
        """Compute summed AdditionalAttributes bonuses from functional effects, activated tech
        effects, and activated item effects (AdditionalFunctionalEffect items).
        Returns list of dicts with keys: attr, total, icon."""
        bd = self.buildings.get(building_guid)
        if not bd:
            return []

        effect_guids = list(bd.functional_effects)

        if active_tech_guids:
            for tech_guid in active_tech_guids:
                tech_effect = self.effects.get(str(tech_guid))
                if not tech_effect:
                    continue
                for buff_guid in tech_effect.get('buffs', []):
                    buff = self.buffs.get(str(buff_guid))
                    if not buff:
                        continue
                    add_fe = (buff.get('properties', {})
                              .get('BuildingUpgrade', {})
                              .get('AdditionalFunctionalEffect'))
                    if add_fe is not None:
                        effect_guids.append(int(add_fe))

        if active_item_guids:
            for item_guid in active_item_guids:
                item = self.items.get(str(item_guid))
                if not item:
                    continue
                use_boost = (boosted_item_guids and item_guid in boosted_item_guids
                             and item.get('boostBuffs'))
                buff_guids = item.get('boostBuffs') if use_boost else item.get('buffs', [])
                for buff_guid in buff_guids:
                    buff = self.item_buffs.get(str(buff_guid))
                    if not buff:
                        continue
                    add_fe = (buff.get('properties', {})
                              .get('BuildingUpgrade', {})
                              .get('AdditionalFunctionalEffect'))
                    if add_fe is not None:
                        effect_guids.append(int(add_fe))

        bonuses = {}  # attr -> {total, icon, name}
        in_range_set = list(in_range_guids)

        for effect_guid in effect_guids:
            effect = self.effects.get(str(effect_guid))
            if not effect or effect.get('effectScope') not in ('Radius', 'StreetDistance'):
                continue
            target_set = self.resolve_targets(effect.get('targets', []))
            matching = sum(1 for g in in_range_set if g in target_set)
            if matching == 0:
                continue
            for buff_guid in effect.get('buffs', []):
                buff = self.buffs.get(str(buff_guid))
                if not buff:
                    continue
                attrs = (buff.get('properties', {})
                         .get('BuildingUpgrade', {})
                         .get('AdditionalAttributes', {}))
                icon = buff.get('icon', '') or effect.get('icon', '')
                for attr, data in attrs.items():
                    amount = data.get('AmountOrPercent', 0)
                    attr_icon = ATTRIBUTE_ICONS.get(attr, icon)
                    display = ATTRIBUTE_DISPLAY_NAMES.get(attr, attr)
                    if attr not in bonuses:
                        bonuses[attr] = {'total': 0.0, 'icon': attr_icon, 'display': display}
                    bonuses[attr]['total'] += matching * amount

        # Public service effect: grants a need to residences in range.
        # The need's needAttributes define the per-residence attribute bonus.
        if bd.public_service_effect:
            pse = self.effects.get(str(bd.public_service_effect))
            if pse and pse.get('effectScope') in ('Radius', 'StreetDistance'):
                pse_targets = self.resolve_targets(pse.get('targets', []))
                pse_matching = sum(1 for g in in_range_set if g in pse_targets)
                if pse_matching > 0:
                    for buff_guid in pse.get('buffs', []):
                        buff = self.buffs.get(str(buff_guid))
                        if not buff:
                            continue
                        need_upgrades = (buff.get('properties', {})
                                         .get('ResidenceUpgrade', {})
                                         .get('ProvidedNeedUpgrade', []))
                        for nu in need_upgrades:
                            need_guid = nu.get('ProvidedNeed')
                            if not need_guid:
                                continue
                            need = self.needs.get(str(need_guid))
                            if not need:
                                continue
                            for attr, amount in need.get('needAttributes', {}).items():
                                attr_icon = ATTRIBUTE_ICONS.get(attr, need.get('icon', ''))
                                display = ATTRIBUTE_DISPLAY_NAMES.get(attr, attr)
                                if attr not in bonuses:
                                    bonuses[attr] = {'total': 0.0, 'icon': attr_icon,
                                                     'display': display}
                                bonuses[attr]['total'] += pse_matching * amount

        return [{'attr': b['display'], 'total': b['total'], 'icon': b['icon']}
                for b in bonuses.values()]

    def get_product(self, guid: int) -> Optional[ProductData]:
        return self.products.get(guid)

    def get_product_name(self, guid: int, lang: str = 'english') -> str:
        pd = self.products.get(guid)
        if pd:
            return pd.get_name(lang)
        return f'#{guid}'

    def get_building(self, guid: int) -> Optional[BuildingData]:
        return self.buildings.get(guid)

    def get_building_name(self, guid: int, lang: str = 'english') -> str:
        bd = self.buildings.get(guid)
        if bd:
            return bd.get_name(lang)
        return f'GUID {guid}'

    def get_building_color(self, bd: BuildingData) -> str:
        """Resolve a building's fill colour: per-building override, then per-category override, then the built-in category default."""
        if bd.guid in self.building_color_overrides:
            return self.building_color_overrides[bd.guid]
        cat = bd.get_category_english()
        if cat in self.category_color_overrides:
            return self.category_color_overrides[cat]
        return get_category_color(cat)

    def set_building_color(self, guid: int, color: str,
                            apply_to_category: bool = False):
        """Set a custom colour for one building, optionally also applying it as the default for every building in its category."""
        self.building_color_overrides[guid] = color
        if apply_to_category:
            bd = self.buildings.get(guid)
            if bd:
                self.category_color_overrides[bd.get_category_english()] = color

    def get_regions(self) -> list[str]:
        """Return internal region keys present in construction menu."""
        return list(self.construction_menu.keys())

    def get_tiers_for_region(self, region: str) -> list[dict]:
        """Return list of tier/category dicts for a region."""
        region_data = self.construction_menu.get(region, {})
        if isinstance(region_data, dict):
            return region_data.get('tiers', [])
        elif isinstance(region_data, list):
            return region_data
        return []

    def get_menu_section(self, region: str, section: str) -> dict:
        """Return a top-level section dict (infrastructure/materials/ornaments) for a region."""
        region_data = self.construction_menu.get(region, {})
        if isinstance(region_data, dict):
            return region_data.get(section, {})
        return {}

    def get_ornaments(self, lang: str = 'english') -> list[BuildingData]:
        """Return all ornament buildings (across all regions)."""
        return [b for b in self.buildings.values() if b.is_ornament()]

    def get_all_buildings_for_region(self, region: str) -> list[BuildingData]:
        """Return all buildings associated with a region."""
        return [b for b in self.buildings.values() if region in b.associated_regions]

    def get_buildings_by_categories(self, region: str, categories: set[str]) -> list[BuildingData]:
        """Return buildings for a region whose English category is in *categories*."""
        result = []
        for b in self.buildings.values():
            if region and region not in b.associated_regions:
                continue
            if b.get_category_english() in categories:
                result.append(b)
        # Sort by category then name for consistent ordering
        result.sort(key=lambda b: (b.get_category_english(), b.guid))
        return result


# Singleton
_data_manager = None

def get_data_manager() -> DataManager:
    global _data_manager
    if _data_manager is None:
        _data_manager = DataManager()
        _data_manager.load()
    return _data_manager
