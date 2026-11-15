"""Place lookup for the location step: streets, areas and landmarks from OpenStreetMap.

Names are compared in a phonetic Latin form so that Greek, Greeklish and English spellings meet:
"Κυανής Ακτής", "kyanis aktis" and "Kianis Aktis" all become "kianis aktis". Matching is fuzzy on
that form. The same street name often exists in several areas (there is a Κολοκοτρώνη in Νέος
Βουτζάς, Ραφήνα and Νέα Μάκρη), so each name is split into one entry per area, and the caller
must narrow by area or ask.
"""
import json
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from shapely.geometry import Point, shape
from shapely.ops import unary_union

from palladion.geo.proj import haversine_m

GREEK_TO_LATIN = {
    "α": "a", "β": "v", "γ": "g", "δ": "d", "ε": "e", "ζ": "z", "η": "i", "θ": "th", "ι": "i", "κ": "k",
    "λ": "l", "μ": "m", "ν": "n", "ξ": "ks", "ο": "o", "π": "p", "ρ": "r", "σ": "s", "ς": "s", "τ": "t",
    "υ": "i", "φ": "f", "χ": "x", "ψ": "ps", "ω": "o",
}
GREEK_DIGRAPHS = [("ου", "u"), ("αυ", "av"), ("ευ", "ev"), ("μπ", "b"), ("ντ", "d"), ("γκ", "g"), ("γγ", "g")]
# Applied to the Latin form of both sides, in order.
FOLDS = [
    ("ch", "x"), ("ph", "f"), ("8", "th"), ("w", "o"), ("y", "i"), ("h", "i"),
    ("ou", "u"), ("ai", "e"), ("ei", "i"), ("oi", "i"), ("ui", "i"), ("ks", "x"), ("ps", "ps"),
    ("mp", "b"), ("nt", "d"), ("gk", "g"), ("gg", "g"),
]
STOPWORDS = {"odos", "odu", "odo", "street", "st", "leoforos", "leof", "avenue", "ave", "plateia", "plat",
             "sti", "stin", "stis", "sto", "ston", "tu", "tis", "ta", "to", "ti", "i", "o"}


