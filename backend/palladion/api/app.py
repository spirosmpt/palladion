"""HTTP API. Run with:  uvicorn palladion.api.app:app --reload"""
import asyncio
import hmac
import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from shapely.geometry import mapping

from palladion import __version__
from palladion.agents.alert_writer import AlertText
from palladion.alerts.service import Alert, AlertError
from palladion.api.state import AppState, build_state
from palladion.cases.service import (
    Case,
    CaseError,
    Status,
    deadline,
    deadline_left,
    minutes_left,
    no_news,
    urgency,
    why_here,
)
from palladion.config import REPO_ROOT, Settings, get_settings
from palladion.engines.registry.registry import Registry
from palladion.field.service import FieldError
from palladion.geo.proj import LocalFrame
from palladion.missions.service import CHECKLIST, MissionError
from palladion.supervisor.service import SupervisorError

LOCKED = """<!doctype html><html lang="el"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Palladion</title><body style="font:16px system-ui;max-width:32rem;margin:15vh auto;padding:0 16px">
<h1>Η αίθουσα είναι κλειδωμένη</h1><p>Άνοιξέ την με το κλειδί αίθουσας: <code>/?key=…</code></p>
<p>Θέλεις να στείλεις μήνυμα; <a href="/send">Γράψε στο κέντρο</a>.</p></body></html>"""


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        s = build_state(settings)
        app.state.s = s
        s.start_workers()
        yield
        s.stop_workers()

    app = FastAPI(title="Palladion API", version=__version__, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        """A refused request says why, without echoing the input back: input that is not valid text (a lone
        surrogate, say) would otherwise break the reply itself and turn a refusal into a 500."""
        errors = [{"loc": [str(x) for x in e.get("loc", ())], "msg": str(e.get("msg", ""))} for e in exc.errors()]
        return JSONResponse(status_code=422, content={"detail": errors})
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"],
                       allow_headers=["*"])

    @app.middleware("http")
    async def english_for_the_desk(request: Request, call_next):  # noqa: ANN001, ANN202
        """With `X-Lang: en`, the sentences code wrote in Greek come back in English (palladion.i18n).
        Messages, quotes and names stay as they were written."""
        resp = await call_next(request)
        if request.headers.get("x-lang") != "en" or not request.url.path.startswith("/api/") \
                or not resp.headers.get("content-type", "").startswith("application/json"):
            return resp
        from palladion.i18n import translate
        body = b"".join([chunk async for chunk in resp.body_iterator])
        try:
            data = translate(json.loads(body))
        except ValueError:
            return Response(body, status_code=resp.status_code, media_type="application/json")
        return JSONResponse(data, status_code=resp.status_code)

    @app.middleware("http")
    async def the_desk_needs_the_room_key(request: Request, call_next):  # noqa: ANN001, ANN202
        """With a room key set, only the public page and its API are open; the desk, where people decide, is not."""
        key = settings.room_key
        if key:
            path = request.url.path
            public = path == "/send" or path.startswith(("/api/live", "/assets/")) \
                or path in ("/favicon.ico", "/favicon.svg")
            if request.query_params.get("key") == key:
                resp = RedirectResponse(path, status_code=303)
                resp.set_cookie("palladion_room", key, httponly=True, secure=True, samesite="lax", max_age=7 * 86400)
                return resp
            if not public and not hmac.compare_digest(request.cookies.get("palladion_room", ""), key):
                if path.startswith("/api/"):
                    return JSONResponse(status_code=401, content={"detail": "the desk needs the room key"})
                return HTMLResponse(LOCKED, status_code=401)
        return await call_next(request)

    def st(request: Request) -> AppState:
        return request.app.state.s

    def at(s: AppState, t: datetime | None) -> datetime:
        if t is None:
            return s.clock.state().sim_time
        return t if t.tzinfo else t.replace(tzinfo=s.tz)

    def clock_json(s: AppState) -> dict[str, Any]:
        c = s.clock.state()
        return {"sim_time": c.sim_time.isoformat(), "playing": c.playing, "speed": c.speed,
                "start": c.start.isoformat(), "end": c.end.isoformat()}

    # -- meta ------------------------------------------------------------------------------------
    @app.get("/api/health")
    def health(request: Request) -> dict[str, Any]:
        s = st(request)
        return {"ok": True, "version": __version__, "scenario": s.scenario.spec.id,
                "sample_mode": s.settings.sample_mode}

    @app.get("/api/scenario")
    def scenario(request: Request) -> dict[str, Any]:
        s = st(request)
        spec = s.scenario.spec
        return {
            "spec": spec.model_dump(mode="json", exclude={"fallback_coastline"}),
            "data": {"land": s.scenario.land_source, "roads": s.roads is not None,
                     "buildings": len(s.buildings) if s.buildings else 0,
                     "perimeter": s.scenario.perimeter is not None,
                     "road_edges": len(s.roads.edges) if s.roads else 0, "zones": s.scenario.zones_source},
            "fire": {"t0": s.fire.t0.isoformat(), "final_time": s.fire.final_time.isoformat(),
                     "eccentricity": s.fire.e,
                     "last_anchor_time": (s.fire.t0 + timedelta(
                         seconds=float(s.fire.knots_t[s.fire.anchor_count - 1]))).isoformat()},
            "warnings": s.warnings,
        }

    # -- clock -----------------------------------------------------------------------------------
    class ClockCommand(BaseModel):
        action: Literal["play", "pause", "seek", "speed"]
        time: datetime | None = None
        speed: float | None = Field(default=None, gt=0)

    @app.get("/api/clock")
    def get_clock(request: Request) -> dict[str, Any]:
        return clock_json(st(request))

    @app.post("/api/clock")
    def post_clock(cmd: ClockCommand, request: Request) -> dict[str, Any]:
        s = st(request)
        if cmd.action == "play":
            s.clock.play()
        elif cmd.action == "pause":
            s.clock.pause()
        elif cmd.action == "seek":
            if cmd.time is None:
                raise HTTPException(422, "seek needs 'time'")
            s.clock.seek(at(s, cmd.time))
        elif cmd.action == "speed":
            if cmd.speed is None:
                raise HTTPException(422, "speed needs 'speed'")
            s.clock.set_speed(cmd.speed)
        return clock_json(s)

    @app.get("/api/stream")
    async def stream(request: Request) -> StreamingResponse:
        s = st(request)

        async def gen() -> AsyncIterator[str]:
            while not await request.is_disconnected():
                yield f"event: clock\ndata: {json.dumps(clock_json(s))}\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # -- fire ------------------------------------------------------------------------------------
    @app.get("/api/fire/front")
    def fire_front(request: Request, t: datetime | None = None) -> dict[str, Any]:
        s = st(request)
        when = at(s, t)
        geom = s.fire.front(when)
        frame = LocalFrame(*s.scenario.spec.center)
        area_ha = 0.0
        if not geom.is_empty:
            from shapely.ops import transform
            area_ha = transform(lambda x, y, z=None: frame.to_xy(x, y), geom).area / 10_000
        # After the last sourced anchor the "head" only stands for flank spread; report the head's reach.
        head_m = min(s.fire.head_distance(when), float(s.fire.knots_d[s.fire.anchor_count - 1]))
        edge = s.fire.front_edge(geom)
        return {"type": "Feature", "geometry": mapping(geom) if not geom.is_empty else None,
                "properties": {"time": when.isoformat(), "head_distance_m": round(head_m),
                               "edge": mapping(edge) if not edge.is_empty else None,
                               "area_ha": round(area_ha), "kind": "reconstruction",
                               "buildings_reached": s.buildings.reached_by((when - s.fire.t0).total_seconds())
                               if s.buildings else None}}

    @app.get("/api/fire/isochrones")
    def fire_isochrones(request: Request, step_min: int = Query(15, ge=5, le=120)) -> dict[str, Any]:
        s = st(request)
        # Round clock times (16:45, 17:00, ...) read better than offsets from ignition.
        local_t0 = s.fire.t0.astimezone(s.tz)
        first = local_t0.replace(minute=0, second=0, microsecond=0)
        while first <= local_t0:
            first += timedelta(minutes=step_min)
        times: list[datetime] = []
        t = first
        # Skip a regular isochrone that would sit right on top of the final one.
        while t < s.fire.final_time - timedelta(minutes=step_min / 2):
            times.append(t)
            t += timedelta(minutes=step_min)
        times.append(s.fire.final_time)
        feats = []
        for when, geom in s.fire.isochrones(times):
            if geom.is_empty:
                continue
            # Once the whole perimeter has burned there is no moving front left to label.
            moving = when < s.fire.final_time or s.fire.perimeter is None
            head = s.fire.label_point(when, geom) if moving else None
            edge = s.fire.front_edge(geom)
            if edge.is_empty:
                continue
            feats.append({"type": "Feature", "geometry": mapping(edge),
                          "properties": {"time": when.isoformat(),
                                         "label": when.astimezone(s.tz).strftime("%H:%M"),
                                         "label_point": [round(v, 6) for v in head] if head else None}})
        return {"type": "FeatureCollection", "features": feats}

    @app.get("/api/fire/arrival")
    def fire_arrival(request: Request, lon: float, lat: float, t: datetime | None = None) -> dict[str, Any]:
        s = st(request)
        now = at(s, t)
        arr = s.fire.arrival_time(lon, lat)
        return {"arrival": arr.isoformat() if arr else None,
                "minutes_from_now": round((arr - now).total_seconds() / 60, 1) if arr else None,
                "now": now.isoformat()}

    @app.get("/api/flow")
    def flow(request: Request, case_id: str | None = Query(None, max_length=40),
             limit: int = Query(300, ge=1, le=2000)) -> dict[str, Any]:
        """Who handed what to whom, in time order, with the seconds each model call took. Read only."""
        from palladion.flow.service import FlowBuilder
        s = st(request)
        return FlowBuilder(lambda: s.cases, lambda: s.registry).view(s.clock.state().sim_time, case_id, limit)

    @app.get("/api/flow/room")
    def flow_room(request: Request) -> dict[str, Any]:
        """The operations room's agents: what each wrote, whether code kept it, how long it took. Read only."""
        from palladion.flow.room import RoomFlow
        s = st(request)
        return RoomFlow(lambda: s.registry, lambda: s.alerts).view(s.clock.state().sim_time)

    # -- map layers ------------------------------------------------------------------------------
    @app.get("/api/roads")
    def roads(request: Request) -> dict[str, Any]:
        s = st(request)
        if s.roads is None:
            return {"type": "FeatureCollection", "features": [], "available": False}
        return {**s.roads.to_geojson(), "available": True}

    @app.get("/api/buildings")
    def buildings(request: Request) -> dict[str, Any]:
        s = st(request)
        if s.buildings is None:
            return {"type": "FeatureCollection", "features": [], "available": False}
        return {**s.buildings.to_geojson(), "available": True}

    @app.get("/api/perimeter")
    def perimeter(request: Request) -> dict[str, Any]:
        s = st(request)
        if s.scenario.perimeter is None:
            return {"type": "FeatureCollection", "features": [], "available": False}
        return {"type": "FeatureCollection", "available": True, "features": [
            {"type": "Feature", "geometry": mapping(s.scenario.perimeter.boundary), "properties": {}}]}

    @app.get("/api/zones")
    def zones(request: Request) -> dict[str, Any]:
        s = st(request)
        return {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [z.lon, z.lat]},
            "properties": z.model_dump(mode="json", exclude={"lon", "lat"}),
        } for z in s.scenario.zones]}

    @app.get("/api/anchors")
    def anchors(request: Request) -> dict[str, Any]:
        s = st(request)
        f = s.scenario.spec.fire
        return {"type": "FeatureCollection", "features": [{
            "type": "Feature", "geometry": {"type": "Point", "coordinates": [a.lon, a.lat]},
            "properties": {**a.model_dump(mode="json", exclude={"lon", "lat"}),
                           "is_ignition": a.id == f.ignition.id},
        } for a in [f.ignition, *f.anchors]]}

    # -- routing ---------------------------------------------------------------------------------
    class EvacuateRequest(BaseModel):
        lon: float
        lat: float
        depart: datetime | None = None
        margin_min: float | None = Field(default=None, ge=0, le=120)

    @app.post("/api/route/evacuate")
    def evacuate(req: EvacuateRequest, request: Request) -> dict[str, Any]:
        s = st(request)
        if s.roads is None:
            raise HTTPException(409, "Δεν έχουν φορτωθεί δρόμοι. Τρέξε πρώτα το data_pipeline (README).")
        margin = req.margin_min if req.margin_min is not None else s.scenario.spec.fire.safety_margin_min
        r = s.roads.evacuate(req.lon, req.lat, at(s, req.depart), s.scenario.zones, margin)
        return {
            "status": r.status, "warnings": r.warnings,
            "zone": r.zone.model_dump(mode="json") if r.zone else None,
            "depart": r.depart.isoformat() if r.depart else None,
            "arrive": r.arrive.isoformat() if r.arrive else None,
            "distance_m": round(r.distance_m), "min_margin_min": r.min_margin_min,
            "dead_end_edges": r.dead_end_edges, "snap_m": round(r.snap_m),
            "geometry": {"type": "LineString", "coordinates": r.coords} if r.coords else None,
        }

    # -- shore ---------------------------------------------------------------------------------------
    @app.get("/api/coast")
    def coast(request: Request) -> dict[str, Any]:
        s = st(request)
        if s.coast is None:
            return {"type": "FeatureCollection", "features": [], "counts": {}, "elevation": False}
        return {**s.coast.to_geojson(), "counts": s.coast.counts(), "elevation": s.coast.has_dem}

    # -- cases ---------------------------------------------------------------------------------------
    def cliff_flag(p) -> list[str]:
        """People reported at the shore next to a cliff: a boat may be the only way to reach them."""
        coast = app.state.s.coast
        if p is None or coast is None or p.location.lon is None or p.location.lat is None:
            return []
        return ["cliff_nearby"] if coast.near_cliff(p.location.lon, p.location.lat) else []

    def critic_flag(p) -> list[str]:
        rv = p.intake.review if p is not None and p.intake is not None else None
        return ["critic_revised"] if rv and rv.get("revised") else []

    def case_summary(c: Case, now: datetime) -> dict[str, Any]:
        p = c.processed
        out = p.intake.output if p and p.intake else None
        m = minutes_left(c, now)
        dl = deadline_left(c, now)
        return {
            "id": c.message.id, "time": c.message.time.isoformat(), "channel": c.message.channel,
            "entered_by": c.message.entered_by,
            "version": app.state.s.cases.version(c.message.id),
            "text": c.message.text, "status": c.status.value, "urgency": urgency(c, now),
            "minutes_left": None if m is None else round(m, 1),
            "deadline_left": None if dl is None else round(dl, 1),
            "deadline_reason": deadline(c)[1],
            "why_here": why_here(c, now),
            "no_news": no_news(c, now, app.state.s.cases.get),
            "needs": p.needs if p else None,
            "fire_arrival": c.fire_arrival.isoformat() if c.fire_arrival else None,
            "decision": p.outcome.decision.value if p else None,
            "final_decision": c.final_decision, "priority": p.outcome.priority if p else None,
            "reasons": p.outcome.reasons if p else [],
            "flags": ((p.outcome.flags + p.location.flags) if p else []) + cliff_flag(p) + critic_flag(p),
            "question_el": p.outcome.question_el if p else None,
            "summary_el": out.summary_el if out else None,
            "people": c.field_people if c.field_people is not None else (
                out.people.count if out and out.people else None),
            "field": [{"report": f["report"], "unit": f["unit"], "text": f["text"], "simulated": f["simulated"],
                       **f["outcome"]} for f in c.field_reports],
            "vulnerable": [v.kind.value for v in out.vulnerable] if out else [],
            "location": None if not p or p.location.lon is None else {
                "lon": p.location.lon, "lat": p.location.lat, "status": p.location.status,
                "label": p.location.label, "uncertainty_m": p.location.uncertainty_m},
            "location_status": p.location.status if p else None,
            "operator": c.operator, "note": c.note,
            "duplicate": None if c.duplicate is None or c.duplicate_dismissed or c.status == Status.merged else {
                "match_id": c.duplicate.match_id, "reason_el": c.duplicate.reason_el,
                "evidence": c.duplicate.evidence},
            "merged_into": c.merged_into, "linked": list(c.linked), "reopened_by": c.reopened_by,
            "callback": None if c.callback is None else {
                "items": c.callback.items, "questions": [q.model_dump() for q in c.callback.questions],
                "source": c.callback.source, "problems": c.callback.problems,
                "sent": c.callback_sent.isoformat() if c.callback_sent else None,
                "due": c.callback_due.isoformat() if c.callback_due else None,
                "reply": c.callback_reply, "cancelled": c.callback_cancelled,
                "before": c.before_callback},
            "replies": c.replies,
            "second": c.second,
            "to_sender": c.to_sender,
            "contradictions_open": [{"kind": it["kind"], "strength": it["strength"]}
                                    for it in app.state.s.cases.contradictions(c.message.id) if it["decision"] is None],
            "pii": p.pii if p else None,
            "error": (p.error if p else None) or next((h["error"] for h in c.history if "error" in h), None),
        }

    @app.get("/api/cases")
    def cases(request: Request) -> dict[str, Any]:
        s = st(request)
        now = s.clock.state().sim_time
        summaries = [{**case_summary(c, now), "rank": i + 1} for i, c in enumerate(s.cases.visible(now))]
        return {
            "now": now.isoformat(),
            "llm": {"provider": s.llm.provider.name, "model": s.llm.provider.model_for("default"),
                    "budget": s.llm.budget.status() if s.llm.budget is not None else None},
            "counts": {
                "open": sum(1 for c in summaries if c["status"] in ("processing", "proposed", "failed")),
                "urgent": sum(1 for c in summaries if c["urgency"] == "urgent"),
                "overdue": sum(1 for c in summaries if c["urgency"] == "overdue"),
                "handled": sum(1 for c in summaries if c["status"] in ("approved", "changed", "closed", "merged",
                                                                       "resolved")),
                "duplicates": sum(1 for c in summaries if c["duplicate"]),
                "pending_messages": sum(1 for m in s.cases.messages if m.time > now),
            },
            "cases": summaries,
        }

    @app.get("/api/cases/{case_id}")
    def case_detail(case_id: str, request: Request) -> dict[str, Any]:
        s = st(request)
        c = s.cases.get(case_id)
        if c is None:
            raise HTTPException(404, "no such case (yet)")
        now = s.clock.state().sim_time
        rank = next((i + 1 for i, x in enumerate(s.cases.visible(now)) if x is c), None)
        return {**case_summary(c, now), "rank": rank, "contradictions": s.cases.contradictions(case_id, now, ask=True),
                "processed": c.processed.to_json() if c.processed else None,
                "events": [e.model_dump(mode="json") for e in s.registry.events(f"case:{case_id}")]}

    class CaseSubmit(BaseModel):
        text: str = Field(min_length=3, max_length=12000)       # a long message is read, never bounced
        channel: str = Field(default="199_call", max_length=20)
        received_at: datetime | None = None          # default: the scenario clock now
        lon: float | None = None                     # a point the operator marked on the map (optional)
        lat: float | None = None
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/cases/submit")
    def case_submit(body: CaseSubmit, request: Request) -> dict[str, Any]:
        """A call or radio message the operator typed in: same agents, same queue, same human decisions."""
        s = st(request)
        now = s.clock.state().sim_time
        point = (body.lon, body.lat) if body.lon is not None and body.lat is not None else None
        at = body.received_at.astimezone(now.tzinfo) if body.received_at else None
        try:
            m = s.cases.submit(body.text, body.channel, body.operator, now, at, point)
        except CaseError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": m.id, "time": m.time.isoformat(), "token": live_token(m.id)}

    # -- live page: people write to the desk from a phone (palladion/live.py) ------------------------
    from palladion.live import RateLimit
    from palladion.live import status as live_status
    live_limit = RateLimit()

    class LiveMessage(BaseModel):
        text: str = Field(min_length=3, max_length=600)

    live_secret = secrets.token_bytes(16)

    def live_token(case_id: str) -> str:
        """Proof that a phone sent this message: only its sender can answer for it."""
        return hmac.new(live_secret, case_id.encode(), "sha256").hexdigest()[:24]

    def client_of(request: Request) -> str:
        """Who is sending, as far as we can tell: the proxy's header, or the connection."""
        v = request.headers.get("x-forwarded-for")
        if v:
            return v.split(",")[0].strip()[:64]
        return request.client.host if request.client else "?"

    @app.get("/api/live")
    def live_info(request: Request) -> dict[str, Any]:
        s = st(request)
        spec = s.scenario.spec
        return {"open": s.settings.live_inbox, "now": s.clock.state().sim_time.isoformat(),
                "name": spec.name, "timezone": spec.timezone}

    @app.post("/api/live/send")
    def live_send(body: LiveMessage, request: Request) -> dict[str, Any]:
        s = st(request)
        if not s.settings.live_inbox:
            raise HTTPException(403, "closed")
        why = live_limit.allow(client_of(request))
        if why:
            raise HTTPException(429, why)
        try:
            m = s.cases.submit_public(body.text, s.clock.state().sim_time)
        except CaseError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": m.id, "time": m.time.isoformat(), "token": live_token(m.id)}

    @app.get("/api/live/{case_id}")
    def live_case(case_id: str, request: Request) -> dict[str, Any]:
        """Only a live message, and only what its sender may see about it."""
        from palladion.flow.service import FlowBuilder
        s = st(request)
        c = s.cases.get(case_id) if case_id.startswith("w") and len(case_id) <= 8 else None
        if c is None or c.message.channel != "live_web":
            raise HTTPException(404, "no such message")
        now = s.clock.state().sim_time
        flow = FlowBuilder(lambda: s.cases, lambda: s.registry).view(max(now, c.message.time), case_id)
        return {**live_status(c, flow, now), "help": live_help(s, case_id, now)}

    def live_help(s: AppState, case_id: str, now: datetime) -> dict[str, Any] | None:
        """The unit a person sent, for the sender's phone: which kind, and when it is expected."""
        if s.missions is None:
            return None
        try:
            ms = s.missions.plan(case_id, now).get("missions", [])
        except MissionError:
            return None
        m = next((m for m in ms if m["phase"] in ("assigned", "en_route", "on_scene")), None)
        return None if m is None else {"unit": m["unit"], "kind": m["kind"], "arrive": m["arrive"], "phase": m["phase"]}

    class ToSender(BaseModel):
        text: str = Field(min_length=2, max_length=600)
        operator: str = Field(default="operator", max_length=80)

    @app.post("/api/cases/{case_id}/to_sender")
    def to_sender(case_id: str, body: ToSender, request: Request) -> dict[str, Any]:
        """A person writes back to the sender of a message from the public page; it shows on their phone."""
        s = st(request)
        try:
            c = s.cases.tell_sender(case_id, body.text, body.operator, s.clock.state().sim_time)
        except CaseError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"id": c.message.id, "to_sender": c.to_sender}

    class LiveAnswer(BaseModel):
        text: str = Field(min_length=2, max_length=600)
        token: str = Field(max_length=64)

    @app.post("/api/live/{case_id}/answer")
    def live_answer(case_id: str, body: LiveAnswer, request: Request) -> dict[str, Any]:
        """The sender answers the desk's question from the phone: only for their own message (the token)."""
        from palladion.flow.service import FlowBuilder
        from palladion.live import SENDER
        s = st(request)
        c = s.cases.get(case_id) if case_id.startswith("w") and len(case_id) <= 8 else None
        if c is None or c.message.channel != "live_web" or not hmac.compare_digest(body.token, live_token(case_id)):
            raise HTTPException(404, "no such message")
        why = live_limit.allow(client_of(request))
        if why:
            raise HTTPException(429, why)
        now = s.clock.state().sim_time
        try:
            c = s.cases.answer(case_id, body.text, SENDER, now)
        except CaseError as exc:
            raise HTTPException(409, str(exc)) from exc
        flow = FlowBuilder(lambda: s.cases, lambda: s.registry).view(max(now, c.message.time), case_id)
        return {**live_status(c, flow, now), "help": live_help(s, case_id, now)}

    class ContradictionDecision(BaseModel):
        item: str = Field(max_length=200)
        verdict: Literal["first", "second", "not_a_contradiction"]
        note: str | None = Field(default=None, max_length=500)
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/cases/{case_id}/contradictions")
    def case_contradiction(case_id: str, body: ContradictionDecision, request: Request) -> dict[str, Any]:
        """A person says which side holds. Nothing else changes: the decision on the case stays theirs to make."""
        s = st(request)
        try:
            items = s.cases.decide_contradiction(case_id, body.item, body.verdict, body.operator,
                                                 s.clock.state().sim_time, body.note)
        except CaseError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"contradictions": items}

    class CaseAnswer(BaseModel):
        text: str = Field(min_length=2, max_length=12000)
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/cases/{case_id}/answer")
    def case_answer(case_id: str, body: CaseAnswer, request: Request) -> dict[str, Any]:
        """What the sender said when called back: the case is read again with it."""
        s = st(request)
        now = s.clock.state().sim_time
        try:
            c = s.cases.answer(case_id, body.text, body.operator, now)
        except CaseError as exc:
            raise HTTPException(409, str(exc)) from exc
        return case_summary(c, now)

    class CaseAction(BaseModel):
        action: Literal["approve", "change", "close", "reopen", "merge", "not_duplicate", "unmerge",
                        "cancel_callback"]
        decision: str | None = None
        target: str | None = Field(default=None, max_length=20)     # merge: the case to merge into
        note: str | None = Field(default=None, max_length=500)
        operator: str = Field(default="operator", max_length=60)
        expected_version: int | None = None     # the version the operator saw; a newer case refuses the action

    @app.post("/api/cases/{case_id}/action")
    def case_action(case_id: str, body: CaseAction, request: Request) -> dict[str, Any]:
        s = st(request)
        now = s.clock.state().sim_time
        try:
            c = s.cases.act(case_id, body.action, body.operator, now, body.decision, body.note, body.target,
                            body.expected_version)
        except CaseError as exc:
            raise HTTPException(409, str(exc)) from exc
        return case_summary(c, now)

    # -- 112 desk --------------------------------------------------------------------------------------
    def alert_json(a: Alert, now: datetime) -> dict[str, Any]:
        t, ins = a.threat, a.threat.instruction
        return {
            "id": a.id, "status": a.status.value, "origin": a.origin, "created": a.created.isoformat(),
            "settlement": t.settlement, "band": list(t.band), "buildings": t.buildings,
            "first_arrival": t.first_arrival.isoformat(),
            "minutes_left": round((t.first_arrival - now) / timedelta(minutes=1), 1),
            "instruction": {"action": ins.action.value, "destination": ins.destination, "refuge": ins.refuge,
                            "refuge_kind": ins.refuge_kind, "roads": ins.roads, "routed": ins.routed,
                            "sampled": ins.sampled, "coast_m": ins.coast_m, "coast_name": ins.coast_name,
                            "coast_road": ins.coast_road,
                            "reasons": ins.reasons},
            "text": a.text.model_dump() if a.text else None,
            "source": "operator" if a.edited else (a.written.source if a.written else None),
            "problems": a.written.problems if a.written else [],
            "call": a.written.call if a.written else None,
            "operator": a.operator, "acted_at": a.acted_at.isoformat() if a.acted_at else None,
            "replaces": a.replaces,
            "area": mapping(t.area.simplify(0.0002, preserve_topology=True)),
        }

    # -- Coast Guard: sea cases by shore; the liaison agent drafts and ranks, a person sends ---------------------
    @app.get("/api/coastguard")
    def coastguard(request: Request, t: datetime | None = None) -> dict[str, Any]:
        s = st(request)
        if s.coastguard is None:
            return {"available": False}
        return {"available": True, **s.coastguard.view(at(s, t), ask=t is None)}

    class CoastGuardSend(BaseModel):
        group: str = Field(max_length=10)
        text: str = Field(min_length=10, max_length=1500)
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/coastguard/send")
    def coastguard_send(body: CoastGuardSend, request: Request) -> dict[str, Any]:
        from palladion.coastguard.service import CoastGuardError
        s = st(request)
        if s.coastguard is None:
            raise HTTPException(409, "no coast guard desk")
        try:
            return {"available": True, **s.coastguard.send(body.group, body.text, body.operator,
                                                           s.clock.state().sim_time)}
        except CoastGuardError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/alerts")
    def alerts(request: Request) -> dict[str, Any]:
        s = st(request)
        now = s.clock.state().sim_time
        items = [alert_json(a, now) for a in s.alerts.visible()] if s.alerts else []
        return {
            "now": now.isoformat(), "available": s.alerts is not None,
            "alerts": [a for a in items if a["status"] not in ("expired", "replaced")],
            "closed": [a for a in items if a["status"] in ("expired", "replaced")],
        }

    class AlertRequest(BaseModel):
        lon: float
        lat: float
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/alerts/request")
    def alert_request(body: AlertRequest, request: Request) -> dict[str, Any]:
        s = st(request)
        if s.alerts is None:
            raise HTTPException(409, "Χρειάζονται δεδομένα OSM (κτίρια, οικισμοί). Τρέξε το fetch.")
        now = s.clock.state().sim_time
        try:
            a = s.alerts.request(body.lon, body.lat, now, body.operator, sync=not s.settings.case_worker)
        except AlertError as exc:
            raise HTTPException(409, str(exc)) from exc
        return alert_json(a, now)

    class AlertAction(BaseModel):
        action: Literal["send", "dismiss", "edit"]
        el: str | None = Field(default=None, max_length=1000)
        en: str | None = Field(default=None, max_length=1000)
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/alerts/{alert_id}/action")
    def alert_action(alert_id: str, body: AlertAction, request: Request) -> dict[str, Any]:
        s = st(request)
        if s.alerts is None:
            raise HTTPException(404, "no 112 desk")
        now = s.clock.state().sim_time
        text = AlertText(el=body.el or "", en=body.en or "") if body.action == "edit" else None
        try:
            a = s.alerts.act(alert_id, body.action, body.operator, now, text)
        except AlertError as exc:
            raise HTTPException(409, str(exc)) from exc
        return alert_json(a, now)

    # -- land missions -----------------------------------------------------------------------------------
    def missions_or_409(request: Request):
        s = st(request)
        if s.missions is None:
            raise HTTPException(409, "Χρειάζονται οδικό δίκτυο και το μπλοκ ground_units.")
        return s

    @app.get("/api/missions")
    def missions(request: Request) -> dict[str, Any]:
        s = st(request)
        if s.missions is None:
            return {"available": False}
        return {"available": True, **s.missions.snapshot(s.clock.state().sim_time)}

    @app.get("/api/missions/plan/{case_id}")
    def mission_plan(case_id: str, request: Request) -> dict[str, Any]:
        s = missions_or_409(request)
        try:
            return {**s.missions.plan(case_id, s.clock.state().sim_time), "checklist": CHECKLIST}
        except MissionError as exc:
            raise HTTPException(409, str(exc)) from exc

    class MissionDispatch(BaseModel):
        case: str = Field(max_length=20)
        unit: str | None = Field(default=None, max_length=20)
        reason: str | None = Field(default=None, max_length=500)    # required when the unit is not the proposal
        case_version: int | None = None
        operator: str = Field(default="operator", max_length=60)
        checklist: list[str] = Field(default_factory=list, max_length=10)   # every CHECKLIST item, ticked

    @app.post("/api/missions/dispatch")
    def mission_dispatch(body: MissionDispatch, request: Request) -> dict[str, Any]:
        s = missions_or_409(request)
        now = s.clock.state().sim_time
        if body.case_version is not None and body.case_version != s.cases.version(body.case):
            raise HTTPException(409, "Το περιστατικό άλλαξε από τότε που το άνοιξες. "
                                     "Δες τι άλλαξε πριν στείλεις μονάδα.")
        try:
            s.missions.dispatch(body.case, body.operator, now, body.unit, body.reason, body.checklist)
            return {**s.missions.plan(body.case, now), "checklist": CHECKLIST}
        except MissionError as exc:
            raise HTTPException(409, str(exc)) from exc

    class MissionRecall(BaseModel):
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/missions/{mission_id}/recall")
    def mission_recall(mission_id: str, body: MissionRecall, request: Request) -> dict[str, Any]:
        s = missions_or_409(request)
        now = s.clock.state().sim_time
        try:
            m = s.missions.recall(mission_id, body.operator, now)
            return s.missions.plan(m.case_id, now)
        except MissionError as exc:
            raise HTTPException(409, str(exc)) from exc

    # -- crew reports -----------------------------------------------------------------------------------
    class UnitReport(BaseModel):
        text: str = Field(default="", max_length=2000)
        status: Literal["en_route", "on_scene", "returning", "available", "out_of_service"] | None = None
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/units/{unit_id}/report")
    def unit_report(unit_id: str, body: UnitReport, request: Request) -> dict[str, Any]:
        """What a crew said, typed by an operator (radio, phone), or a status button."""
        s = st(request)
        if s.field is None:
            raise HTTPException(409, "no field desk")
        known = {u.id for u in s.mission_engine.units} if s.mission_engine else set()
        if unit_id not in known:
            raise HTTPException(404, "unknown unit")
        now = s.clock.state().sim_time
        try:
            r = s.field.report(unit_id, body.text, now, source="operator", actor=body.operator, button=body.status)
        except FieldError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"report": r.to_json(), "state": s.field.state(unit_id, now)}

    # -- supervisor -----------------------------------------------------------------------------------
    @app.get("/api/supervisor")
    def supervisor(request: Request) -> dict[str, Any]:
        s = st(request)
        if s.supervisor is None:
            return {"available": False}
        return {"available": True, **s.supervisor.view(s.clock.state().sim_time)}

    @app.post("/api/supervisor/tick")
    def supervisor_tick(request: Request) -> dict[str, Any]:
        """Look now, without waiting for the next 10 minutes."""
        s = st(request)
        if s.supervisor is None:
            raise HTTPException(409, "no supervisor")
        now = s.clock.state().sim_time
        s.supervisor.tick(now)
        return {"available": True, **s.supervisor.view(now)}

    class SupervisorDecision(BaseModel):
        decision: Literal["accept", "dismiss"]
        action: str | None = Field(default=None, max_length=40)
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/supervisor/{alert_id}")
    def supervisor_decide(alert_id: str, body: SupervisorDecision, request: Request) -> dict[str, Any]:
        s = st(request)
        if s.supervisor is None:
            raise HTTPException(409, "no supervisor")
        now = s.clock.state().sim_time
        try:
            out = s.supervisor.decide(alert_id, body.decision, body.operator, now, body.action)
        except SupervisorError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"decided": out, **s.supervisor.view(now)}

    # -- situation report / shift handover ----------------------------------------------------------------
    @app.get("/api/handover")
    def handover(request: Request) -> dict[str, Any]:
        s = st(request)
        now = s.clock.state().sim_time
        return {**s.handover.view(now), "history": s.handover.history(now)}

    class HandoverCreate(BaseModel):
        summary: bool = True
        note: str = Field(default="", max_length=1000)
        operator: str = Field(default="operator", max_length=60)

    @app.post("/api/handover")
    def handover_create(body: HandoverCreate, request: Request) -> dict[str, Any]:
        s = st(request)
        now = s.clock.state().sim_time
        created = s.handover.create(now, body.operator, body.summary, body.note)
        return {"created": created, **s.handover.view(now), "history": s.handover.history(now)}

    @app.post("/api/session/reset")
    def session_reset(request: Request) -> dict[str, Any]:
        """Start a fresh run: the event log is archived, not deleted, and the clock goes back to the start."""
        s = st(request)
        s.stop_workers()
        path = s.registry.path
        if path.exists():
            path.rename(path.with_suffix(f".{datetime.now().strftime('%Y%m%d-%H%M%S')}.jsonl"))
        s.registry = Registry(path)
        s.cases = s.new_cases()
        s.alerts = s.new_alerts()
        s.new_field_desks()
        s.start_workers()
        s.clock.pause()
        s.clock.seek(s.clock.start)
        return clock_json(s)

    # -- registry --------------------------------------------------------------------------------
    @app.get("/api/events")
    def events(request: Request, after: int = 0, stream: str | None = None) -> list[dict[str, Any]]:
        s = st(request)
        return [e.model_dump(mode="json") for e in s.registry.events(stream, after)]

    dist = REPO_ROOT / "frontend" / "dist"
    if dist.exists():
        from fastapi.responses import FileResponse

        @app.get("/send", include_in_schema=False)
        def send_html() -> FileResponse:
            """The live page (people write to the desk from a phone) is the same app, opened at /send."""
            return FileResponse(dist / "index.html")

        app.mount("/", StaticFiles(directory=dist, html=True), name="web")

    return app


app = create_app()
