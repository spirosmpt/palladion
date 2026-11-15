"""Everything a request needs, built once at startup."""
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from palladion.alerts.service import AlertService
from palladion.cases.service import CaseService
from palladion.coastguard.service import CoastGuardService
from palladion.config import Settings
from palladion.datasets.dem import Dem
from palladion.engines.buildings import BuildingLayer
from palladion.engines.coast import CoastAccess
from palladion.engines.fire.model import AnchoredEllipseFire
from palladion.engines.missions import MissionEngine
from palladion.engines.registry.registry import Registry
from palladion.engines.routing.graph import RoadGraph
from palladion.engines.warnings import WarningEngine, coastline, settlement_cells
from palladion.field.service import FieldService
from palladion.field.simulator import CrewSimulator
from palladion.geo.gazetteer import Gazetteer
from palladion.handover.service import HandoverService
from palladion.llm.client import LLM, build_llm
from palladion.missions.service import MissionService
from palladion.pipeline.dedupe import find_duplicate
from palladion.pipeline.process import load_messages, process
from palladion.scenarios.clock import ScenarioClock
from palladion.scenarios.loader import ScenarioData, load_scenario
from palladion.supervisor.service import SupervisorService


@dataclass
class AppState:
    settings: Settings
    scenario: ScenarioData
    fire: AnchoredEllipseFire
    roads: RoadGraph | None
    buildings: BuildingLayer | None
    clock: ScenarioClock
    registry: Registry
    tz: ZoneInfo
    warnings: list[str]
    gazetteer: Gazetteer
    llm: LLM
    cases: CaseService
    warnings_engine: WarningEngine | None = None
    coast: CoastAccess | None = None
    alerts: AlertService | None = None
    mission_engine: MissionEngine | None = None
    missions: MissionService | None = None
    field: FieldService | None = None
    crew_sim: CrewSimulator | None = None
    supervisor: SupervisorService | None = None
    handover: HandoverService = None  # type: ignore[assignment]
    coastguard: "CoastGuardService | None" = None   # sea cases by shore; the liaison agent drafts, a person sends
    _field_stop: object = None

    def hazard_context(self):
        from palladion.agents.hazard import HazardContext
        return HazardContext(self.roads, self.scenario.zones, self.coast, self.scenario.spec.fire.safety_margin_min)

    def new_alerts(self) -> AlertService | None:
        return AlertService(self.warnings_engine, self.llm, self.registry) if self.warnings_engine else None

    def case_info(self, case_id: str):
        """What the missions desk needs about a case: position, people and what they said."""
        c = self.cases.get(case_id)
        if c is None or c.processed is None or c.processed.location.lon is None:
            return None
        p = c.processed
        o = p.intake.output if p.intake else None
        return {"lon": p.location.lon, "lat": p.location.lat, "address": p.location.label or case_id,
                "people": o.people.count if o and o.people else None,
                "vulnerable": [v.kind.value for v in o.vulnerable] if o else [],
                "vulnerable_quotes": [[v.kind.value, v.quote] for v in o.vulnerable] if o else [],
                "fire_at": c.fire_arrival,
                "asks_for": list(o.asks_for) + (["medical"] if (p.needs or {}).get("ambulance") else [])
                if o else [],
                "needs": (p.needs or {}).get("summary_el", ""),
                "situation": o.situation.quote if o and o.situation else None,
                "message": c.message.text, "decision": c.final_decision or p.outcome.decision.value,
                "open": c.status.value not in ("closed", "merged", "resolved")}

    def new_missions(self) -> MissionService | None:
        if self.mission_engine is None:
            return None
        return MissionService(self.mission_engine, self.registry, self.llm, self.case_info,
                              self.field)  # type: ignore[arg-type]

    def new_field(self) -> FieldService:
        """Crew reports. Built before the missions desk, which register their assignments in it."""
        return FieldService(self.registry, self.llm, case_people=lambda cid: self.cases.people(cid),
                            on_outcome=lambda cid, r: self.cases.apply_field(cid, r))

    def new_crew_sim(self) -> CrewSimulator | None:
        if self.field is None:
            return None
        return CrewSimulator.load(
            self.settings.scenario_dir, self.field,
            land=lambda: [m for m in (self.missions.missions if self.missions else []) if m.recalled_at is None])

    def new_field_desks(self) -> None:
        """Field reports, then the desks that assign units, then the crew simulator (demo only), then the
        supervisor that watches all of them."""
        self.field = self.new_field()
        self.missions = self.new_missions()
        self.crew_sim = self.new_crew_sim()
        self.supervisor = self.new_supervisor()
        self.handover = self.new_handover()

    # -- supervisor ------------------------------------------------------------------------------------
    def supervisor_snapshot(self, now):
        """What the supervisor's detectors read: plain values, computed from the desks at `now`."""
        from palladion.cases.service import OPEN, minutes_left, no_news
        missions = self.missions.missions if self.missions else []
        field = self.field
        phase = {m.id: field.mission_phase(m.id, now) for m in missions} if field else {}
        active = {"assigned", "en_route", "on_scene", "returning"}
        cases = []
        for c in self.cases.visible(now):
            p = c.processed
            mine = [m.id for m in missions if m.case_id == c.message.id]
            ml = minutes_left(c, now)
            cases.append({
                "id": c.message.id, "time": c.message.time, "status": c.status.value,
                "decision": c.final_decision or (p.outcome.decision.value if p else None),
                "open": c.status in OPEN, "open_or_handled": c.status.value not in ("closed", "merged", "resolved"),
                "minutes_left": None if ml is None else round(ml, 1),
                "label": p.location.label if p else "", "people": self.cases.people(c.message.id),
                "point": (p.location.lon, p.location.lat) if p and p.location.lon is not None else None,
                "acted_at": c.acted_at, "callback_sent": c.callback_sent, "callback_reply": c.callback_reply,
                "callback_cancelled": c.callback_cancelled, "missions": mine,
                "active_missions": [x for x in mine if phase.get(x) in active],
                "field_status": c.field_reports[-1]["outcome"]["status"] if c.field_reports else None,
                "rescued": sum(f["outcome"].get("rescued") or 0 for f in c.field_reports),
                "no_news_min": (no_news(c, now, self.cases.get) or {}).get("minutes")})
        mrows = []
        eng = self.mission_engine
        for m in missions:
            st = field.state(m.unit, now) if field else {"since": None, "label_el": ""}
            unit = eng.unit(m.unit) if eng else None
            mrows.append({"id": m.id, "case": m.case_id, "unit": m.unit, "phase": phase.get(m.id, "planned"),
                          "since": datetime.fromisoformat(st["since"]) if st["since"] and st.get("mission") == m.id
                          else None, "arrive": m.arrive, "label_el": st["label_el"],
                          "on_scene_min": unit.on_scene_min if unit else 15.0})
        units = [{"id": u.id, "state": field.state(u.id, now)["state"]} for u in eng.units] if eng and field else []
        cg = self.coastguard
        waiting = cg.waiting_since(now) if cg is not None else None
        sea = {"waiting_since": datetime.fromisoformat(waiting) if waiting else None,
               "cases": len(cg.sea_cases(now)) if cg is not None else 0}
        return {"cases": cases, "missions": mrows, "units": units, "sea": sea}

    def handover_snapshot(self, now):
        """The supervisor's view, plus the 112 desk and the supervisor's open alerts."""
        snap = self.supervisor_snapshot(now)
        action_el = {"evacuate": "απομάκρυνση", "go_to_coast": "προς την παραλία", "shelter": "παραμονή σε κτίριο"}
        snap["alerts112"] = [] if self.alerts is None else [
            {"id": a.id, "status": a.status.value, "settlement": a.threat.settlement, "at": a.acted_at,
             "action_el": action_el.get(a.threat.instruction.action.value, a.threat.instruction.action.value)}
            for a in self.alerts.visible() if a.status.value in ("sent", "proposed") and a.created <= now]
        kinds = {"front_no_decision": "Μέτωπο κοντά, χωρίς απόφαση", "approved_not_sent": "Εγκρίθηκε, δεν στάλθηκε",
                 "idle_units": "Διαθέσιμες μονάδες, περιστατικά σε αναμονή", "unit_silent": "Μονάδα χωρίς αναφορά",
                 "unit_idle": "Μονάδα επί τόπου χωρίς αποτέλεσμα", "unit_down": "Βλάβη μονάδας",
                 "callback_aging": "Επανακλήσεις χωρίς απάντηση", "no_news": "Χωρίς νέα από ανθρώπους σε αναμονή",
                 "cluster": "Συγκέντρωση περιστατικών", "coast_guard": "Θάλασσα χωρίς αίτημα στο Λιμενικό"}
        snap["supervisor_alerts"] = [] if self.supervisor is None else [
            {"alert_id": a["alert_id"], "kind_el": kinds.get(a["kind"], a["kind"]), "fact_el": a["fact_el"],
             "created_at": datetime.fromisoformat(a["created_at"]), "escalated": a["escalated"],
             "case_ids": a["case_ids"]}
            for a in self.supervisor.view(now)["alerts"]
            if a["status"] == "open" or (a["status"] == "accepted" and a["outcome"] != "resolved")]
        return snap

    def new_handover(self) -> HandoverService:
        return HandoverService(self.registry, self.llm, self.handover_snapshot)

    def new_supervisor(self) -> SupervisorService:
        def request_alert(a, operator, now):
            if self.alerts is None or not a.get("point"):
                return {"ok": False, "error": "no 112 desk or no point"}
            al = self.alerts.request(a["point"][0], a["point"][1], now, operator)
            return {"ok": True, "alert": al.id, "open_tab": "alerts"}

        actions = {
            "open_case": lambda a, op, now: {"ok": True, "open_case": (a["case_ids"] or [None])[0]},
            "acknowledge": lambda a, op, now: {"ok": True},
            "request_alert": request_alert,
            "open_coastguard": lambda a, op, now: {"ok": True, "open_tab": "alerts"},
        }
        llm = self.llm if self.settings.supervisor_agent else None
        return SupervisorService(self.registry, llm, self.supervisor_snapshot, actions)

    def start_workers(self) -> None:
        import threading
        now = lambda: self.clock.state().sim_time  # noqa: E731
        if self.settings.case_worker:
            self.cases.start(now)
            if self.alerts is not None:
                self.alerts.start(now)
            stop = threading.Event()
            self._field_stop = stop

            def crews() -> None:
                while not stop.wait(1.0):
                    sim = self.crew_sim
                    if sim is not None:
                        try:
                            sim.tick(now())
                        except Exception as exc:  # noqa: BLE001 - a failed report must not stop the others
                            print("crew simulator:", exc)

            threading.Thread(target=crews, daemon=True, name="crew-simulator").start()

            def supervise() -> None:
                while not stop.wait(2.0):
                    sup = self.supervisor
                    if sup is not None:
                        try:
                            sup.maybe_tick(now())
                        except Exception as exc:  # noqa: BLE001 - the desk works without its supervisor
                            print("supervisor:", exc)

            threading.Thread(target=supervise, daemon=True, name="supervisor").start()

    def stop_workers(self) -> None:
        self.cases.stop()
        if self.alerts is not None:
            self.alerts.stop()
        if self._field_stop is not None:
            self._field_stop.set()  # type: ignore[attr-defined]

    def new_cases(self) -> CaseService:
        """A fresh case service over the scenario's messages (used at start and on a session reset)."""
        spec = self.scenario.spec
        msgs_file = self.settings.scenario_dir / "messages.yaml"
        msgs = load_messages(self.settings.scenario_dir, spec.start) if msgs_file.exists() else []
        gaz = self.gazetteer

        def dedupe(p, earlier):
            res, _ = find_duplicate(self.llm, p, earlier, is_area=lambda n: gaz.find_area(n) is not None)
            return res

        ctx = self.hazard_context()
        from palladion.agents.callback import plan as callback_plan
        from palladion.agents.contra_question import ask as contra_ask
        from palladion.cases.simulator import Simulator
        return CaseService(msgs, lambda m: process(m, self.llm, gaz, self.fire, spec.bbox, ctx=ctx,
                                                   earlier=lambda name, t: self.cases.earlier_places(name, t)),
                           self.registry, dedupe=dedupe,
                           callback=lambda p, channel: callback_plan(self.llm, p, channel),
                           contra_ask=lambda item, text, lang: contra_ask(self.llm, item, text, lang),
                           simulator=Simulator.load(self.settings.scenario_dir), second=self.second_opinion(),
                           watch=self.llm.watch)

    def second_opinion(self):  # noqa: ANN201
        """Oumi Iris, when its key is set: one call per reading, cached by text."""
        st = self.settings
        if not (st.oumi_api_key and st.oumi_project):
            return None
        from palladion.agents.iris import Iris, compare, state_of
        iris = Iris(st.oumi_api_key, st.oumi_project, st.llm_cache_dir, st.oumi_model)
        return lambda text, decision: compare(iris.ask(state_of(text, [])), decision)


