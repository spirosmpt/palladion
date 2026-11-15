import "maplibre-gl/dist/maplibre-gl.css";

import maplibregl, { type ExpressionSpecification, type GeoJSONSource } from "maplibre-gl";
import { useEffect, useRef, useState } from "react";

import { api, type ScenarioInfo } from "../api";
import { useUi } from "../store";
import { colors } from "../theme";
import { useAlerts } from "../alerts/AlertDesk";
import { useCases } from "../cases/CaseQueue";
import { useMissions } from "../missions/useMissions";
import { anchorPopup, zonePopup } from "./popups";
import {
  FIRST_LABEL_LAYER,
  TERRAIN_EXAGGERATION,
  TERRAIN_SOURCE,
  baseStyle,
  showRasterFallback,
  vectorTilesAvailable,
} from "./basemap";

const EMPTY: GeoJSON.FeatureCollection = { type: "FeatureCollection", features: [] };
const THREAT_WINDOW_S = 30 * 60;

function roadColor(nowEpoch: number): ExpressionSpecification {
  const fire: ExpressionSpecification = ["get", "fire_epoch"];
  return [
    "case",
    ["<", fire, 0], colors.roadOpen,
    ["<=", fire, nowEpoch], colors.roadBurned,
    ["<=", fire, nowEpoch + THREAT_WINDOW_S], colors.roadThreat,
    colors.roadOpen,
  ];
}

function buildingColor(nowEpoch: number): ExpressionSpecification {
  const fire: ExpressionSpecification = ["get", "fire_epoch"];
  return [
    "case",
    ["<", fire, 0], colors.building,
    ["<=", fire, nowEpoch], colors.buildingBurned,
    ["<=", fire, nowEpoch + THREAT_WINDOW_S], colors.buildingThreat,
    colors.building,
  ];
}

