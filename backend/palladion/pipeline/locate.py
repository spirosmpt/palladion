"""Turn the places the intake agent quoted into one point on the map, or say why it cannot.

Pure code over the gazetteer, so it is cheap, testable and never invents a place:
  resolved     one street (or a corner of two) in a known area
  approximate  only an area, beach or coast is known
  ambiguous    a street name that exists in several areas and nothing narrows it down
  out_of_area  the place is known but outside the scenario
  none         nothing usable
"""
import re
from dataclasses import dataclass, field
from itertools import product

from palladion.agents.intake import IntakeOutput, MessageKind, PlaceKind, PlaceRole, Situation
from palladion.geo.gazetteer import Candidate, Entry, Gazetteer
from palladion.geo.proj import haversine_m

AREA_RADIUS_M = 2500.0
CORNER_MAX_M = 350.0
COAST_RADIUS_M = 2500.0


@dataclass
class Location:
    status: str
    lon: float | None = None
    lat: float | None = None
    uncertainty_m: float | None = None
    label: str = ""
    area: str | None = None
    candidates: list[str] = field(default_factory=list)
    question_el: str | None = None
    flags: list[str] = field(default_factory=list)


# "Ενιπέως ή Αριάδνης", "either X or Y": two streets that are alternatives, not a corner.
ALTERNATIVE = re.compile(r"(?:^|[\s,])(?:ή|είτε|or|either|eite)(?=[\s,]|$)", re.IGNORECASE)
UNSURE = re.compile(r"δεν ξέρω αν|δεν είμαι σίγουρ|den ks?er[ow] an|not sure (?:if|whether)", re.IGNORECASE)


def _alternatives(text: str, q0: str, q1: str) -> bool:
    """The message offers the two places as either-or. Read between the two quotes when both are found."""
    if not text:
        return False
    low = text.lower()
    i, j = low.find(q0.lower()), low.find(q1.lower())
    if i >= 0 and j >= 0 and i != j:
        return bool(ALTERNATIVE.search(text[min(i, j):max(i, j)]))
    return bool(ALTERNATIVE.search(text) and UNSURE.search(text))


IN_AREA = (" στο ", " στη ", " στην ", " στον ", " sto ", " sti ", " stin ", " in ", ", ")


def _split_in_area(name: str) -> tuple[str, str | None]:
    low = f" {name.lower()} "
    for sep in IN_AREA:
        i = low.find(sep, 1)
        if 0 < i < len(low) - len(sep):
            head, tail = name[: i - 1].strip(), name[i - 1 + len(sep):].strip()
            if head and tail:
                return head, tail
    return name, None


def _strip_number(name: str) -> str:
    import re
    return re.sub(r"\s+\d+[α-ωa-z]?$", "", name.strip()) or name


def _in_bbox(bbox: tuple[float, float, float, float], lon: float, lat: float) -> bool:
    w, s, e, n = bbox
    return w <= lon <= e and s <= lat <= n


def _near(c: Candidate, area: Entry, radius: float) -> bool:
    return haversine_m(c.entry.lon, c.entry.lat, area.lon, area.lat) <= radius


