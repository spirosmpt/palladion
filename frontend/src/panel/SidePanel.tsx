import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type ScenarioInfo } from "../api";
import { AlertDesk, useAlerts } from "../alerts/AlertDesk";
import { useCoastGuard } from "../alerts/CoastGuard";
import { CaseQueue, useCases } from "../cases/CaseQueue";
import { SupervisorDesk, useSupervisor } from "../supervisor/SupervisorDesk";
import { compass, dict, formatMinutes } from "../i18n";
import { useUi } from "../store";
import { colors } from "../theme";
import { hhmm } from "../time";

export function SidePanel({ scenario }: { scenario: ScenarioInfo }) {
  const lang = useUi((s) => s.lang);
  const tab = useUi((s) => s.tab);
  const setTab = useUi((s) => s.setTab);
  const cases = useCases();
  const alerts = useAlerts();
  const el = lang === "el";
  const counts = cases.data?.counts;
  const proposed = alerts.data?.alerts.filter((a) => a.status === "proposed").length ?? 0;
  const cg = useCoastGuard().data;
  const cgOpen = cg?.available ? cg.groups.filter((g) => !g.sent || g.new_since_sent.length > 0).length : 0;
  const sup = useSupervisor();
  const supOpen = sup.data?.available ? sup.data.counts.open : 0;
  const supEsc = sup.data?.available ? sup.data.counts.escalated : 0;
  return (
    <aside className="panel">
      <div className="tabs" role="tablist">
        <button role="tab" aria-selected={tab === "cases"} onClick={() => setTab("cases")}>
          {el ? "Περιστατικά" : "Cases"}
          {counts && counts.open > 0 && (
            <span className={`tab-badge ${counts.overdue ? "b-overdue" : counts.urgent ? "b-urgent" : ""}`}>{counts.open}</span>
          )}
        </button>
        <button role="tab" aria-selected={tab === "alerts"} onClick={() => setTab("alerts")}>
          112
          {proposed + cgOpen > 0 && <span className="tab-badge b-alert" title={el ? "Προτάσεις 112 και αιτήματα Λιμενικού που δεν στάλθηκαν" : "112 proposals and unsent Coast Guard requests"}>{proposed + cgOpen}</span>}
        </button>
        <button role="tab" aria-selected={tab === "supervisor"} onClick={() => setTab("supervisor")}>
          {el ? "Επίβλεψη" : "Watch"}
          {supOpen > 0 && <span className={`tab-badge ${supEsc ? "b-overdue" : "b-urgent"}`}>{supOpen}</span>}
        </button>
        <button role="tab" aria-selected={tab === "situation"} onClick={() => setTab("situation")}>
          {el ? "Χάρτης" : "Map"}
        </button>
      </div>
      {tab === "cases" ? <CaseQueue tz={scenario.spec.timezone} />
        : tab === "alerts" ? <AlertDesk tz={scenario.spec.timezone} />
        : tab === "supervisor" ? <SupervisorDesk tz={scenario.spec.timezone} />
        : <SituationTab scenario={scenario} />}
    </aside>
  );
}

function SituationTab({ scenario }: { scenario: ScenarioInfo }) {
  const lang = useUi((s) => s.lang);
  const t = dict[lang];
  const qc = useQueryClient();
  const setClock = useUi((s) => s.setClock);
  const reset = useMutation({
    mutationFn: api.resetSession,
    onSuccess: (c) => { setClock(c); void qc.invalidateQueries(); },
  });
  return (
    <>
      <Situation scenario={scenario} />
      {scenario.warnings.length > 0 && (
        <section className="block">
          {scenario.warnings.map((w) => <p key={w} className="notice">{w}</p>)}
        </section>
      )}
      <PointCheck scenario={scenario} />
      <Legend />
      <section className="block block-quiet">
        <details>
          <summary>{lang === "el" ? "Σχετικά με τα δεδομένα" : "About the data"}</summary>
          <p className="disclaimer">{scenario.spec.disclaimer[lang]}</p>
          <ul className="sources">
            {scenario.spec.sources.map((s) => (
              <li key={s.id}>
                <span className="source-id">{s.id}</span>{" "}
                <a href={s.url} target="_blank" rel="noreferrer">{s.title}</a>
              </li>
            ))}
          </ul>
        <p className="data-status">
          {t.data}: {lang === "el" ? "ακτογραμμή" : "coastline"} {scenario.data.land === "osm" ? t.dataOsm : t.dataFallback},{" "}
          {lang === "el" ? "δρόμοι" : "roads"} {scenario.data.roads ? scenario.data.road_edges.toLocaleString() : "—"},{" "}
          {lang === "el" ? "κτίρια" : "buildings"} {scenario.data.buildings ? scenario.data.buildings.toLocaleString() : "—"},{" "}
          {lang === "el" ? "ζώνες" : "zones"} {scenario.data.zones === "osm" ? t.dataOsm : t.dataFallback}
        </p>
        </details>
        <button className="link danger" onClick={() => {
          if (window.confirm(lang === "el" ? "Νέα συνεδρία: η ώρα γυρνά στην αρχή και οι αποφάσεις αρχειοθετούνται. Συνέχεια;" : "New session: time goes back to the start and decisions are archived. Continue?")) reset.mutate();
        }}>
          {lang === "el" ? "Νέα συνεδρία (αρχειοθέτηση αποφάσεων)" : "New session (archive decisions)"}
        </button>
      </section>
    </>
  );
}

