"""Elevation from Mapzen/Tilezen Terrain Tiles (terrarium PNG, open data on AWS).

Each pixel stores height as (R * 256 + G + B / 256) - 32768 metres. At zoom 15 a pixel is about 3.8 m
at the latitude of Mati, but the underlying sources in Greece are coarser (SRTM / EU-DEM, ~25-30 m),
so the grid shows slopes and coastal bluffs, not every rock. Pure functions here; fetch.py downloads.
"""
import io
import math
from pathlib import Path

import numpy as np

TILE_URL = "https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"
ZOOM = 15
TILE = 256


def lonlat_to_tile(lon: float, lat: float, z: int) -> tuple[float, float]:
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    y = (1.0 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2.0 * n
    return x, y


def tile_range(bbox: tuple[float, float, float, float], z: int = ZOOM) -> tuple[range, range]:
    w, s, e, n = bbox
    x0, y0 = lonlat_to_tile(w, n, z)
    x1, y1 = lonlat_to_tile(e, s, z)
    return range(int(x0), int(x1) + 1), range(int(y0), int(y1) + 1)


def decode_terrarium(png: bytes) -> np.ndarray:
    from PIL import Image
    rgb = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=np.float64)
    return (rgb[..., 0] * 256 + rgb[..., 1] + rgb[..., 2] / 256) - 32768


class Dem:
    """Elevation grid in Web Mercator pixels at one zoom; sampled bilinearly in lon/lat."""

    def __init__(self, elev: np.ndarray, x0: int, y0: int, z: int = ZOOM):
        self.elev, self.x0, self.y0, self.z = elev.astype(np.float32), x0, y0, z

    @classmethod
    def from_tiles(cls, tiles: dict[tuple[int, int], np.ndarray], z: int = ZOOM) -> "Dem":
        xs = sorted({x for x, _ in tiles})
        ys = sorted({y for _, y in tiles})
        grid = np.full((len(ys) * TILE, len(xs) * TILE), np.nan, dtype=np.float32)
        for (x, y), a in tiles.items():
            i, j = ys.index(y), xs.index(x)
            grid[i * TILE:(i + 1) * TILE, j * TILE:(j + 1) * TILE] = a
        return cls(grid, xs[0], ys[0], z)

    def save(self, path: Path) -> None:
        np.savez_compressed(path, elev=self.elev, x0=self.x0, y0=self.y0, z=self.z)

    @classmethod
    def load(cls, path: Path) -> "Dem | None":
        if not path.exists():
            return None
        d = np.load(path)
        return cls(d["elev"], int(d["x0"]), int(d["y0"]), int(d["z"]))

    def sample(self, lon: np.ndarray, lat: np.ndarray) -> np.ndarray:
        """Elevation in metres; NaN outside the grid."""
        lon, lat = np.asarray(lon, dtype=float), np.asarray(lat, dtype=float)
        n = 2 ** self.z
        px = ((lon + 180.0) / 360.0 * n - self.x0) * TILE - 0.5
        py = ((1.0 - np.arcsinh(np.tan(np.radians(lat))) / math.pi) / 2.0 * n - self.y0) * TILE - 0.5
        h, w = self.elev.shape
        i0 = np.floor(py).astype(int)
        j0 = np.floor(px).astype(int)
        ok = (i0 >= 0) & (j0 >= 0) & (i0 < h - 1) & (j0 < w - 1)
        out = np.full(lon.shape, np.nan)
        if not ok.any():
            return out
        i, j = i0[ok], j0[ok]
        fy, fx = py[ok] - i, px[ok] - j
        e = self.elev
        out[ok] = (e[i, j] * (1 - fx) * (1 - fy) + e[i, j + 1] * fx * (1 - fy)
                   + e[i + 1, j] * (1 - fx) * fy + e[i + 1, j + 1] * fx * fy)
        return out
