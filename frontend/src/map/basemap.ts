/**
 * Basemap: OpenFreeMap vector tiles (OpenMapTiles schema, no API key) with our own quiet style.
 *
 * The basemap stays in the background: muted land, readable Greek place names, and vegetation
 * shown faintly because fuel matters in a wildfire. Colour is kept for the operational layers.
 * If the vector tiles cannot load, MapView switches to the OSM raster fallback layer.
 */
import type { ExpressionSpecification, LayerSpecification, StyleSpecification } from "maplibre-gl";

const OFM = "https://tiles.openfreemap.org";

export const base = {
  land: "#eef0ed",
  water: "#bcd2da",
  waterLine: "#a7c3cd",
  wood: "#d6e0cb",
  grass: "#e3e9d9",
  sand: "#efe8d7",
  built: "#e7e7e3",
  building: "#dad9d4",
  buildingLine: "#c9c7c0",
  roadCasing: "#cfd3d6",
  road: "#ffffff",
  roadMajor: "#fbf7ee",
  label: "#3c4955",
  labelMinor: "#6a7782",
  halo: "rgba(255,255,255,0.9)",
};

/** Greek name first, then the local name, then English. */
const NAME: ExpressionSpecification = ["coalesce", ["get", "name:el"], ["get", "name"], ["get", "name_en"]];

const roadWidth = (major: number, minor: number): ExpressionSpecification => [
  "interpolate", ["exponential", 1.6], ["zoom"],
  11, ["match", ["get", "class"], ["motorway", "trunk", "primary"], major * 0.4, minor * 0.2],
  15, ["match", ["get", "class"], ["motorway", "trunk", "primary"], major * 2.2, ["secondary", "tertiary"], major * 1.6, minor * 1.4],
  18, ["match", ["get", "class"], ["motorway", "trunk", "primary"], major * 7, ["secondary", "tertiary"], major * 5, minor * 4],
];

/**
 * Elevation: AWS Open Data terrain tiles (Terrarium encoding, public, no key), up to zoom 15.
 * Two sources on purpose: MapLibre renders hillshade and 3D terrain better from separate sources.
 */
const DEM_TILES = ["https://s3.amazonaws.com/elevation-tiles-prod/terrarium/{z}/{x}/{y}.png"];
const DEM_ATTRIBUTION = "Elevation: Mapzen Terrain Tiles (AWS Open Data)";
const demSource = () => ({
  type: "raster-dem" as const, tiles: DEM_TILES, tileSize: 256, maxzoom: 15, encoding: "terrarium" as const,
  attribution: DEM_ATTRIBUTION,
});
export const TERRAIN_SOURCE = "dem-terrain";
export const TERRAIN_EXAGGERATION = 1.4;

const HILLSHADE: LayerSpecification = {
  id: "hillshade", type: "hillshade", source: "dem-hillshade",
  paint: {
    "hillshade-shadow-color": "#5b6670",
    "hillshade-highlight-color": "#ffffff",
    "hillshade-accent-color": "#4a5560",
    "hillshade-exaggeration": 0.45,
    "hillshade-illumination-direction": 315,
  },
};

const ROAD_CLASSES = ["motorway", "trunk", "primary", "secondary", "tertiary", "minor", "service", "track"];

