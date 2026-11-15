"""Road graph with fire-aware, time-dependent evacuation routing.

Input is a GeoJSON FeatureCollection of LineStrings already split at intersections
(data_pipeline/fetch_osm.py does this). Each line becomes one edge between its endpoints.

Every edge stores the earliest time the fire front reaches any point along it. A vehicle may use an
edge only if it leaves the edge at least `margin` before the fire gets there. Because the fire can
only close roads (never reopen them), earliest-arrival Dijkstra is exact for this rule.
"""
import heapq
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import numpy as np

from palladion.domain.models import RoadStatus, SafeZone
from palladion.engines.fire.model import FireEngine
from palladion.geo.proj import haversine_m

# Default urban speeds (km/h) for evacuation traffic: below legal limits on purpose.
SPEED_KMH = {
    "motorway": 80, "trunk": 60, "primary": 50, "secondary": 40, "tertiary": 35,
    "unclassified": 25, "residential": 20, "living_street": 10, "service": 15, "track": 10,
}
DEFAULT_SPEED_KMH = 20
SAMPLE_STEP_M = 25.0
SNAP_LIMIT_M = 600.0


def _key(lon: float, lat: float) -> tuple[float, float]:
    return (round(lon, 7), round(lat, 7))


@dataclass
class Edge:
    id: str
    u: int
    v: int
    coords: list[tuple[float, float]]
    length_m: float
    speed_kmh: float
    name: str
    highway: str
    oneway: bool
    fire_s: float = math.inf          # seconds after ignition when fire reaches the edge
    dead_end: bool = False

    @property
    def travel_s(self) -> float:
        return self.length_m / (self.speed_kmh / 3.6)


@dataclass
class RouteResult:
    status: str                                    # "ok" | "no_safe_route" | "origin_burned" | "off_network"
    zone: SafeZone | None = None
    coords: list[tuple[float, float]] = field(default_factory=list)
    depart: datetime | None = None
    arrive: datetime | None = None
    min_margin_min: float | None = None
    distance_m: float = 0.0
    dead_end_edges: int = 0
    snap_m: float = 0.0
    warnings: list[str] = field(default_factory=list)
    road_m: dict[str, float] = field(default_factory=dict)   # metres driven on each named road
    edges: list[int] = field(default_factory=list)           # edge indices, in driving order (evacuate only)
    edge_margin_s: list[float] = field(default_factory=list)  # the front's time minus the time on each edge


