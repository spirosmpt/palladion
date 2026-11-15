"""Public warnings (112 cell broadcast): where to warn and what to tell people to do. All of it is code.

Areas
  Each settlement gets a cell: the part of the land closer to its centre than to any other settlement
  (a Voronoi cell). The front at now, now+15' and now+30' cuts each cell into two time bands. A band
  becomes a warning area when it holds at least MIN_BUILDINGS buildings the front has not reached yet.

Instruction (the agent never chooses it)
  A few buildings spread across the band are routed with the fire-aware road graph, leaving now:
  * every sample reaches a refuge with the safety margin  -> evacuate, towards that refuge, by those roads
    (only refuges at least REFUGE_CLEAR_M from everything the front ever burns count: a public message
    must not send a whole area towards a place the fire will reach or pass beside later, as a beach
    downwind can be)
  * otherwise, a shore people can walk into the sea from is near (a beach or a low shore, never a cliff;
    see engines/coast.py)                                 -> do not drive; walk to that beach
  * otherwise                                              -> do not drive; stay in a solid building
  "Every sample" is deliberate: telling a whole area to drive when some of it cannot get out is how
  people end up trapped on a road.
"""
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

import numpy as np
import shapely
import shapely.ops
from shapely import affinity
from shapely.geometry import MultiPoint, Point, box
from shapely.geometry.base import BaseGeometry

from palladion.domain.models import SafeZone
from palladion.engines.buildings import BuildingLayer
from palladion.engines.coast import CoastAccess
from palladion.engines.fire.model import FireEngine
from palladion.engines.routing.graph import RoadGraph
from palladion.geo.proj import haversine_m

BANDS = ((0, 15), (15, 30))
MIN_BUILDINGS = 5
SAMPLES = 6
COAST_WALK_M = 800.0                 # median walk from the area to the named beach
COAST_WALK_MAX_M = 1000.0            # and nobody in the area further than this
ROAD_END_M = 120.0                   # a street "ends at" the beach when one end is this close

REFUGE_CLEAR_M = 1000.0


class Action(StrEnum):
    evacuate = "evacuate"
    go_to_coast = "go_to_coast"
    shelter = "shelter"


@dataclass
class Settlement:
    name: str
    lon: float
    lat: float
    cell: BaseGeometry


@dataclass
class Instruction:
    action: Action
    destination: str | None = None          # settlement the refuge is in (what people recognise)
    refuge: str | None = None               # the refuge's own name, if it has one
    refuge_kind: str | None = None          # beach | port | open_space ...
    roads: list[str] = field(default_factory=list)
    routed: int = 0                          # samples with a safe route
    sampled: int = 0
    coast_m: float | None = None             # median distance to shore access, as the crow flies
    coast_name: str | None = None            # the beach to walk to, when it has a name
    coast_road: str | None = None            # otherwise: the street that ends at it
    coast_point: tuple[float, float] | None = None   # where on the shore people are sent
    reasons: list[str] = field(default_factory=list)


@dataclass
class Threat:
    settlement: str
    band: tuple[int, int]                   # minutes from now
    area: BaseGeometry
    buildings: int
    first_arrival: datetime
    instruction: Instruction

    @property
    def key(self) -> str:
        return f"{self.settlement}|{self.band[0]}-{self.band[1]}"


def settlement_cells(areas: list[tuple[str, float, float]], land: BaseGeometry,
                     bbox: tuple[float, float, float, float]) -> list[Settlement]:
    """Voronoi cells of settlement centres, clipped to land. Computed in a frame where 1 unit of
    longitude and latitude are about the same distance, so the cells are not stretched."""
    frame = box(*bbox)
    inside = [(n, x, y) for n, x, y in areas if frame.contains(Point(x, y))]
    if not inside:
        return []
    if len(inside) == 1:
        n, x, y = inside[0]
        return [Settlement(n, x, y, land.intersection(frame))]
    k = math.cos(math.radians(sum(y for _, _, y in inside) / len(inside)))
    pts = MultiPoint([(x * k, y) for _, x, y in inside])
    env = affinity.scale(frame, xfact=k, yfact=1.0, origin=(0, 0))
    cells = shapely.voronoi_polygons(pts, extend_to=env)
    out = []
    clip = land.intersection(frame)
    for cell in cells.geoms:
        cell = affinity.scale(cell, xfact=1 / k, yfact=1.0, origin=(0, 0))
        for n, x, y in inside:
            if cell.contains(Point(x, y)):
                piece = cell.intersection(clip)
                if not piece.is_empty:
                    out.append(Settlement(n, x, y, piece))
                break
    return out


