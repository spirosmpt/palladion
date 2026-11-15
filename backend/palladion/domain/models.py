"""Domain models shared by engines, API and (later) agents."""
from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LocalizedText(BaseModel):
    el: str
    en: str


class Confidence(StrEnum):
    documented = "documented"        # stated in a cited source
    derived = "derived"              # computed from documented values
    approximate = "approximate"      # placed by us, needs checking against data


class Source(BaseModel):
    id: str
    title: str
    url: str


class FireAnchor(BaseModel):
    """A place and time the fire front is known (or estimated) to have reached."""
    id: str
    label: LocalizedText
    lon: float
    lat: float
    time: datetime
    confidence: Confidence
    source_ids: list[str] = Field(default_factory=list)
    note: str = ""

    @field_validator("time")
    @classmethod
    def _aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("anchor times must carry a UTC offset")
        return v


class FireSpec(BaseModel):
    model: Literal["anchored_ellipse"] = "anchored_ellipse"
    ignition: FireAnchor
    spread_bearing_deg: float = Field(ge=0, lt=360)
    length_to_breadth: float = Field(ge=1.0, le=8.0)
    anchors: list[FireAnchor]
    safety_margin_min: float = 10.0
    post_head_spread_min: float = 55.0
    note: str = ""


class WindObservation(BaseModel):
    time_from: datetime
    time_to: datetime
    station: str
    from_bearing_deg: float
    speed_kmh_min: float
    speed_kmh_max: float
    gust_kmh_max: float | None = None
    source_ids: list[str] = Field(default_factory=list)


class ZoneKind(StrEnum):
    port = "port"
    beach = "beach"
    open_space = "open_space"
    assembly = "assembly"


class SafeZone(BaseModel):
    id: str
    name: str
    kind: ZoneKind
    lon: float
    lat: float
    capacity_estimate: int | None = None
    confidence: Confidence = Confidence.approximate
    note: str = ""


class CopernicusSpec(BaseModel):
    activation: str
    delineation_zip: str
    grading_zip: str | None = None
    source_id: str


class Station(BaseModel):
    name: str
    lon: float
    lat: float


class UnitKind(StrEnum):
    fire_engine = "fire_engine"
    ambulance = "ambulance"


class UnitGroup(BaseModel):
    """Land units of one kind at one base (Fire Service engines, EKAB ambulances)."""
    id: str
    prefix: str                       # unit ids are prefix-01, prefix-02, ...
    name: LocalizedText
    kind: UnitKind
    base: Station
    count: int
    ready_min: float = 2.0            # from the order to leaving the base
    on_scene_min: float = 15.0        # time at the place before the unit is free again


class GroundUnitsSpec(BaseModel):
    groups: list[UnitGroup] = Field(default_factory=list)
    note: LocalizedText | None = None


class ScenarioSpec(BaseModel):
    id: str
    name: LocalizedText
    summary: LocalizedText
    timezone: str
    start: datetime
    end: datetime
    default_speed: float = 60.0
    center: tuple[float, float]
    bbox: tuple[float, float, float, float]
    detail_bbox: tuple[float, float, float, float] | None = None
    fire: FireSpec
    wind: list[WindObservation] = Field(default_factory=list)
    fallback_coastline: list[tuple[float, float]] = Field(default_factory=list)
    fallback_zones: list[SafeZone] = Field(default_factory=list)
    copernicus: CopernicusSpec | None = None
    # Local names people use for places OSM does not tag as a settlement (lon, lat).
    extra_areas: dict[str, tuple[float, float]] = Field(default_factory=dict)
    # Well-known places outside the scenario area (lon, lat): messages from there are out of area.
    outside_areas: dict[str, tuple[float, float]] = Field(default_factory=dict)
    # Public names for settlements whose OSM name is an institution or code, used in 112 warnings.
    area_display_names: dict[str, str] = Field(default_factory=dict)
    sources: list[Source]
    ground_units: GroundUnitsSpec | None = None
    disclaimer: LocalizedText


class RoadStatus(StrEnum):
    open = "open"
    threatened = "threatened"   # front arrives within the warning window
    burned = "burned"