def build_state(settings: Settings) -> AppState:
    scenario = load_scenario(settings.scenario_dir, settings.data_dir)
    spec = scenario.spec
    perimeter = scenario.perimeter if settings.fire_use_perimeter else None
    fire = AnchoredEllipseFire(spec.fire, scenario.land, perimeter)
    warnings = []
    if perimeter is not None:
        for a in [spec.fire.ignition, *spec.fire.anchors]:
            if fire.outside(a.lon, a.lat):
                warnings.append(f"Το σημείο '{a.id}' είναι έξω από την περίμετρο του Copernicus· "
                                "η θέση του χρειάζεται διόρθωση στο scenario.yaml.")
    for w in warnings:
        print("WARNING:", w)
    roads = RoadGraph.from_geojson(scenario.roads, fire) if scenario.roads else None
    buildings = BuildingLayer(scenario.buildings, fire) if scenario.buildings else None
    clock = ScenarioClock(spec.start, spec.end, spec.default_speed)
    log = settings.runtime_dir / f"{spec.id}.events.jsonl"
    if settings.fresh_start and log.exists() and log.stat().st_size > 0:
        # Demo mode: every start is a new run. The old log is archived, never deleted.
        archived = log.with_suffix(f".{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl")
        log.rename(archived)
        print(f"Fresh start: previous event log archived as {archived.name}")
    registry = Registry(log)
    gaz = Gazetteer.from_processed(settings.data_dir, spec.extra_areas, spec.outside_areas)
    llm = build_llm(settings, spec.id, pool="ui")
    state = AppState(settings, scenario, fire, roads, buildings, clock, registry, ZoneInfo(spec.timezone),
                     warnings, gaz, llm, None)  # type: ignore[arg-type]
    state.cases = state.new_cases()
    bbox = spec.detail_bbox or spec.bbox
    rename = spec.area_display_names
    areas = [(rename.get(e.name, e.name), e.lon, e.lat) for e in gaz.areas if e.area is None]
    settlements = settlement_cells(areas, scenario.land, bbox)
    state.coast = build_coast(settings, scenario, bbox)
    if settlements:
        state.warnings_engine = WarningEngine(fire, settlements, buildings, roads, scenario.zones,
                                              state.coast, spec.fire.safety_margin_min, spec.end)
    state.alerts = state.new_alerts()
    if spec.ground_units is not None and roads is not None:
        coast = state.coast

        def near_shore(lon: float, lat: float) -> bool:
            hit = coast.nearest_shore(lon, lat) if coast is not None else None
            return hit is not None and hit[1] <= 400

        state.mission_engine = MissionEngine(spec.ground_units, roads, fire, spec.fire.safety_margin_min, near_shore)
        print(f"Land units: {len(state.mission_engine.units)}")
    state.new_field_desks()
    # Reads the registry and the cases through the state, so a session reset is followed.

    from palladion.agents.coastguard import draft as coastguard_draft
    state.coastguard = CoastGuardService(
        lambda: state.registry, lambda: state.cases, lambda: state.alerts, state.coast,
        drafter=lambda groups, facts, template, order: coastguard_draft(state.llm, groups, facts, template, order))
    return state


def build_coast(settings: Settings, scenario: ScenarioData, bbox) -> CoastAccess | None:
    """Shore access from mapped cliffs and beaches and, if fetched, elevation (fetch --only coast)."""
    import json

    from shapely.geometry import Point
    shore = coastline(scenario.land, bbox)
    if shore.is_empty:
        return None
    cliffs_path = settings.data_dir / "cliffs.geojson"
    cliffs = json.loads(cliffs_path.read_text(encoding="utf-8")) if cliffs_path.exists() else None
    beaches = [(z.name if z.name != z.kind.value else "", Point(z.lon, z.lat).buffer(60 / 111_320))
               for z in scenario.zones if z.kind.value == "beach"]
    coast = CoastAccess(shore, scenario.land, Dem.load(settings.data_dir / "dem.npz"), cliffs, beaches)
    print(f"Shore access: {coast.counts()} (elevation: {'yes' if coast.has_dem else 'no'}, "
          f"mapped cliffs: {'yes' if cliffs else 'no'})")
    return coast
