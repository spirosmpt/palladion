"""Building exposure: when the front reaches each building footprint.

Arrival is taken at the footprint's centroid. This counts buildings the reconstructed front passes;
it is not a damage model (many buildings inside a fire perimeter survive, some outside burn from
embers). The S2 source reports about 4,000 homes burned, which gives a sanity check.
"""
import math
from typing import Any

import numpy as np
from shapely.geometry import shape

from palladion.engines.fire.model import FireEngine


class BuildingLayer:
    def __init__(self, fc: dict[str, Any], fire: FireEngine):
        self.fire = fire
        self.features = [f for f in fc.get("features", []) if f.get("geometry")]
        cents = [shape(f["geometry"]).centroid for f in self.features]
        lon = np.array([c.x for c in cents])
        lat = np.array([c.y for c in cents])
        self.lon, self.lat = lon, lat
        self.arrival_s = fire.arrival_seconds(lon, lat) if len(cents) else np.zeros(0)
        self._sorted = np.sort(self.arrival_s[np.isfinite(self.arrival_s)])

    def __len__(self) -> int:
        return len(self.features)

    def reached_by(self, seconds_after_ignition: float) -> int:
        """Buildings the front has reached at the given time."""
        return int(np.searchsorted(self._sorted, seconds_after_ignition, side="right"))

    def to_geojson(self) -> dict[str, Any]:
        t0 = self.fire.t0.timestamp()
        out = []
        for f, s in zip(self.features, self.arrival_s, strict=True):
            p = f.get("properties") or {}
            out.append({"type": "Feature", "geometry": f["geometry"], "properties": {
                "id": p.get("id"), "height": p.get("height", 6.0),
                "fire_epoch": round(t0 + float(s)) if math.isfinite(s) else -1,
            }})
        return {"type": "FeatureCollection", "features": out}