const layers: LayerSpecification[] = [
  { id: "bg", type: "background", paint: { "background-color": base.land } },

  // Fallback raster, hidden unless the vector tiles fail.
  { id: "osm-raster", type: "raster", source: "osm", layout: { visibility: "none" },
    paint: { "raster-saturation": -0.65, "raster-contrast": -0.1 } },

  { id: "landuse-built", type: "fill", source: "omt", "source-layer": "landuse",
    filter: ["in", ["get", "class"], ["literal", ["residential", "suburb", "neighbourhood", "commercial", "industrial", "retail"]]],
    paint: { "fill-color": base.built } },
  { id: "landcover-wood", type: "fill", source: "omt", "source-layer": "landcover",
    filter: ["==", ["get", "class"], "wood"],
    paint: { "fill-color": base.wood, "fill-opacity": 0.9 } },
  { id: "landcover-grass", type: "fill", source: "omt", "source-layer": "landcover",
    filter: ["in", ["get", "class"], ["literal", ["grass", "farmland", "scrub"]]],
    paint: { "fill-color": base.grass } },
  { id: "landcover-sand", type: "fill", source: "omt", "source-layer": "landcover",
    filter: ["==", ["get", "class"], "sand"],
    paint: { "fill-color": base.sand } },
  { id: "park", type: "fill", source: "omt", "source-layer": "park",
    paint: { "fill-color": base.grass, "fill-opacity": 0.6 } },

  HILLSHADE,

  { id: "water", type: "fill", source: "omt", "source-layer": "water",
    paint: { "fill-color": base.water } },
  { id: "water-edge", type: "line", source: "omt", "source-layer": "water",
    paint: { "line-color": base.waterLine, "line-width": 0.8 } },
  { id: "waterway", type: "line", source: "omt", "source-layer": "waterway",
    paint: { "line-color": base.waterLine, "line-width": ["interpolate", ["linear"], ["zoom"], 12, 0.5, 16, 1.5] } },

  { id: "building", type: "fill", source: "omt", "source-layer": "building", minzoom: 14,
    paint: { "fill-color": base.building, "fill-outline-color": base.buildingLine } },

  { id: "road-casing", type: "line", source: "omt", "source-layer": "transportation",
    filter: ["in", ["get", "class"], ["literal", ROAD_CLASSES]],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: { "line-color": base.roadCasing, "line-width": roadWidth(1.6, 1.3), "line-gap-width": 0 } },
  { id: "road", type: "line", source: "omt", "source-layer": "transportation",
    filter: ["in", ["get", "class"], ["literal", ROAD_CLASSES]],
    layout: { "line-cap": "round", "line-join": "round" },
    paint: {
      "line-color": ["match", ["get", "class"], ["motorway", "trunk", "primary"], base.roadMajor, base.road],
      "line-width": roadWidth(1.1, 0.9),
    } },

  // Labels. MapView inserts the operational layers below "label-road" so names stay readable.
  { id: "label-road", type: "symbol", source: "omt", "source-layer": "transportation_name", minzoom: 14,
    layout: {
      "symbol-placement": "line", "text-field": NAME, "text-font": ["Noto Sans Regular"],
      "text-size": ["interpolate", ["linear"], ["zoom"], 14, 10, 17, 13],
    },
    paint: { "text-color": base.labelMinor, "text-halo-color": base.halo, "text-halo-width": 1.2 } },
  { id: "label-water", type: "symbol", source: "omt", "source-layer": "water_name",
    layout: { "text-field": NAME, "text-font": ["Noto Sans Italic"], "text-size": 12 },
    paint: { "text-color": "#5d7f8c", "text-halo-color": base.halo, "text-halo-width": 1 } },
  { id: "label-place-minor", type: "symbol", source: "omt", "source-layer": "place", minzoom: 13,
    filter: ["in", ["get", "class"], ["literal", ["neighbourhood", "hamlet", "locality", "isolated_dwelling"]]],
    layout: { "text-field": NAME, "text-font": ["Noto Sans Regular"], "text-size": 11 },
    paint: { "text-color": base.labelMinor, "text-halo-color": base.halo, "text-halo-width": 1.4 } },
  { id: "label-place", type: "symbol", source: "omt", "source-layer": "place",
    filter: ["in", ["get", "class"], ["literal", ["city", "town", "village", "suburb"]]],
    layout: {
      "text-field": NAME, "text-font": ["Noto Sans Bold"],
      "text-size": ["match", ["get", "class"], ["city", "town"], 15, 13],
    },
    paint: { "text-color": base.label, "text-halo-color": base.halo, "text-halo-width": 1.6 } },
];

export const FIRST_LABEL_LAYER = "label-road";

/** True when OpenFreeMap answers quickly; otherwise the map starts on the raster fallback. */
export async function vectorTilesAvailable(timeoutMs = 4000): Promise<boolean> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(`${OFM}/planet`, { signal: ctrl.signal });
    return r.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export function baseStyle(vector: boolean): StyleSpecification {
  const osm = {
    type: "raster" as const, tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"], tileSize: 256, maxzoom: 19,
    attribution: "© OpenStreetMap contributors",
  };
  if (!vector) {
    return {
      version: 8,
      sources: { osm, "dem-hillshade": demSource(), [TERRAIN_SOURCE]: demSource() },
      layers: [layers[0], { ...layers[1], layout: { visibility: "visible" } } as LayerSpecification, HILLSHADE],
    };
  }
  return {
    version: 8,
    glyphs: `${OFM}/fonts/{fontstack}/{range}.pbf`,
    sources: {
      omt: { type: "vector", url: `${OFM}/planet`, attribution: "© OpenFreeMap © OpenMapTiles © OpenStreetMap contributors" },
      osm,
      "dem-hillshade": demSource(),
      [TERRAIN_SOURCE]: demSource(),
    },
    layers,
  };
}

/** Show the raster fallback and hide the vector layers (used if vector tiles start failing). */
export function showRasterFallback(map: { getLayer(id: string): unknown; setLayoutProperty(id: string, k: string, v: string): void }) {
  for (const l of layers) {
    if (!map.getLayer(l.id) || l.id === "bg" || l.id === "hillshade") continue;
    map.setLayoutProperty(l.id, "visibility", l.id === "osm-raster" ? "visible" : "none");
  }
}
