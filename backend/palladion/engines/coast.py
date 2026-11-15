"""Where people on foot can actually get into the sea, and where the shore is a cliff.

The shore is sampled every STEP_M metres. Each sample is classed:
  cliff       an OSM-mapped cliff within CLIFF_NEAR_M, or (from the elevation tiles) the ground is already
              CLIFF_HEIGHT_M high within EDGE_M of the water line, or rises CLIFF_RISE_M within CLIFF_RUN_M.
              The first test matters most: the elevation grid is coarser than the shore, so where a bluff
              stands at the water the grid shows high ground at the water line itself.
  beach       inside or next to an OSM beach
  gentle      neither: the elevation stays low near the water
  unknown     no elevation data here and no mapped beach

Only beach and gentle samples count as access. Without elevation data only mapped beaches do: a
public warning must not send people to a stretch of shore nobody has checked.

In Mati in 2018 many people who tried to reach the sea were trapped on the bluffs above it. This is
the reason the 112 desk asks this engine before it says "walk to the coast".
"""
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import shapely
import shapely.ops
from shapely.geometry import LineString, MultiLineString, Point, shape
from shapely.geometry.base import BaseGeometry

from palladion.datasets.dem import Dem
from palladion.geo.proj import haversine_m

STEP_M = 25.0
CLIFF_NEAR_M = 40.0
CLIFF_RISE_M = 8.0
CLIFF_HEIGHT_M = 6.0
EDGE_M = 20.0
CLIFF_RUN_M = 40.0
BEACH_NEAR_M = 40.0
DEG_M = 111_320.0


class Shore(StrEnum):
    cliff = "cliff"
    beach = "beach"
    gentle = "gentle"
    unknown = "unknown"


ACCESS = {Shore.beach, Shore.gentle}


@dataclass
class Access:
    kind: Shore
    name: str | None          # beach name, if mapped
    lon: float
    lat: float
    distance_m: float


def _lines(geom: BaseGeometry) -> list[LineString]:
    if isinstance(geom, LineString):
        return [geom]
    if isinstance(geom, MultiLineString):
        return list(geom.geoms)
    if hasattr(geom, "geoms"):
        return [g for part in geom.geoms for g in _lines(part)]
    return []


