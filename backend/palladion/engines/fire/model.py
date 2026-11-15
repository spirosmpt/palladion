"""Fire front engine.

`FireEngine` is the interface the rest of the system uses. `AnchoredEllipseFire` is the replay
implementation: an elliptical front with the ignition at its rear focus, whose growth is fitted
through sourced anchors (place + time). A live implementation (satellite hotspots + forecast wind)
will implement the same interface.

Geometry. For an ellipse with eccentricity e and the ignition at a focus, a point at distance r and
angle θ from the spread direction lies on the front whose head distance D satisfies
    D = r (1 - e cos θ) / (1 - e).
We call D the point's *effective distance*. Arrival time is the time the head distance reaches D,
which we interpolate between anchors. The front passes every anchor exactly at its time.
"""
import math
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Protocol

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

from palladion.domain.models import FireSpec
from palladion.geo.proj import LocalFrame, bearing_to_unit


class FireEngine(Protocol):
    def arrival_time(self, lon: float, lat: float) -> datetime | None: ...
    def arrival_seconds(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray: ...
    def front(self, t: datetime) -> BaseGeometry: ...
    def head_point(self, t: datetime) -> tuple[float, float]: ...
    @property
    def t0(self) -> datetime: ...


class InconsistentAnchors(ValueError):
    pass


class AnchoredEllipseFire:
    """Replay fire front.

    Without a perimeter, the front stops growing after the last anchor (the head reached the sea).
    With an observed burned-area perimeter (e.g. Copernicus EMS), the front is confined to it, and
    the parts of it the ellipse has not reached by the last anchor burn progressively over
    `spec.post_head_spread_min` minutes (the flanks). That last part is an assumption, not sourced.
    """

    N_VERTICES = 180

    def __init__(self, spec: FireSpec, land: BaseGeometry, perimeter: BaseGeometry | None = None):
        self.spec = spec
        self.perimeter = perimeter
        self.ground = land                     # all land, before the perimeter: "on land but never burns" vs "at sea"
        self.land = land.intersection(perimeter) if perimeter is not None else land
        shapely.prepare(self.land)
        ign = spec.ignition
        self._t0 = ign.time
        self.frame = LocalFrame(ign.lon, ign.lat)
        self.u = np.array(bearing_to_unit(spec.spread_bearing_deg))      # spread direction (east, north)
        self.v = np.array([-self.u[1], self.u[0]])                        # left-hand normal
        lb = spec.length_to_breadth
        self.e = math.sqrt(1.0 - 1.0 / (lb * lb)) if lb > 1 else 0.0

        knots_t, knots_d = [0.0], [0.0]
        for a in sorted(spec.anchors, key=lambda a: a.time):
            t = (a.time - self._t0).total_seconds()
            d = float(self._effective(*self.frame.to_xy(a.lon, a.lat)))
            if t <= knots_t[-1] or d <= knots_d[-1]:
                raise InconsistentAnchors(
                    f"anchor '{a.id}' ({a.time:%H:%M}) is not later and further than the previous one "
                    f"(effective distance {d:.0f} m); the front cannot move backwards")
            knots_t.append(t)
            knots_d.append(d)
        self.anchor_count = len(knots_t)
        if perimeter is not None:
            d_max = self._max_effective(self.land)
            if d_max > knots_d[-1]:
                knots_t.append(knots_t[-1] + spec.post_head_spread_min * 60.0)
                knots_d.append(d_max * 1.0001)
        self.knots_t = np.array(knots_t)
        self.knots_d = np.array(knots_d)

    def _max_effective(self, geom: BaseGeometry) -> float:
        """Largest effective distance of any point of `geom` (attained on its boundary)."""
        coords = shapely.get_coordinates(geom.boundary.segmentize(0.0005))
        x, y = self.frame.to_xy(coords[:, 0], coords[:, 1])  # type: ignore[arg-type]
        return float(np.max(self._effective(x, y)))

    def outside(self, lon: float, lat: float) -> bool:
        return not bool(shapely.contains_xy(self.land, lon, lat))

    def label_point(self, t: datetime, geom: BaseGeometry) -> tuple[float, float]:
        """A point on the moving front line of `geom` (not on the coast or the perimeter edge)."""
        d = self.head_distance(t)
        if geom.is_empty or d <= 0:
            return self.head_point(t)
        coords = shapely.get_coordinates(geom.boundary)
        xy = self.frame.to_xy(coords[:, 0], coords[:, 1])  # type: ignore[arg-type]
        x, y = np.asarray(xy[0]), np.asarray(xy[1])
        eff = self._effective(x, y)
        on_front = np.abs(eff - d) <= 0.02 * d
        if not on_front.any():
            return self.head_point(t)
        cand = coords[on_front]
        # Prefer the front point furthest downwind.
        proj = x[on_front] * self.u[0] + y[on_front] * self.u[1]
        best = cand[int(np.argmax(proj))]
        return (float(best[0]), float(best[1]))

    # -- core geometry -------------------------------------------------------------------------
    def _effective(self, x, y):
        x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
        r = np.hypot(x, y)
        with np.errstate(invalid="ignore", divide="ignore"):
            cos_t = np.where(r > 0, (x * self.u[0] + y * self.u[1]) / r, 1.0)
        return r * (1.0 - self.e * cos_t) / (1.0 - self.e)

    def head_distance(self, t: datetime) -> float:
        s = (t - self._t0).total_seconds()
        if s <= 0:
            return 0.0
        return float(np.interp(s, self.knots_t, self.knots_d))

    def head_point(self, t: datetime) -> tuple[float, float]:
        """Lon/lat of the head of the front at time t (may lie in the sea once the head reaches it)."""
        d = self.head_distance(t)
        lon, lat = self.frame.to_lonlat(float(self.u[0] * d), float(self.u[1] * d))
        return (lon, lat)

    @property
    def t0(self) -> datetime:
        return self._t0

    @property
    def final_time(self) -> datetime:
        return self._t0 + timedelta(seconds=float(self.knots_t[-1]))

    # -- public API ----------------------------------------------------------------------------
    def arrival_seconds(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Seconds after ignition when the front reaches each point; inf if it never does."""
        lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
        x, y = self.frame.to_xy(lon, lat)  # type: ignore[arg-type]
        d = self._effective(x, y)
        t = np.interp(d, self.knots_d, self.knots_t)
        t = np.where(d > self.knots_d[-1], np.inf, t)
        on_land = shapely.contains_xy(self.land, lon, lat)   # land, within the perimeter if any
        return np.where(on_land, t, np.inf)

    def arrival_time(self, lon: float, lat: float) -> datetime | None:
        s = float(self.arrival_seconds(np.array([lon]), np.array([lat]))[0])
        return None if math.isinf(s) else self._t0 + timedelta(seconds=s)

    def front(self, t: datetime) -> BaseGeometry:
        """Burned area at time t (lon/lat), clipped to land and, if given, to the observed perimeter."""
        d = self.head_distance(t)
        if d <= 0:
            return Polygon()
        a = d / (1.0 + self.e)
        b = a * math.sqrt(1.0 - self.e * self.e)
        centre = self.u * (a * self.e)
        phi = np.linspace(0, 2 * math.pi, self.N_VERTICES, endpoint=False)
        pts = centre[None, :] + np.outer(a * np.cos(phi), self.u) + np.outer(b * np.sin(phi), self.v)
        lon, lat = self.frame.to_lonlat(pts[:, 0], pts[:, 1])  # type: ignore[arg-type]
        ellipse = Polygon(np.column_stack([lon, lat]))
        return ellipse.intersection(self.land)

    def front_edge(self, geom: BaseGeometry) -> BaseGeometry:
        """The moving edge of a burned-area geometry: its boundary minus coast and perimeter edges."""
        if geom.is_empty:
            return geom
        fixed = self.land.boundary.buffer(2e-6)   # ~0.2 m: absorbs floating-point noise on shared edges
        return geom.boundary.difference(fixed)

    def isochrones(self, times: Sequence[datetime]) -> list[tuple[datetime, BaseGeometry]]:
        return [(t, self.front(t)) for t in times]