def nearest_settlement(settlements: list[Settlement], lon: float, lat: float) -> Settlement | None:
    p = Point(lon, lat)
    for s in settlements:
        if s.cell.contains(p):
            return s
    return min(settlements, key=lambda s: haversine_m(lon, lat, s.lon, s.lat), default=None)


class WarningEngine:
    def __init__(self, fire: FireEngine, settlements: list[Settlement], buildings: BuildingLayer | None,
                 roads: RoadGraph | None, zones: list[SafeZone], coast: CoastAccess | None, margin_min: float,
                 end: datetime):
        self.fire, self.settlements, self.buildings = fire, settlements, buildings
        self.roads, self.coast, self.margin_min = roads, coast, margin_min
        # Ends of named streets, for describing beaches that have no name ("at the end of X street").
        ends = []
        for e in (roads.edges if roads is not None else []):
            if e.name:
                ends.append((e.name, *e.coords[0], *e.coords[-1]))
                ends.append((e.name, *e.coords[-1], *e.coords[0]))
        self._ends = ends
        self._end_xy = np.array([[x, y, ox, oy] for _, x, y, ox, oy in ends]) if ends else np.zeros((0, 4))
        burned = fire.front(end)
        self.zones = [z for z in zones if burned.is_empty
                      or haversine_m(z.lon, z.lat, *_nearest(burned, z.lon, z.lat)) >= REFUGE_CLEAR_M]

    def assess(self, now: datetime, horizon_min: int = 30, only: str | None = None,
               min_buildings: int = MIN_BUILDINGS) -> list[Threat]:
        if self.buildings is None or not len(self.buildings):
            return []
        s_now = (now - self.fire.t0).total_seconds()
        arr = self.buildings.arrival_s
        bands = [b for b in BANDS if b[0] < horizon_min] if horizon_min <= 30 else [*BANDS, (30, horizon_min)]
        fronts = {m: self.fire.front(now + timedelta(minutes=m)) for b in bands for m in b}
        out = []
        for st in self.settlements:
            if only is not None and st.name != only:
                continue
            for lo, hi in bands:
                ring = fronts[hi].difference(fronts[lo]).intersection(st.cell)
                if ring.is_empty:
                    continue
                sel = np.where((arr > s_now + lo * 60) & (arr <= s_now + hi * 60))[0]
                sel = sel[shapely.contains_xy(ring, self.buildings.lon[sel], self.buildings.lat[sel])]
                if len(sel) < min_buildings:
                    continue
                first = self.fire.t0 + timedelta(seconds=float(arr[sel].min()))
                out.append(Threat(st.name, (lo, hi), ring, int(len(sel)), first, self.instruct(now, sel)))
        out.sort(key=lambda t: t.first_arrival)
        return out

    def road_to(self, lon: float, lat: float) -> str | None:
        """A named street with one end at this point and the other end further inland."""
        if not len(self._end_xy):
            return None
        near = _haversine_rows(lon, lat, self._end_xy[:, 0], self._end_xy[:, 1])
        far = _haversine_rows(lon, lat, self._end_xy[:, 2], self._end_xy[:, 3])
        ok = np.where((near <= ROAD_END_M) & (far > near + 40))[0]
        if not len(ok):
            return None
        return self._ends[int(ok[np.argmin(near[ok])])][0]

    def instruct(self, now: datetime, sel: np.ndarray) -> Instruction:
        """What everyone in the area should do, decided from routes out of a few spread buildings."""
        order = sel[np.argsort(self.buildings.arrival_s[sel])]           # type: ignore[union-attr]
        idx = order[np.unique(np.linspace(0, len(order) - 1, min(SAMPLES, len(order))).astype(int))]
        pts = [(float(self.buildings.lon[i]), float(self.buildings.lat[i])) for i in idx]  # type: ignore[union-attr]
        ins = Instruction(action=Action.shelter, sampled=len(pts))
        routes = []
        if self.roads is not None and self.zones:
            routes = [self.roads.evacuate(x, y, now, self.zones, self.margin_min) for x, y in pts]
            ins.routed = sum(r.status == "ok" for r in routes)
        if routes and ins.routed == len(routes):
            zones = Counter(r.zone.id for r in routes if r.zone)
            zid = zones.most_common(1)[0][0]
            zone = next(z for z in self.zones if z.id == zid)
            road_m: Counter[str] = Counter()
            for r in routes:
                if r.zone and r.zone.id == zid:
                    road_m.update(r.road_m)
            dest = nearest_settlement(self.settlements, zone.lon, zone.lat)
            ins.action, ins.destination = Action.evacuate, dest.name if dest else None
            ins.refuge = zone.name if zone.name and zone.name != zone.kind.value else None
            ins.refuge_kind = zone.kind.value
            ins.roads = [n for n, _ in road_m.most_common(2)]
            ins.reasons.append(f"Και τα {len(routes)} δείγματα κτιρίων βγαίνουν με όχημα, με περιθώριο "
                               f"{self.margin_min:.0f}′ από το μέτωπο.")
            return ins
        if self.roads is None:
            ins.reasons.append("Δεν υπάρχει οδικό δίκτυο: δεν μπορεί να προταθεί διαδρομή.")
        else:
            ins.reasons.append(f"Μόνο {ins.routed} από {len(pts)} δείγματα κτιρίων έχουν ασφαλή διαδρομή με όχημα: "
                               "δεν προτείνεται μετακίνηση με αυτοκίνητο.")
        if self.coast is None:
            ins.reasons.append("Δεν υπάρχουν δεδομένα ακτής: δεν προτείνεται μετάβαση στη θάλασσα.")
            return ins
        found = [a for a in (self.coast.nearest_access(x, y) for x, y in pts) if a is not None]
        if not found:
            ins.reasons.append("Δεν βρέθηκε ακτή με πρόσβαση στο νερό (μόνο γκρεμοί ή ανεξέλεγκτη ακτή).")
            return ins
        shores = [self.coast.nearest_shore(x, y) for x, y in pts]
        cliffs_first = sum(1 for s in shores if s is not None and s[0] == "cliff")
        if cliffs_first:
            ins.reasons.append(f"Για {cliffs_first} από {len(pts)} δείγματα η πιο κοντινή ακτή είναι γκρεμός· "
                               "μετριέται η απόσταση ως σημείο με πρόσβαση στο νερό.")
        # One public message names one place: the access point whose farthest sample is nearest.
        target = min(found, key=lambda a: max(haversine_m(x, y, a.lon, a.lat) for x, y in pts))
        dists = [haversine_m(x, y, target.lon, target.lat) for x, y in pts]
        ins.coast_m = float(np.median(dists))
        if len(found) < len(pts) or ins.coast_m > COAST_WALK_M or max(dists) > COAST_WALK_MAX_M:
            ins.reasons.append(f"Η πλησιέστερη ακτή με πρόσβαση στο νερό απέχει περίπου {ins.coast_m:.0f} m "
                               f"(έως {max(dists):.0f} m): πολύ μακριά με τα πόδια.")
            return ins
        ins.coast_name = target.name
        ins.coast_point = (target.lon, target.lat)
        if not target.name:
            ins.coast_road = self.road_to(target.lon, target.lat)
            if ins.coast_road is None:
                ins.reasons.append("Η ακτή με πρόσβαση δεν έχει όνομα και κανένας δρόμος δεν καταλήγει εκεί: "
                                   "δεν μπορεί να περιγραφεί σε μήνυμα, οπότε δεν προτείνεται.")
                return ins
        ins.action = Action.go_to_coast
        where = f"παραλία {target.name}" if target.name else f"παραλία στο τέλος της οδού {ins.coast_road}"
        ins.reasons.append(f"Προς: {where}, περίπου {ins.coast_m:.0f} m (διάμεση απόσταση, έως {max(dists):.0f} m).")
        return ins


def _haversine_rows(lon: float, lat: float, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    r = 6_371_000.0
    p1, p2 = math.radians(lat), np.radians(lats)
    a = np.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(np.radians(lons - lon) / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))


def _nearest(geom: BaseGeometry, lon: float, lat: float) -> tuple[float, float]:
    p = shapely.ops.nearest_points(geom, Point(lon, lat))[0]
    return p.x, p.y


def coastline(land: BaseGeometry, bbox: tuple[float, float, float, float]) -> BaseGeometry:
    """The land boundary without the edges of the frame (those are cuts, not shore)."""
    frame = box(*bbox)
    return land.boundary.difference(frame.exterior.buffer(1e-4))