function Situation({ scenario }: { scenario: ScenarioInfo }) {
  const lang = useUi((s) => s.lang);
  const clock = useUi((s) => s.clock);
  const t = dict[lang];
  const now = clock?.sim_time ?? scenario.spec.start;
  const front = useQuery({ queryKey: ["front-stats", now.slice(0, 16)], queryFn: () => api.front(now) });
  const wind = scenario.spec.wind.find((w) => w.station === "Penteli") ?? scenario.spec.wind[0];
  const started = new Date(now) >= new Date(scenario.spec.fire.ignition.time);
  return (
    <section className="block">
      <h2>{t.situation}</h2>
      {started && front.data ? (
        <dl className="stats">
          <div>
            <dt>{t.burned}</dt>
            <dd>{front.data.properties.area_ha.toLocaleString(lang === "el" ? "el-GR" : "en-GB")} ha</dd>
          </div>
          {front.data.properties.buildings_reached !== null && (
            <div>
              <dt>{t.buildingsReached}</dt>
              <dd>{front.data.properties.buildings_reached.toLocaleString(lang === "el" ? "el-GR" : "en-GB")}</dd>
            </div>
          )}
          <div>
            <dt>{t.head}</dt>
            <dd>{(front.data.properties.head_distance_m / 1000).toFixed(1)} km</dd>
          </div>
        </dl>
      ) : (
        <p className="muted">{t.notStarted}</p>
      )}
      {wind && (
        <div className="wind">
          <WindArrow towards={(wind.from_bearing_deg + 180) % 360} />
          <div>
            <div className="wind-title">{t.wind} · {wind.station}</div>
            <div>{t.windFrom(compass(wind.from_bearing_deg, lang), wind.speed_kmh_min, wind.speed_kmh_max, wind.gust_kmh_max)}</div>
          </div>
        </div>
      )}
    </section>
  );
}

function PointCheck({ scenario }: { scenario: ScenarioInfo }) {
  const lang = useUi((s) => s.lang);
  const t = dict[lang];
  const point = useUi((s) => s.point);
  const clock = useUi((s) => s.clock);
  const route = useUi((s) => s.route);
  const setRoute = useUi((s) => s.setRoute);
  const now = clock?.sim_time ?? scenario.spec.start;
  const tz = scenario.spec.timezone;

  const arrival = useQuery({
    queryKey: ["arrival", point?.lon, point?.lat, now.slice(0, 16)],
    queryFn: () => api.arrival(point!.lon, point!.lat, now),
    enabled: !!point,
  });
  const evac = useMutation({
    mutationFn: () => api.evacuate(point!.lon, point!.lat, now),
    onSuccess: setRoute,
  });
  const qc = useQueryClient();
  const setAlertId = useUi((s) => s.setAlertId);
  const warn = useMutation({
    mutationFn: () => api.alertRequest(point!.lon, point!.lat, lang === "el" ? "χειριστής" : "operator"),
    onSuccess: (a) => { void qc.invalidateQueries({ queryKey: ["alerts"] }); setAlertId(a.id); },
  });

  if (!point) {
    return (
      <section className="block">
        <h2>{t.pointTitle}</h2>
        <p className="muted">{t.pointHint}</p>
      </section>
    );
  }
  const a = arrival.data;
  const mins = a?.minutes_from_now ?? null;
  return (
    <section className="block">
      <h2>{t.pointTitle}</h2>
      <p className="coords">{point.lat.toFixed(5)}, {point.lon.toFixed(5)}</p>
      {a && (a.arrival ? (
        <div className={`countdown ${mins !== null && mins <= 0 ? "is-past" : mins !== null && mins <= 30 ? "is-near" : ""}`}>
          <span className="countdown-label">{t.arrivesAt}</span>
          <span className="countdown-value">{hhmm(a.arrival, tz)}</span>
          <span className="countdown-rel">{mins !== null && (mins > 0 ? t.arrivesIn(mins) : t.arrivedAgo(mins))}</span>
        </div>
      ) : (
        <p className="muted">{t.never}</p>
      ))}
      {scenario.data.roads ? (
        <button className="primary" onClick={() => evac.mutate()} disabled={evac.isPending}>
          {evac.isPending ? t.routing : t.routeButton}
        </button>
      ) : (
        <p className="notice">{t.noRoads}</p>
      )}
      {evac.error && <p className="notice">{(evac.error as Error).message}</p>}
      <button className="secondary warn-here" onClick={() => warn.mutate()} disabled={warn.isPending}>
        {lang === "el" ? "Πρόταση προειδοποίησης 112 εδώ" : "Propose a 112 warning here"}
      </button>
      {warn.error && <p className="notice">{(warn.error as Error).message}</p>}
      {route && (
        <div className={`route route-${route.status}`}>
          <h3>{t.route[route.status]}</h3>
          {route.status === "ok" && route.zone && (
            <dl className="stats">
              <div><dt>{t.to}</dt><dd>{route.zone.name || route.zone.kind}</dd></div>
              <div><dt>{t.arrive}</dt><dd>{route.arrive ? hhmm(route.arrive, tz) : "—"}</dd></div>
              <div><dt>{t.distance}</dt><dd>{(route.distance_m / 1000).toFixed(1)} km</dd></div>
              <div><dt>{t.margin}</dt><dd>{route.min_margin_min !== null ? formatMinutes(route.min_margin_min, lang) : t.notThreatened}</dd></div>
            </dl>
          )}
          {route.warnings.map((w) => <p key={w} className="notice">{w}</p>)}
        </div>
      )}
    </section>
  );
}