def phonetic(text: str) -> str:
    """Greek, Greeklish or English to one comparable Latin form."""
    t = unicodedata.normalize("NFD", text.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    for a, b in GREEK_DIGRAPHS:              # before single letters: ου is "u", not "o" + "υ"
        t = t.replace(a, b)
    t = "".join(GREEK_TO_LATIN.get(c, c) for c in t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    for a, b in FOLDS:
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)          # collapse doubled letters
    return " ".join(w for w in t.split() if w not in STOPWORDS)


def similarity(a: str, b: str) -> float:
    """Fuzzy match on the phonetic form. Also accepts a mention that is a whole-word prefix of the name
    ("kolokotroni" for "Θεοδώρου Κολοκοτρώνη") or of a known Greek genitive/nominative pair."""
    pa, pb = phonetic(a), phonetic(b)
    if not pa or not pb:
        return 0.0
    if pa == pb:
        return 1.0
    score = SequenceMatcher(None, pa, pb).ratio()
    wa, wb = pa.split(), pb.split()
    short, long_ = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    # Every word of the shorter name matches a word of the longer one, allowing Greek case endings
    # (Κολοκοτρώνης / Κολοκοτρώνη, Βουτζάς / Βουτζά): "voutza" finds "Νέος Βουτζάς".
    # A shorter mention that matches the last word of the longer name, the distinctive one, may differ in its
    # case ending ("kolokotroni" finds "Θεοδώρου Κολοκοτρώνη"). One that matches only an earlier word must match
    # it exactly, give or take a final "s" ("Ρήγα" finds "Ρήγα Φεραίου"): "Vasileiou" must not find
    # "Βασίλη Λογοθετίδη" through a first name that only begins the same.
    strict = len(short) < len(long_) and not any(_same_stem(x, long_[-1]) for x in short)
    match = (lambda x, y: x == y or x == y + "s" or y == x + "s") if strict else _same_stem
    if short and all(any(match(x, y) for y in long_) for x in short):
        score = max(score, 0.92 if len(short) == len(long_) else 0.86)
    return score


def _same_stem(x: str, y: str) -> bool:
    if x == y:
        return True
    if min(len(x), len(y)) == 3:                 # short words: "neo" / "neos", "agi" / "agia"
        return (x.startswith(y) or y.startswith(x)) and abs(len(x) - len(y)) <= 1
    if min(len(x), len(y)) < 3:
        return False
    k = max(4, min(len(x), len(y)) - 2)
    return x[:k] == y[:k] and abs(len(x) - len(y)) <= 3


@dataclass
class Entry:
    name: str
    kind: str                 # street | area | landmark
    area: str | None          # the area a street stretch belongs to
    lon: float
    lat: float
    extent_m: float           # rough size: how far the true spot may be from (lon, lat)
    source: str


@dataclass
class Candidate:
    entry: Entry
    score: float


AREA_KINDS = ("town", "village", "suburb", "neighbourhood", "hamlet", "locality")


class Gazetteer:
    def __init__(self, entries: list[Entry]):
        self.entries = entries
        self.areas = [e for e in entries if e.kind == "area"]

    @classmethod
    def from_processed(cls, data_dir: Path, extra_areas: dict[str, tuple[float, float]] | None = None,
                       outside_areas: dict[str, tuple[float, float]] | None = None) -> "Gazetteer":
        entries: list[Entry] = []

        def load(name: str) -> dict[str, Any] | None:
            p = data_dir / name
            return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

        places = load("places.geojson") or {"features": []}
        for f in places["features"]:
            p = f["properties"]
            if p.get("kind") in AREA_KINDS and p.get("name"):
                lon, lat = f["geometry"]["coordinates"]
                entries.append(Entry(p["name"], "area", None, lon, lat, 1500.0, "osm_place"))
                for alt in [p.get("name_en"), *p.get("alt_names", [])]:
                    if alt:
                        entries.append(Entry(alt, "area", p["name"], lon, lat, 1500.0, "osm_place_alt"))
        for name, (lon, lat) in (extra_areas or {}).items():
            entries.append(Entry(name, "area", None, lon, lat, 1200.0, "scenario"))
        # Well-known places outside the scenario, so "Παγκράτι, Αθήνα" is out of area, not a fuzzy local match.
        for name, (lon, lat) in (outside_areas or {}).items():
            entries.append(Entry(name, "area", None, lon, lat, 3000.0, "outside"))
        areas = [e for e in entries if e.kind == "area" and e.area is None and e.source != "outside"]

        zones = load("zones.geojson") or {"features": []}
        for f in zones["features"]:
            p = f["properties"]
            lon, lat = f["geometry"]["coordinates"]
            if p.get("name"):
                entries.append(Entry(p["name"], "landmark", _nearest_area(areas, lon, lat), lon, lat,
                                     300.0, f"osm_{p.get('kind', 'zone')}"))
            elif p.get("kind") in ("beach", "port"):
                # Unnamed beaches cannot be searched by name, but "the beach in Mati" can land on one.
                entries.append(Entry("", "landmark", _nearest_area(areas, lon, lat), lon, lat, 300.0,
                                     f"osm_{p['kind']}"))

        roads = load("roads.geojson") or {"features": []}
        by_name: dict[str, list[Any]] = {}
        for f in roads["features"]:
            n = f["properties"].get("name")
            if n:
                by_name.setdefault(n, []).append(shape(f["geometry"]))
        for name, geoms in by_name.items():
            groups: dict[str | None, list[Any]] = {}
            for g in geoms:
                c = g.centroid
                groups.setdefault(_nearest_area(areas, c.x, c.y), []).append(g)
            for area, gs in groups.items():
                u = unary_union(gs)
                mid = u.interpolate(0.5, normalized=True) if u.geom_type == "LineString" else u.representative_point()
                minx, miny, maxx, maxy = u.bounds
                extent = haversine_m(minx, miny, maxx, maxy) / 2
                entries.append(Entry(name, "street", area, mid.x, mid.y, max(100.0, extent), "osm_road"))
        return cls(entries)

    # -- lookups ------------------------------------------------------------------------------------
    def find_area(self, text: str, min_score: float = 0.8) -> Entry | None:
        best = max(((similarity(text, e.name), e) for e in self.areas), default=(0.0, None), key=lambda x: x[0])
        if best[1] is None or best[0] < min_score:
            return None
        e = best[1]
        return next((a for a in self.areas if a.name == e.area), e) if e.area else e

    def find(self, text: str, kind: str | None = None, area: str | None = None,
             min_score: float = 0.82, limit: int = 8) -> list[Candidate]:
        out = []
        for e in self.entries:
            if not e.name or (kind and e.kind != kind):
                continue
            if area and e.kind != "area" and e.area != area:
                continue
            sc = similarity(text, e.name)
            if sc >= min_score:
                out.append(Candidate(e, sc))
        out.sort(key=lambda c: (-c.score, c.entry.extent_m))
        return out[:limit]


def _nearest_area(areas: list[Entry], lon: float, lat: float) -> str | None:
    if not areas:
        return None
    p = Point(lon, lat)
    return min(areas, key=lambda a: p.distance(Point(a.lon, a.lat))).name