class CoastAccess:
    def __init__(self, coast: BaseGeometry, land: BaseGeometry, dem: Dem | None,
                 cliffs_fc: dict[str, Any] | None, beaches: list[tuple[str, BaseGeometry]]):
        self.has_dem = dem is not None
        cliffs = [shape(f["geometry"]) for f in (cliffs_fc or {}).get("features", [])
                  if f["properties"].get("kind") == "cliff"]
        mapped_beaches = [(f["properties"].get("name") or "", shape(f["geometry"]))
                          for f in (cliffs_fc or {}).get("features", []) if f["properties"].get("kind") == "beach"]
        self.beaches = [(n, g) for n, g in beaches + mapped_beaches if not g.is_empty]
        self.cliff_lines = shapely.union_all(cliffs) if cliffs else None

        lon, lat, nx, ny = [], [], [], []
        k = math.cos(math.radians(land.centroid.y)) if not land.is_empty else 1.0
        for line in _lines(coast):
            length_m = haversine_m(*line.coords[0], *line.coords[-1]) if len(line.coords) == 2 else \
                sum(haversine_m(*a, *b) for a, b in zip(line.coords, line.coords[1:], strict=False))
            n = max(2, int(length_m / STEP_M) + 1)
            for f in np.linspace(0, 1, n):
                p = line.interpolate(f, normalized=True)
                q = line.interpolate(min(1.0, f + 0.5 / n), normalized=True)
                r = line.interpolate(max(0.0, f - 0.5 / n), normalized=True)
                tx, ty = (q.x - r.x) * k, q.y - r.y
                norm = math.hypot(tx, ty) or 1.0
                ux, uy = -ty / norm, tx / norm                # one of the two normals (metric frame)
                probe = Point(p.x + ux * 15 / DEG_M / k, p.y + uy * 15 / DEG_M)
                if not land.contains(probe):
                    ux, uy = -ux, -uy                          # point the normal inland
                lon.append(p.x)
                lat.append(p.y)
                nx.append(ux)
                ny.append(uy)
        self.lon, self.lat = np.array(lon), np.array(lat)
        self.kind = np.array([self._classify(i, dem, k, nx[i], ny[i]) for i in range(len(lon))], dtype=object)

    def _classify(self, i: int, dem: Dem | None, k: float, ux: float, uy: float) -> Shore:
        p = Point(self.lon[i], self.lat[i])
        if self.cliff_lines is not None and _dist_m(self.cliff_lines, p) <= CLIFF_NEAR_M:
            return Shore.cliff
        on_beach = any(_dist_m(g, p) <= BEACH_NEAR_M for _, g in self.beaches)
        if dem is not None:
            d = np.array([0.0, EDGE_M / 2, EDGE_M, CLIFF_RUN_M])
            z = dem.sample(self.lon[i] + ux * d / DEG_M / k, self.lat[i] + uy * d / DEG_M)
            steep = np.isfinite(z).all() and (z[:3].max() >= CLIFF_HEIGHT_M or z.max() - z[0] >= CLIFF_RISE_M)
            if steep and not on_beach:
                return Shore.cliff
            if on_beach:
                return Shore.beach
            return Shore.gentle if np.isfinite(z).all() else Shore.unknown
        return Shore.beach if on_beach else Shore.unknown

    # -- queries ----------------------------------------------------------------------------------
    def nearest_access(self, lon: float, lat: float) -> Access | None:
        ok = np.isin(self.kind, list(ACCESS))
        if not ok.any():
            return None
        d = _haversine_vec(lon, lat, self.lon[ok], self.lat[ok])
        i = int(np.argmin(d))
        lo, la = float(self.lon[ok][i]), float(self.lat[ok][i])
        kind = Shore(self.kind[ok][i])
        return Access(kind, self._beach_name(lo, la), lo, la, float(d[i]))

    def nearest_shore(self, lon: float, lat: float) -> tuple[Shore, float] | None:
        if not len(self.lon):
            return None
        d = _haversine_vec(lon, lat, self.lon, self.lat)
        i = int(np.argmin(d))
        return Shore(self.kind[i]), float(d[i])

    def near_cliff(self, lon: float, lat: float, within_m: float = 150.0) -> bool:
        c = self.kind == Shore.cliff
        return bool(c.any() and _haversine_vec(lon, lat, self.lon[c], self.lat[c]).min() <= within_m)

    def _beach_name(self, lon: float, lat: float) -> str | None:
        p = Point(lon, lat)
        named = [(n, _dist_m(g, p)) for n, g in self.beaches if n]
        named = [x for x in named if x[1] <= BEACH_NEAR_M * 2]
        return min(named, key=lambda x: x[1])[0] if named else None

    def counts(self) -> dict[str, int]:
        return {s.value: int((self.kind == s).sum()) for s in Shore}

    def to_geojson(self) -> dict[str, Any]:
        """Runs of consecutive samples of the same kind, as lines for the map."""
        feats = []
        start = 0
        for i in range(1, len(self.lon) + 1):
            end = i == len(self.lon) or self.kind[i] != self.kind[start] or \
                haversine_m(self.lon[i - 1], self.lat[i - 1], self.lon[i], self.lat[i]) > STEP_M * 3
            if end:
                pts = [[float(self.lon[j]), float(self.lat[j])] for j in range(start, i)]
                if len(pts) == 1:
                    pts.append(pts[0])
                feats.append({"type": "Feature", "properties": {"kind": str(self.kind[start])},
                              "geometry": {"type": "LineString", "coordinates": pts}})
                start = i
        return {"type": "FeatureCollection", "features": feats}


def _dist_m(geom: BaseGeometry, p: Point) -> float:
    q = shapely.ops.nearest_points(geom, p)[0]
    return haversine_m(q.x, q.y, p.x, p.y)


def _haversine_vec(lon: float, lat: float, lons: np.ndarray, lats: np.ndarray) -> np.ndarray:
    r = 6_371_000.0
    p1, p2 = math.radians(lat), np.radians(lats)
    dp, dl = p2 - p1, np.radians(lons - lon)
    a = np.sin(dp / 2) ** 2 + math.cos(p1) * np.cos(p2) * np.sin(dl / 2) ** 2
    return 2 * r * np.arcsin(np.sqrt(a))