function Legend() {
  const lang = useUi((s) => s.lang);
  const t = dict[lang];
  const items: [string, React.ReactNode][] = [
    [t.legendCase, <span className="sw sw-dot" style={{ background: colors.caseNormal }} />],
    [t.legendCaseUrgent, <span className="sw sw-dot" style={{ background: colors.caseUrgent }} />],
    [t.legendCaseOverdue, <span className="sw sw-dot" style={{ background: colors.caseOverdue }} />],
    [t.legendCaseHandled, <span className="sw sw-dot" style={{ background: colors.caseHandled, opacity: 0.55 }} />],
    [t.legendUnit, <span className="sw sw-dot" style={{ background: colors.unit }} />],
    [t.legendUnitRoute, <span className="sw sw-line" style={{ background: colors.unit }} />],
    [t.legendPlanRoute, <span className="sw sw-dash" style={{ borderColor: colors.unit, borderTopWidth: 2, borderTopStyle: "dotted" }} />],
    [t.legendAlert, <span className="sw sw-dash" style={{ borderColor: colors.alert, borderTopWidth: 2 }} />],
    [t.legendRoute, <span className="sw sw-line" style={{ background: colors.safe, height: 5 }} />],
    [t.legendBurned, <span className="sw sw-fill" style={{ background: colors.burned }} />],
    [t.legendFront, <span className="sw sw-line" style={{ background: colors.front, height: 3 }} />],
    [t.legendIso, <span className="sw sw-dash" style={{ borderColor: colors.burned }} />],
    [t.legendPerimeter, <span className="sw sw-dash" style={{ borderColor: colors.perimeter, borderTopWidth: 2 }} />],
    [t.legendThreat, <span className="sw sw-line" style={{ background: colors.roadThreat }} />],
    [t.legendClosed, <span className="sw sw-line" style={{ background: colors.roadBurned }} />],
    [t.legendDeadEnd, <span className="sw sw-deadend" style={{ background: colors.roadOpen }} />],
    [t.legendBuilding, <span className="sw sw-fill sw-solid" style={{ background: colors.buildingBurned }} />],
    [t.legendZone, <span className="sw sw-dot" style={{ background: colors.safe }} />],
    [t.legendCliff, <span className="sw sw-line" style={{ background: colors.cliff, height: 4 }} />],
    [t.legendAccess, <span className="sw sw-line" style={{ background: colors.safe }} />],
    [t.legendAnchor, <span className="sw sw-dot sw-ring" />],
  ];
  return (
    <section className="block">
      <h2>{t.legend}</h2>
      <ul className="legend">
        {items.map(([label, sw]) => <li key={label}>{sw}{label}</li>)}
      </ul>
    </section>
  );
}

function WindArrow({ towards }: { towards: number }) {
  return (
    <svg className="wind-arrow" viewBox="-20 -20 40 40" width="40" height="40" aria-hidden>
      <circle r="18" fill="none" stroke="currentColor" strokeOpacity="0.25" />
      <g transform={`rotate(${towards})`}>
        <path d="M0 -14 L6 2 L0 -2 L-6 2 Z" fill={colors.front} />
        <line x1="0" y1="-2" x2="0" y2="14" stroke={colors.front} strokeWidth="2.5" />
      </g>
    </svg>
  );
}