export function MapView({ scenario }: { scenario: ScenarioInfo }) {
  const el = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const ready = useRef(false);
  const clock = useUi((s) => s.clock);
  const point = useUi((s) => s.point);
  const route = useUi((s) => s.route);
  const setPoint = useUi((s) => s.setPoint);
  const lang = useUi((s) => s.lang);
  const [is3d, setIs3d] = useState(false);
  // Set once the layers exist: data that arrived earlier (a paused clock, no refetch change) is drawn then.
  const [loaded, setLoaded] = useState(false);

  // Build the map once.
  useEffect(() => {
    if (!el.current) return;
    const [w, s, e, n] = scenario.spec.bbox;
    let m: maplibregl.Map | null = null;
    let cancelled = false;

    void vectorTilesAvailable().then((vector) => {
      if (cancelled || !el.current) return;
      m = build(el.current, vector, [[w, s], [e, n]]);
    });

    return () => {
      cancelled = true;
      ready.current = false;
      setLoaded(false);
      m?.remove();
    };
  }, [scenario]);

  function build(container: HTMLDivElement, vector: boolean, bounds: [[number, number], [number, number]]) {
    const m = new maplibregl.Map({
      container,
      bounds,
      fitBoundsOptions: { padding: 20 },
      attributionControl: { compact: true },
      style: baseStyle(vector),
    });
    let fellBack = !vector;
    m.on("error", (ev) => {
      // Vector tiles failing mid-session: switch to the raster basemap once, keep everything else.
      if (!fellBack && (ev as { sourceId?: string }).sourceId === "omt") {
        fellBack = true;
        showRasterFallback(m);
      }
    });
    m.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), "top-left");
    m.addControl(new maplibregl.ScaleControl({ unit: "metric" }), "bottom-left");

    // "style.load", not "load": "load" waits for every tile, so a slow elevation server
    // would hold back the fire layers.
    m.once("style.load", async () => {
      // Operational layers go under the basemap labels so place and street names stay readable.
      const before = m.getLayer(FIRST_LABEL_LAYER) ? FIRST_LABEL_LAYER : undefined;
      const add = (layer: Parameters<maplibregl.Map["addLayer"]>[0]) => m.addLayer(layer, before);
      for (const id of ["front", "iso", "roads", "buildings", "perimeter", "zones", "anchors", "point", "route", "front-edge", "cases", "alerts", "coast", "units", "unit-routes", "plan-route"]) {
        m.addSource(id, { type: "geojson", data: EMPTY });
      }
      add({ id: "roads", type: "line", source: "roads",
        paint: {
          "line-color": colors.roadOpen,
          "line-width": ["match", ["get", "highway"], ["motorway", "trunk", "primary"], 2.6, ["secondary", "tertiary"], 1.8, 1],
          "line-opacity": 0.85,
        } });
      // Dead ends: the road keeps its colour (open, closing, burned) but is broken by white gaps,
      // so it can never be mistaken for a burned road.
      add({ id: "roads-deadend", type: "line", source: "roads", minzoom: 13,
        filter: ["==", ["get", "dead_end"], true],
        paint: { "line-color": "#ffffff", "line-width": 1.6, "line-dasharray": [1.2, 1.2] } });
      // Buildings from our own data (each with its own fire arrival time), extruded in 3D.
      add({ id: "buildings", type: "fill-extrusion", source: "buildings", minzoom: 13,
        paint: {
          "fill-extrusion-color": colors.building,
          "fill-extrusion-height": ["coalesce", ["get", "height"], 6],
          "fill-extrusion-base": 0,
          "fill-extrusion-opacity": 0.92,
        } });
      add({ id: "front-fill", type: "fill", source: "front",
        paint: { "fill-color": colors.burned, "fill-opacity": 0.32 } });
      // 112 warning areas: proposed = dashed outline, sent = tinted, selected = thicker.
      add({ id: "alerts-fill", type: "fill", source: "alerts",
        paint: { "fill-color": colors.alert, "fill-opacity": ["case", ["get", "sent"], 0.18, ["get", "selected"], 0.12, 0.04] } });
      // line-dasharray cannot depend on data, so proposed and sent outlines are two layers.
      add({ id: "alerts-line-proposed", type: "line", source: "alerts", filter: ["!", ["get", "sent"]],
        paint: { "line-color": colors.alert, "line-width": ["case", ["get", "selected"], 3, 1.6], "line-dasharray": [3, 2] } });
      add({ id: "alerts-line-sent", type: "line", source: "alerts", filter: ["get", "sent"],
        paint: { "line-color": colors.alert, "line-width": ["case", ["get", "selected"], 3, 1.8] } });
      add({ id: "iso", type: "line", source: "iso",
        paint: { "line-color": colors.burned, "line-width": 1.2, "line-opacity": 0.7, "line-dasharray": [3, 2] } });
      add({ id: "perimeter", type: "line", source: "perimeter",
        paint: { "line-color": colors.perimeter, "line-width": 1.6, "line-opacity": 0.75, "line-dasharray": [4, 2, 1, 2] } });
      add({ id: "front-line", type: "line", source: "front-edge",
        paint: { "line-color": colors.front, "line-width": 3 } });
      add({ id: "route-casing", type: "line", source: "route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": "#ffffff", "line-width": 8 } });
      add({ id: "route", type: "line", source: "route",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: { "line-color": colors.safe, "line-width": 4.5 } });
      add({ id: "coast-cliff", type: "line", source: "coast", minzoom: 12, filter: ["==", ["get", "kind"], "cliff"],
        layout: { "line-cap": "round" },
        paint: { "line-color": colors.cliff, "line-width": ["interpolate", ["linear"], ["zoom"], 12, 2, 16, 6] } });
      add({ id: "coast-access", type: "line", source: "coast", minzoom: 12,
        filter: ["in", ["get", "kind"], ["literal", ["beach", "gentle"]]],
        layout: { "line-cap": "round" },
        paint: { "line-color": colors.safe, "line-width": ["interpolate", ["linear"], ["zoom"], 12, 1.5, 16, 4], "line-opacity": 0.8 } });
      add({ id: "unit-routes", type: "line", source: "unit-routes", layout: { "line-cap": "round" },
        paint: { "line-color": colors.unit, "line-width": 3, "line-opacity": 0.85 } });
      add({ id: "plan-route", type: "line", source: "plan-route", layout: { "line-cap": "round" },
        paint: { "line-color": colors.unit, "line-width": 2.5, "line-opacity": 0.9, "line-dasharray": [1, 1.5] } });
      add({ id: "zones", type: "circle", source: "zones",
        paint: { "circle-radius": 6, "circle-color": colors.safe, "circle-stroke-color": "#fff", "circle-stroke-width": 2 } });
      add({ id: "anchors", type: "circle", source: "anchors",
        paint: {
          "circle-radius": ["case", ["get", "is_ignition"], 7, 5],
          "circle-color": ["case", ["get", "is_ignition"], colors.front, "#ffffff"],
          "circle-stroke-color": colors.ink, "circle-stroke-width": 2,
        } });
      // Cases: colour by how close the front is; a ring marks the one open in the panel.
      add({ id: "cases-halo", type: "circle", source: "cases",
        filter: ["==", ["get", "selected"], true],
        paint: { "circle-radius": 16, "circle-color": "rgba(0,0,0,0)", "circle-stroke-color": colors.ink, "circle-stroke-width": 2.5 } });
      add({ id: "cases", type: "circle", source: "cases",
        paint: {
          "circle-radius": ["interpolate", ["linear"], ["coalesce", ["get", "people"], 1], 1, 6, 10, 10, 40, 14],
          "circle-color": ["match", ["get", "state"],
            "overdue", colors.caseOverdue, "urgent", colors.caseUrgent, "handled", colors.caseHandled, colors.caseNormal],
          "circle-opacity": ["match", ["get", "state"], "handled", 0.55, 0.95],
          "circle-stroke-color": "#ffffff", "circle-stroke-width": 2,
        } });
      add({ id: "cases-label", type: "symbol", source: "cases", minzoom: 13,
        layout: { "text-field": ["get", "id"], "text-font": ["Noto Sans Regular"], "text-size": 11,
                  "text-offset": [0, 1.3], "text-anchor": "top", "text-allow-overlap": false },
        paint: { "text-color": colors.ink, "text-halo-color": "#ffffff", "text-halo-width": 1.5 } });
      add({ id: "units", type: "circle", source: "units",
        // An estimated position (between reports) is hollow; a reported one (at base, on scene) is solid.
        paint: { "circle-radius": ["case", ["get", "moving"], 6, 4],
                 "circle-color": ["case", ["get", "estimated"], "#ffffff", colors.unit],
                 "circle-opacity": ["case", ["get", "moving"], 1, 0.6],
                 "circle-stroke-color": ["case", ["get", "estimated"], colors.unit, "#ffffff"], "circle-stroke-width": 2 } });
      add({ id: "point", type: "circle", source: "point",
        paint: { "circle-radius": 7, "circle-color": colors.ink, "circle-stroke-color": "#fff", "circle-stroke-width": 2.5 } });

      const [iso, roads, zones, anchors, buildings, perimeter, coast] = await Promise.all(
        [api.isochrones(), api.roads(), api.zones(), api.anchors(), api.buildings(), api.perimeter(), api.coast()]);
      (m.getSource("coast") as GeoJSONSource).setData(coast);
      (m.getSource("perimeter") as GeoJSONSource).setData(perimeter);
      if (buildings.available) {
        (m.getSource("buildings") as GeoJSONSource).setData(buildings);
        // Our buildings replace the basemap's flat ones.
        if (m.getLayer("building")) m.setLayoutProperty("building", "visibility", "none");
      }
      (m.getSource("iso") as GeoJSONSource).setData(iso);
      // Time labels as DOM markers at the head of each isochrone: no glyph server needed.
      const isoLabels: HTMLElement[] = [];
      for (const f of iso.features) {
        if (!f.properties?.label_point) continue;
        const label = document.createElement("div");
        label.className = "iso-label";
        label.textContent = String(f.properties?.label ?? "");
        isoLabels.push(label);
        new maplibregl.Marker({ element: label }).setLngLat(f.properties?.label_point).addTo(m);
      }
      // Zoomed far out the time labels pile up on top of each other: hide them there.
      const fitLabels = () => isoLabels.forEach((l) => { l.style.visibility = m.getZoom() < 12.5 ? "hidden" : "visible"; });
      m.on("zoom", fitLabels);
      fitLabels();
      (m.getSource("roads") as GeoJSONSource).setData(roads);
      (m.getSource("zones") as GeoJSONSource).setData(zones);
      (m.getSource("anchors") as GeoJSONSource).setData(anchors);
      ready.current = true;
      setLoaded(true);
      m.fire("palladion:ready");
    });

    // Clicking a zone or a sourced point opens its popup; clicking anywhere else checks that point.
    const popup = new maplibregl.Popup({ closeButton: true, maxWidth: "280px", offset: 10 });
    m.on("click", async (ev) => {
      const caseHit = m.getLayer("cases") ? m.queryRenderedFeatures(ev.point, { layers: ["cases"] })[0] : undefined;
      if (caseHit) {
        popup.remove();
        useUi.getState().setCaseId(String(caseHit.properties.id));
        return;
      }
      const hit = m.queryRenderedFeatures(ev.point, { layers: ["anchors", "zones"].filter((id) => m.getLayer(id)) })[0];
      if (!hit) {
        popup.remove();
        setPoint({ lon: ev.lngLat.lng, lat: ev.lngLat.lat });
        return;
      }
      const { lang, clock } = useUi.getState();
      const at = (hit.geometry as GeoJSON.Point).coordinates as [number, number];
      if (hit.layer.id === "anchors") {
        popup.setLngLat(at).setDOMContent(anchorPopup(hit.properties, lang, scenario)).addTo(m);
      } else {
        const now = clock?.sim_time ?? scenario.spec.start;
        const arrival = await api.arrival(at[0], at[1], now).catch(() => null);
        popup.setLngLat(at).setDOMContent(zonePopup(hit.properties, lang, arrival, scenario.spec.timezone)).addTo(m);
      }
    });
    for (const id of ["anchors", "zones", "cases"]) {
      m.on("mouseenter", id, () => { m.getCanvas().style.cursor = "pointer"; });
      m.on("mouseleave", id, () => { m.getCanvas().style.cursor = "crosshair"; });
    }
    m.getCanvas().style.cursor = "crosshair";
    map.current = m;
    return m;
  }

  // Follow the clock: fetch the front (at most one request in flight) and recolour roads.
  const pending = useRef(false);
  const latest = useRef<string | null>(null);
  useEffect(() => {
    const m = map.current;
    if (!m || !clock) return;
    latest.current = clock.sim_time;
    const update = async () => {
      if (!ready.current || pending.current || !latest.current) return;
      const t = latest.current;
      pending.current = true;
      try {
        const front = await api.front(t);
        (m.getSource("front") as GeoJSONSource | undefined)?.setData(
          front.geometry ? { type: "Feature", geometry: front.geometry, properties: {} } : EMPTY,
        );
        (m.getSource("front-edge") as GeoJSONSource | undefined)?.setData(
          front.properties.edge ? { type: "Feature", geometry: front.properties.edge, properties: {} } : EMPTY,
        );
        const nowEpoch = new Date(t).getTime() / 1000;
        m.setPaintProperty("roads", "line-color", roadColor(nowEpoch));
        m.setPaintProperty("buildings", "fill-extrusion-color", buildingColor(nowEpoch));
      } finally {
        pending.current = false;
        if (latest.current !== t) void update();
      }
    };
    if (ready.current) void update();
    else m.once("palladion:ready" as never, () => void update());
  }, [clock]);

  // Cases on the map, refreshed with the queue.
  const cases = useCases();
  const caseId = useUi((s) => s.caseId);
  useEffect(() => {
    const src = map.current?.getSource("cases") as GeoJSONSource | undefined;
    if (!src || !cases.data) return;
    src.setData({
      type: "FeatureCollection",
      features: cases.data.cases.filter((c) => c.location).map((c) => ({
        type: "Feature",
        geometry: { type: "Point", coordinates: [c.location!.lon, c.location!.lat] },
        properties: {
          id: c.id, people: c.people ?? 1, selected: c.id === caseId,
          state: ["approved", "changed", "closed", "resolved"].includes(c.status) ? "handled" : c.urgency,
        },
      })),
    });
  }, [cases.data, caseId, loaded]);
  useEffect(() => {
    const m = map.current;
    const c = cases.data?.cases.find((x) => x.id === caseId);
    // With the spotlight open on the left, the place goes to the right of it, where it can be seen.
    const spot = document.querySelector(".spot")?.getBoundingClientRect();
    const left = spot ? Math.min(spot.right, m?.getContainer().clientWidth ?? 0) * 0.9 : 0;
    if (m && c?.location) m.easeTo({ center: [c.location.lon, c.location.lat], zoom: Math.max(m.getZoom(), 14.5), duration: 900,
      padding: { left, right: 0, top: 0, bottom: 0 } });
    // Only when the selection changes, not on every refresh.
  }, [caseId]);

  // 112 warning areas.
  const alerts = useAlerts();
  const alertId = useUi((s) => s.alertId);
  useEffect(() => {
    const src = map.current?.getSource("alerts") as GeoJSONSource | undefined;
    if (!src || !alerts.data) return;
    src.setData({
      type: "FeatureCollection",
      features: alerts.data.alerts.filter((a) => a.status !== "dismissed").map((a) => ({
        type: "Feature", geometry: a.area,
        properties: { id: a.id, sent: a.status === "sent", selected: a.id === alertId },
      })),
    });
  }, [alerts.data, alertId, loaded]);
  useEffect(() => {
    const m = map.current;
    const a = alerts.data?.alerts.find((x) => x.id === alertId);
    if (!m || !a) return;
    const b = new maplibregl.LngLatBounds();
    const walk = (c: unknown): void => {
      if (Array.isArray(c) && typeof c[0] === "number") b.extend(c as [number, number]);
      else if (Array.isArray(c)) c.forEach(walk);
    };
    walk((a.area as GeoJSON.Polygon | GeoJSON.MultiPolygon).coordinates);
    if (!b.isEmpty()) m.fitBounds(b, { padding: 80, maxZoom: 15.5, duration: 700 });
  }, [alertId]);

  // Land units: where each one is, the routes of units on a mission, and the proposed route for the open case.
  const missions = useMissions();
  const planRoute = useUi((s) => s.planRoute);
  useEffect(() => {
    const m = map.current;
    const d = missions.data;
    if (!m || !d?.available || !m.getSource("units")) return;
    (m.getSource("units") as GeoJSONSource).setData({ type: "FeatureCollection", features: d.units.map((u) => ({
      type: "Feature", geometry: { type: "Point", coordinates: u.position },
      properties: { id: u.id, moving: !["available", "unknown"].includes(u.state), estimated: u.estimated } })) });
    (m.getSource("unit-routes") as GeoJSONSource).setData({ type: "FeatureCollection", features:
      d.missions.filter((x) => ["assigned", "en_route"].includes(x.phase) && x.route.length > 1).map((x) => ({
        type: "Feature", geometry: { type: "LineString", coordinates: x.route }, properties: { id: x.id } })) });
  }, [missions.data, loaded]);
  useEffect(() => {
    const src = map.current?.getSource("plan-route") as GeoJSONSource | undefined;
    src?.setData(planRoute && planRoute.length > 1
      ? { type: "Feature", geometry: { type: "LineString", coordinates: planRoute }, properties: {} } : EMPTY);
  }, [planRoute]);


  // Selected point and route overlays.
  useEffect(() => {
    const src = map.current?.getSource("point") as GeoJSONSource | undefined;
    src?.setData(point
      ? { type: "Feature", geometry: { type: "Point", coordinates: [point.lon, point.lat] }, properties: {} }
      : EMPTY);
  }, [point]);
  useEffect(() => {
    const src = map.current?.getSource("route") as GeoJSONSource | undefined;
    src?.setData(route?.geometry ? { type: "Feature", geometry: route.geometry, properties: {} } : EMPTY);
  }, [route]);

  // 3D: drape everything on the terrain and tilt towards the coast, so the slopes of Penteli
  // and the cliffs above the sea at Mati become visible.
  useEffect(() => {
    const m = map.current;
    if (!m || !ready.current) return;
    if (is3d) {
      m.setTerrain({ source: TERRAIN_SOURCE, exaggeration: TERRAIN_EXAGGERATION });
      m.easeTo({ pitch: 62, bearing: -25, duration: 900 });
    } else {
      m.setTerrain(null);
      m.easeTo({ pitch: 0, bearing: 0, duration: 700 });
    }
  }, [is3d]);

  // The panel opens and closes: the map follows its container, not only the window.
  useEffect(() => {
    if (!el.current) return;
    const ro = new ResizeObserver(() => map.current?.resize());
    ro.observe(el.current);
    return () => ro.disconnect();
  }, []);

  return (
    <div className="map-wrap">
      <div ref={el} className="map" role="application" aria-label="Χάρτης επιχειρήσεων" />
      <button className="map-toggle" aria-pressed={is3d} onClick={() => setIs3d((v) => !v)}
        title={lang === "el" ? "Εναλλαγή 2D / 3D ανάγλυφου" : "Toggle 2D / 3D terrain"}>
        {is3d ? "2D" : "3D"}
      </button>
    </div>
  );
}