class RoadGraph:
    def __init__(self, fire: FireEngine):
        self.fire = fire
        self.nodes: list[tuple[float, float]] = []
        self.edges: list[Edge] = []
        self.out: dict[int, list[int]] = defaultdict(list)     # node -> edge indices usable from it
        self._index: dict[tuple[float, float], int] = {}
        self._node_arr: np.ndarray = np.zeros((0, 2))

    # -- construction ---------------------------------------------------------------------------
    def _node(self, lon: float, lat: float) -> int:
        k = _key(lon, lat)
        if k not in self._index:
            self._index[k] = len(self.nodes)
            self.nodes.append((lon, lat))
        return self._index[k]

    @classmethod
    def from_geojson(cls, fc: dict[str, Any], fire: FireEngine) -> "RoadGraph":
        g = cls(fire)
        for i, f in enumerate(fc.get("features", [])):
            geom = f.get("geometry") or {}
            if geom.get("type") != "LineString" or len(geom["coordinates"]) < 2:
                continue
            coords = [(float(c[0]), float(c[1])) for c in geom["coordinates"]]
            p = f.get("properties") or {}
            length = sum(haversine_m(*coords[j], *coords[j + 1]) for j in range(len(coords) - 1))
            if length <= 0:
                continue
            highway = str(p.get("highway", "unclassified"))
            speed = float(p.get("speed_kmh") or SPEED_KMH.get(highway, DEFAULT_SPEED_KMH))
            u, v = g._node(*coords[0]), g._node(*coords[-1])
            if u == v:
                continue
            e = Edge(id=str(p.get("id", i)), u=u, v=v, coords=coords, length_m=length, speed_kmh=speed,
                     name=str(p.get("name") or ""), highway=highway, oneway=bool(p.get("oneway", False)))
            g.edges.append(e)
            idx = len(g.edges) - 1
            g.out[u].append(idx)
            if not e.oneway:
                g.out[v].append(idx)
        g._compute_fire()
        g._mark_dead_ends()
        g._node_arr = np.array(g.nodes) if g.nodes else np.zeros((0, 2))
        return g

    def _compute_fire(self) -> None:
        for e in self.edges:
            lon, lat = _densify(e.coords, SAMPLE_STEP_M)
            e.fire_s = float(np.min(self.fire.arrival_seconds(lon, lat)))

    def _mark_dead_ends(self) -> None:
        """Edges outside the graph's 2-core: branches that lead nowhere but back."""
        nbrs: dict[int, set[int]] = defaultdict(set)
        for e in self.edges:
            nbrs[e.u].add(e.v)
            nbrs[e.v].add(e.u)
        degree = {n: len(s) for n, s in nbrs.items()}
        stack = [n for n, d in degree.items() if d <= 1]
        removed: set[int] = set()
        while stack:
            n = stack.pop()
            if n in removed:
                continue
            removed.add(n)
            for m in nbrs[n]:
                if m not in removed:
                    degree[m] -= 1
                    if degree[m] <= 1:
                        stack.append(m)
        for e in self.edges:
            e.dead_end = e.u in removed or e.v in removed

    # -- queries --------------------------------------------------------------------------------
    def nearest_node(self, lon: float, lat: float) -> tuple[int, float]:
        if not self.nodes:
            return -1, math.inf
        d = np.hypot((self._node_arr[:, 0] - lon) * math.cos(math.radians(lat)), self._node_arr[:, 1] - lat)
        i = int(np.argmin(d))
        return i, haversine_m(lon, lat, *self.nodes[i])

    def status(self, e: Edge, t: datetime, warning_min: float = 30.0) -> RoadStatus:
        s = (t - self.fire.t0).total_seconds()
        if e.fire_s <= s:
            return RoadStatus.burned
        if e.fire_s <= s + warning_min * 60:
            return RoadStatus.threatened
        return RoadStatus.open

    def to_geojson(self) -> dict[str, Any]:
        t0 = self.fire.t0.timestamp()
        return {"type": "FeatureCollection", "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": e.coords},
            "properties": {
                "id": e.id, "name": e.name, "highway": e.highway, "dead_end": e.dead_end,
                # Epoch seconds, or -1 when the reconstructed front never reaches the road.
                "fire_epoch": t0 + e.fire_s if math.isfinite(e.fire_s) else -1,
            },
        } for e in self.edges]}

    def drive_to(self, lon: float, lat: float, to_lon: float, to_lat: float, depart: datetime,
                 margin_min: float, after_min: float = 30.0) -> RouteResult:
        """A responder's route from a base to a place. A road can be used before the front reaches it
        (with the safety margin) or once the front has passed it by `after_min` (burned out, passable
        with care); not while it is burning. Arriving where the fire is now is the point of the trip,
        so the destination itself is never refused."""
        start, snap = self.nearest_node(lon, lat)
        goal_node, snap_goal = self.nearest_node(to_lon, to_lat)
        if start < 0 or goal_node < 0 or snap > SNAP_LIMIT_M or snap_goal > SNAP_LIMIT_M:
            return RouteResult(status="off_network", snap_m=max(snap, snap_goal), depart=depart,
                               warnings=["Το σημείο απέχει πολύ από το οδικό δίκτυο."])
        t0 = self.fire.t0
        s0 = (depart - t0).total_seconds()
        margin, after = margin_min * 60.0, after_min * 60.0
        best = {start: s0}
        via: dict[int, tuple[int, int]] = {}
        heap = [(s0, start)]
        found = False
        while heap:
            t, n = heapq.heappop(heap)
            if t > best.get(n, math.inf):
                continue
            if n == goal_node:
                found = True
                break
            for ei in self.out[n]:
                e = self.edges[ei]
                m = e.v if e.u == n else e.u
                t_exit = t + e.travel_s
                burning = e.fire_s - margin < t_exit < e.fire_s + after
                if burning and m != goal_node:
                    continue
                if t_exit < best.get(m, math.inf):
                    best[m] = t_exit
                    via[m] = (n, ei)
                    heapq.heappush(heap, (t_exit, m))
        if not found:
            return RouteResult(status="no_safe_route", snap_m=snap, depart=depart, warnings=[
                "Κανένας δρόμος δεν φτάνει εκεί χωρίς να περάσει από δρόμο που καίγεται τώρα."])
        coords: list[tuple[float, float]] = []
        t, dist, burned = s0, 0.0, 0.0
        road_m: dict[str, float] = {}
        n, path = goal_node, []
        while n != start:
            p, ei = via[n]
            path.append((p, ei))
            n = p
        for p, ei in reversed(path):
            e = self.edges[ei]
            seg = e.coords if e.u == p else list(reversed(e.coords))
            coords.extend(seg if not coords else seg[1:])
            t += e.travel_s
            dist += e.length_m
            if t > e.fire_s:
                burned += e.length_m
            if e.name:
                road_m[e.name] = road_m.get(e.name, 0.0) + e.length_m
        warnings = []
        if burned:
            km = f"{max(0.1, burned / 1000):.1f}".replace(".", ",")
            warnings.append(f"Περίπου {km} km της διαδρομής είναι σε δρόμους όπου το μέτωπο έχει ήδη περάσει: "
                            "καπνός, συντρίμμια, καμένα οχήματα.")
        return RouteResult(status="ok", coords=coords, depart=depart, arrive=t0 + timedelta(seconds=t),
                           distance_m=dist, snap_m=snap, warnings=warnings, road_m=road_m)

    def reach(self, sources: list[tuple[float, float, datetime, str]], margin_min: float, after_min: float = 30.0,
              until: datetime | None = None) -> tuple[dict[int, float], dict[int, str]]:
        """Earliest arrival (seconds after ignition) at every node from several starts at once, with the same road
        rules as drive_to (no road that is burning on the way). Returns ({node: seconds}, {node: source id})."""
        t0 = self.fire.t0
        margin, after = margin_min * 60.0, after_min * 60.0
        limit = (until - t0).total_seconds() if until else math.inf
        best: dict[int, float] = {}
        who: dict[int, str] = {}
        heap: list[tuple[float, int, str]] = []
        for lon, lat, depart, sid in sources:
            n, snap = self.nearest_node(lon, lat)
            if n < 0 or snap > SNAP_LIMIT_M:
                continue
            s0 = (depart - t0).total_seconds()
            if s0 < best.get(n, math.inf):
                best[n], who[n] = s0, sid
                heapq.heappush(heap, (s0, n, sid))
        while heap:
            t, n, sid = heapq.heappop(heap)
            if t > best.get(n, math.inf) or t > limit:
                continue
            for ei in self.out[n]:
                e = self.edges[ei]
                m = e.v if e.u == n else e.u
                t_exit = t + e.travel_s
                if e.fire_s - margin < t_exit < e.fire_s + after:
                    continue
                if t_exit < best.get(m, math.inf):
                    best[m], who[m] = t_exit, sid
                    heapq.heappush(heap, (t_exit, m, sid))
        return best, who

    def evacuate(self, lon: float, lat: float, depart: datetime, zones: list[SafeZone],
                 margin_min: float) -> RouteResult:
        start, snap = self.nearest_node(lon, lat)
        if start < 0 or snap > SNAP_LIMIT_M:
            return RouteResult(status="off_network", snap_m=snap, depart=depart,
                               warnings=["Το σημείο απέχει πολύ από το οδικό δίκτυο."])
        t0 = self.fire.t0
        s0 = (depart - t0).total_seconds()
        if self.fire.arrival_seconds(np.array([lon]), np.array([lat]))[0] <= s0:
            return RouteResult(status="origin_burned", snap_m=snap, depart=depart,
                               warnings=["Το μέτωπο έχει ήδη περάσει από αυτό το σημείο."])

        targets: dict[int, SafeZone] = {}
        for z in zones:
            n, dz = self.nearest_node(z.lon, z.lat)
            if n >= 0 and dz <= SNAP_LIMIT_M:
                targets.setdefault(n, z)
        margin = margin_min * 60.0

        best = {start: s0}
        via: dict[int, tuple[int, int]] = {}            # node -> (prev node, edge index)
        heap = [(s0, start)]
        goal = -1
        while heap:
            t, n = heapq.heappop(heap)
            if t > best.get(n, math.inf):
                continue
            if n in targets:
                goal = n
                break
            for ei in self.out[n]:
                e = self.edges[ei]
                m = e.v if e.u == n else e.u
                t_exit = t + e.travel_s
                if t_exit > e.fire_s - margin:
                    continue
                if t_exit < best.get(m, math.inf):
                    best[m] = t_exit
                    via[m] = (n, ei)
                    heapq.heappush(heap, (t_exit, m))

        if goal < 0:
            return RouteResult(status="no_safe_route", snap_m=snap, depart=depart, warnings=[
                "Καμία διαδρομή με όχημα δεν φτάνει σε ασφαλή ζώνη πριν από το μέτωπο "
                f"(περιθώριο {margin_min:.0f} λεπτά). "
                "Χρειάζεται εναλλακτική: καταφύγιο ή παραλία με τα πόδια."])

        path_edges: list[tuple[int, int]] = []
        n = goal
        while n != start:
            p, ei = via[n]
            path_edges.append((p, ei))
            n = p
        path_edges.reverse()

        coords: list[tuple[float, float]] = []
        t = s0
        min_margin = math.inf
        dist = 0.0
        flags: list[bool] = []
        road_m: dict[str, float] = {}
        edge_margin: list[float] = []
        for p, ei in path_edges:
            e = self.edges[ei]
            if e.name:
                road_m[e.name] = road_m.get(e.name, 0.0) + e.length_m
            seg = e.coords if e.u == p else list(reversed(e.coords))
            coords.extend(seg if not coords else seg[1:])
            t += e.travel_s
            dist += e.length_m
            min_margin = min(min_margin, e.fire_s - t)
            edge_margin.append(e.fire_s - t)
            flags.append(e.dead_end)
        # Driving out of the cul-de-sac you start in is fine, and so is a spur that ends at the refuge
        # itself. Driving *into* any other dead end is the trap, so only those are counted.
        while flags and flags[0]:
            flags.pop(0)
        while flags and flags[-1]:
            flags.pop()
        dead = sum(flags)

        warnings = []
        if dead:
            warnings.append(f"Η διαδρομή μπαίνει σε αδιέξοδο ({dead} τμ. δρόμου): έλεγξε ότι δεν εγκλωβίζει.")
        if snap > 150:
            warnings.append(f"Το σημείο εκκίνησης απέχει {snap:.0f} m από τον πλησιέστερο δρόμο.")
        return RouteResult(
            status="ok", zone=targets[goal], coords=coords, depart=depart,
            arrive=t0 + timedelta(seconds=t), distance_m=dist, dead_end_edges=dead, snap_m=snap,
            min_margin_min=None if math.isinf(min_margin) else min_margin / 60.0, warnings=warnings, road_m=road_m,
            edges=[ei for _, ei in path_edges], edge_margin_s=edge_margin)


def _densify(coords: list[tuple[float, float]], step_m: float) -> tuple[np.ndarray, np.ndarray]:
    lons, lats = [coords[0][0]], [coords[0][1]]
    for (x1, y1), (x2, y2) in zip(coords, coords[1:], strict=False):
        n = max(1, int(haversine_m(x1, y1, x2, y2) // step_m))
        for k in range(1, n + 1):
            lons.append(x1 + (x2 - x1) * k / n)
            lats.append(y1 + (y2 - y1) * k / n)
    return np.array(lons), np.array(lats)
