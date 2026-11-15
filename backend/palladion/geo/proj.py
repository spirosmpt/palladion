"""Local metric frame for a scenario area.

An equirectangular projection around the scenario centre. Over a ~20 km area the
distance error is well under 0.1 %, which is far below the uncertainty of any fire
reconstruction, and it keeps the engines free of a PROJ dependency.
"""
import math
from dataclasses import dataclass

EARTH_RADIUS_M = 6_371_008.8


@dataclass(frozen=True)
class LocalFrame:
    lon0: float
    lat0: float

    @property
    def _kx(self) -> float:
        return math.radians(1) * EARTH_RADIUS_M * math.cos(math.radians(self.lat0))

    @property
    def _ky(self) -> float:
        return math.radians(1) * EARTH_RADIUS_M

    def to_xy(self, lon: float, lat: float) -> tuple[float, float]:
        return ((lon - self.lon0) * self._kx, (lat - self.lat0) * self._ky)

    def to_lonlat(self, x: float, y: float) -> tuple[float, float]:
        return (self.lon0 + x / self._kx, self.lat0 + y / self._ky)


def bearing_to_unit(bearing_deg: float) -> tuple[float, float]:
    """Compass bearing (0 = north, 90 = east) to a unit vector (east, north)."""
    b = math.radians(bearing_deg)
    return (math.sin(b), math.cos(b))


def haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))