def locate(out: IntakeOutput, gaz: Gazetteer, bbox: tuple[float, float, float, float], text: str = "") -> Location:
    """`text`: the message, read only to tell "A ή B" (either-or) from a corner of A and B."""
    # Only places where the people are; where the fire is or where they went says nothing about that.
    mine = [p for p in out.places if p.role in (PlaceRole.person, PlaceRole.other)]
    if out.kind == MessageKind.safety_report:
        # "We reached Rafina from Mati, we are fine": they are where they went, not where they left.
        went = [p for p in out.places if p.role == PlaceRole.destination]
        if went:
            mine = went
    street_names = [p.name for p in mine if p.kind == PlaceKind.street]
    other_names = [p.name for p in mine if p.kind != PlaceKind.street]
    # "Ποσειδώνος στο Μάτι" -> street "Ποσειδώνος" + area "Μάτι".
    for i, n in enumerate(street_names):
        head, tail = _split_in_area(n)
        if tail:
            street_names[i] = head
            other_names.append(tail)
    # "Ποσειδώνος 32" is the street Ποσειδώνος; the same street mentioned twice is not a corner.
    street_names = list(dict.fromkeys(_strip_number(n) for n in street_names))
    quotes: dict[str, str] = {}
    for p in mine:
        if p.kind == PlaceKind.street:
            quotes.setdefault(_strip_number(_split_in_area(p.name)[0]), p.quote or p.name)

    area: Entry | None = None
    unknown_places: list[str] = []
    for n in other_names:
        a = gaz.find_area(n)
        if a is None:
            hits = gaz.find(n, kind="landmark")
            if hits:
                a = Entry(hits[0].entry.area or hits[0].entry.name, "area", None, hits[0].entry.lon,
                          hits[0].entry.lat, hits[0].entry.extent_m, "landmark")
        if a is not None:
            area = area or a
        elif gaz.find(n, kind="street"):
            street_names.append(n)        # the model called a street an area or a landmark
        else:
            unknown_places.append(n)
    # Areas where the sender sees the fire: a weak hint, used only to choose between same-name streets.
    fire_areas = [a for a in (gaz.find_area(p.name) for p in out.places if p.role == PlaceRole.fire) if a]

    if area is not None and not _in_bbox(bbox, area.lon, area.lat):
        return Location("out_of_area", area.lon, area.lat, area.extent_m, area.name, area.name)

    # Streets: candidates per mention, narrowed by the area when there is one.
    per_mention: list[tuple[str, list[Candidate]]] = []
    for name in street_names:
        cands = [c for c in gaz.find(name, kind="street") if _in_bbox(bbox, c.entry.lon, c.entry.lat)]
        if area is not None:
            same = [c for c in cands if c.entry.area == area.name]
            cands = same or [c for c in cands if _near(c, area, AREA_RADIUS_M)]
        if cands:
            best = cands[0].score
            cands = [c for c in cands if c.score >= best - 0.05]
            per_mention.append((name, cands))

    if len(per_mention) >= 2:   # a corner: the closest pair of stretches
        (_, a_c), (_, b_c) = per_mention[0], per_mention[1]
        pair = min(product(a_c, b_c), key=lambda ab: haversine_m(ab[0].entry.lon, ab[0].entry.lat,
                                                                 ab[1].entry.lon, ab[1].entry.lat))
        d = haversine_m(pair[0].entry.lon, pair[0].entry.lat, pair[1].entry.lon, pair[1].entry.lat)
        (n0, _), (n1, _) = per_mention[0], per_mention[1]
        if _alternatives(text, quotes.get(n0, n0), quotes.get(n1, n1)) and d > CORNER_MAX_M:
            # They do not know which of two places they are at, far apart: a route from the wrong one could
            # lead into the fire. Ask; never average two places into a point between them.
            a, b = pair[0].entry, pair[1].entry
            return Location("ambiguous", label=f"{a.name} ή {b.name}",
                            candidates=[f"{a.name}, {a.area}", f"{b.name}, {b.area}"],
                            question_el=(f"Είστε στην οδό {a.name} ή στην οδό {b.name}; Τι βλέπετε γύρω σας "
                                         "(πινακίδα, μαγαζί, εκκλησία, θάλασσα);"),
                            flags=["either_or"])
        if d <= CORNER_MAX_M * 3:
            lon = (pair[0].entry.lon + pair[1].entry.lon) / 2
            lat = (pair[0].entry.lat + pair[1].entry.lat) / 2
            return Location("resolved", lon, lat, max(150.0, d / 2),
                            f"{pair[0].entry.name} & {pair[1].entry.name}", pair[0].entry.area)

    if per_mention:
        name, cands = per_mention[0]
        areas = sorted({c.entry.area or "?" for c in cands})
        if area is not None and len(cands) > 1:
            # The caller named the area: take the stretch closest to it.
            c = min(cands, key=lambda c: haversine_m(c.entry.lon, c.entry.lat, area.lon, area.lat))
            return Location("resolved", c.entry.lon, c.entry.lat, c.entry.extent_m,
                            f"{c.entry.name}, {area.name}", area.name)
        if len(cands) == 1 or len(areas) == 1:
            c = min(cands, key=lambda c: c.entry.extent_m)
            return Location("resolved", c.entry.lon, c.entry.lat, c.entry.extent_m,
                            f"{c.entry.name}, {c.entry.area}", c.entry.area)
        near_fire = [c for c in cands if any(_near(c, fa, AREA_RADIUS_M) for fa in fire_areas)]
        if near_fire:
            fa = fire_areas[0]
            c = min(near_fire, key=lambda c: haversine_m(c.entry.lon, c.entry.lat, fa.lon, fa.lat))
            return Location("resolved", c.entry.lon, c.entry.lat, c.entry.extent_m,
                            f"{c.entry.name}, {c.entry.area}", c.entry.area,
                            question_el=f"Επιβεβαίωση: η οδός {name} στην περιοχή {c.entry.area};",
                            flags=["area_inferred"])
        return Location("ambiguous", label=name, candidates=[f"{name}, {a}" for a in areas],
                        question_el=f"Σε ποια περιοχή είναι η οδός {name}; ({', '.join(areas[:4])})")

    if area is not None:
        # People at a beach or in the water: the nearest beach/port of that area is a better guess.
        if out.situation and out.situation.kind in (Situation.beach, Situation.sea) or any(
                p.kind == PlaceKind.coast for p in out.places):
            near = [e for e in gaz.entries if e.kind == "landmark" and e.source in ("osm_beach", "osm_port")
                    and haversine_m(e.lon, e.lat, area.lon, area.lat) <= COAST_RADIUS_M]
            # The beach of this area, not the next settlement's beach that happens to be close.
            # A beach named after another settlement (Κόκκινο Λιμανάκι) is that settlement's, not this one's.
            elsewhere = {a.name for a in gaz.areas if a.name != area.name}
            own = [e for e in near if e.area == area.name and e.name not in elsewhere]
            coast = own or near
            if coast:
                z = min(coast, key=lambda e: haversine_m(e.lon, e.lat, area.lon, area.lat))
                return Location("approximate", z.lon, z.lat, 900.0, f"ακτή, {area.name}", area.name,
                                question_el="Σε ποιο σημείο της ακτής είστε; Υπάρχει κάτι γνωστό κοντά;")
        return Location("approximate", area.lon, area.lat, area.extent_m, area.name, area.name,
                        question_el=f"Ποια είναι η ακριβής διεύθυνση στην περιοχή {area.name};")

    if unknown_places:
        return Location("none", label=", ".join(unknown_places),
                        question_el="Πού ακριβώς βρίσκεστε; Οδός και περιοχή.")
    return Location("none", question_el="Πού ακριβώς βρίσκεστε; Οδός και περιοχή.")
