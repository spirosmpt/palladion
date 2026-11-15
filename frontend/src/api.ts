/** Typed client for the Palladion API. */

export type Lang = "el" | "en";
export type Localized = { el: string; en: string };


export interface Anchor {
  id: string;
  label: Localized;
  lon: number;
  lat: number;
  time: string;
  confidence: "documented" | "derived" | "approximate";
  source_ids: string[];
  note: string;
}

export interface Wind {
  time_from: string;
  time_to: string;
  station: string;
  from_bearing_deg: number;
  speed_kmh_min: number;
  speed_kmh_max: number;
  gust_kmh_max: number | null;
  source_ids: string[];
}

export interface ScenarioInfo {
  spec: {
    id: string;
    name: Localized;
    summary: Localized;
    timezone: string;
    start: string;
    end: string;
    center: [number, number];
    bbox: [number, number, number, number];
    fire: { ignition: Anchor; anchors: Anchor[]; spread_bearing_deg: number; safety_margin_min: number };
    wind: Wind[];
    sources: { id: string; title: string; url: string }[];
    disclaimer: Localized;
  };
  data: {
    land: "osm" | "fallback";
    roads: boolean;
    road_edges: number;
    buildings: number;
    perimeter: boolean;
    zones: "osm" | "fallback";
  };
  fire: { t0: string; final_time: string; last_anchor_time: string };
  warnings: string[];
}

export interface ClockState {
  sim_time: string;
  playing: boolean;
  speed: number;
  start: string;
  end: string;
}

export interface FrontFeature {
  type: "Feature";
  geometry: GeoJSON.Geometry | null;
  properties: {
    time: string;
    head_distance_m: number;
    area_ha: number;
    buildings_reached: number | null;
    edge: GeoJSON.Geometry | null;   // the moving edge only, without coast or perimeter
  };
}

export interface Arrival {
  arrival: string | null;
  minutes_from_now: number | null;
  now: string;
}

export interface RouteResult {
  status: "ok" | "no_safe_route" | "origin_burned" | "off_network";
  warnings: string[];
  zone: { id: string; name: string; kind: string } | null;
  depart: string | null;
  arrive: string | null;
  distance_m: number;
  min_margin_min: number | null;
  dead_end_edges: number;
  geometry: GeoJSON.LineString | null;
}

export type CaseStatus = "processing" | "proposed" | "approved" | "changed" | "closed" | "failed" | "merged" | "resolved";
export type Urgency = "normal" | "urgent" | "overdue";

export interface Needs {
  needs: { tag: string; quote: string; source: "rules" | "agent" }[];
  sender: { role: "crew" | "repost" | "on_site" | "relative"; quote: string } | null;
  acuity: "critical" | "serious" | "immobile" | null; clock_min: number | null; ambulance: boolean;
  summary_el: string; check: string; rejected: string[];
}

export interface SecondOpinion {
  choice?: string | null; choice_el?: string | null; confidence?: number;
  top?: { choice: string; el: string; p: number }[]; proposal?: string | null; p_proposal?: number | null;
  urgent?: number | null; genuine?: number | null; verdict?: "agrees" | "disagrees" | "unsure" | "n/a"; flags?: string[];
  error?: string;
}

export interface CaseSummary {
  id: string;
  time: string;
  channel: string;
  /** Its place in the queue (1 = top) and why it is there, in the order the sort uses. */
  rank?: number | null;
  why_here: { el: string; en: string }[];
  /** What the sender answered when called back, oldest first; simulated ones are marked. */
  replies: { text: string; by: string | null; at: string; simulated: boolean }[];
  /** Oumi Iris's second opinion on the latest reading: probabilities, compared by code. Never decides. */
  second?: SecondOpinion | null;
  /** What a person wrote back to the public page. */
  to_sender?: { text: string; by: string; at: string }[];
  /** Kinds of personal data masked before the agents read the message (phone, email, ...). */
  pii: string[] | null;
  /** Waiting for help and nothing new for 20′ or more: ask "are you still there?" (a proposal, never sent alone). */
  no_news: { minutes: number; question_el: string } | null;
  /** Contradictions between sources nobody has judged yet. */
  contradictions_open: { kind: string; strength: "medium" | "strong" }[];
  /** The operator who typed the message in (a call, the radio); null for messages that arrived by themselves. */
  entered_by: string | null;
  /** Moves on with every event of the case; an action sent with an older one is refused. */
  version: number;
  text: string;
  status: CaseStatus;
  urgency: Urgency;
  minutes_left: number | null;
  /** The earlier of the front and the medical clock (from the needs), and which one. */
  deadline_left: number | null;
  deadline_reason: "front" | "medical" | null;
  needs: Needs | null;
  fire_arrival: string | null;
  decision: string | null;
  final_decision: string | null;
  priority: number | null;
  reasons: string[];
  flags: string[];
  question_el: string | null;
  summary_el: string | null;
  people: number | null;
  vulnerable: string[];
  location: { lon: number; lat: number; status: string; label: string; uncertainty_m: number | null } | null;
  location_status: string | null;
  operator: string | null;
  note: string | null;
  error: string | null;
  /** The duplicate agent thinks this is about the same people as an earlier case (quotes checked by code). */
  duplicate: { match_id: string; reason_el: string; evidence: { new?: string; earlier?: string } } | null;
  merged_into: string | null;
  linked: string[];
  reopened_by: string | null;
  callback: {
    items: string[];
    questions: { item: string; text: string }[];
    source: "agent" | "fallback";
    problems: string[];
    sent: string | null;
    due: string | null;
    reply: string | null;
    cancelled: boolean;
    before: { decision: string; location: string; location_status: string; people: number | null } | null;
  } | null;
  /** What field crews reported, and what code concluded (resolved, partial, not_found, review). */
  field: FieldOutcome[];
}

export type CaseActionName = "approve" | "change" | "close" | "reopen" | "merge" | "not_duplicate" | "unmerge"
  | "cancel_callback";

export interface CaseList {
  now: string;
  llm: { provider: string; model: string;
         /** Today's model calls: all of them, and the app's share (the rest is kept for evaluations). */
         budget: { used: number; limit: number; pool_used: number | null; pool_limit: number | null } | null };
  counts: { open: number; urgent: number; overdue: number; handled: number; duplicates: number; pending_messages: number };
  cases: CaseSummary[];
}

export type AlertStatus = "writing" | "proposed" | "sent" | "dismissed" | "expired" | "replaced";
export type AlertActionKind = "evacuate" | "go_to_coast" | "shelter";

export interface AlertItem {
  id: string;
  status: AlertStatus;
  origin: "auto" | "operator";
  created: string;
  settlement: string;
  band: [number, number];
  buildings: number;
  first_arrival: string;
  minutes_left: number;
  instruction: {
    action: AlertActionKind;
    destination: string | null;
    refuge: string | null;
    refuge_kind: string | null;
    roads: string[];
    routed: number;
    sampled: number;
    coast_m: number | null;
    coast_name: string | null;
    coast_road: string | null;
    reasons: string[];
  };
  text: { el: string; en: string } | null;
  source: "agent" | "template" | "operator" | null;
  problems: string[];
  call: { model: string; status: string } | null;
  operator: string | null;
  acted_at: string | null;
  replaces: string[];
  area: GeoJSON.Geometry;
}

export interface AlertList {
  now: string;
  available: boolean;
  alerts: AlertItem[];
  closed: AlertItem[];
}

export interface Finding { kind: string; quote: string; count_quote: string | null; value: number | boolean; source: "rules" | "agent" }
export interface FieldReport {
  id: string; unit: string; mission: string | null; at: string; text: string; source: "crew" | "operator";
  actor: string; simulated: boolean; findings: Finding[]; check: string; rejected: string[]; button: string | null;
  outcome: { status: string | null; reason_el: string; conflicts: string[]; open_items: string[] } | null;
  applied: boolean; note: string | null;
}
export interface FieldOutcome {
  report: string; unit: string; text: string; simulated: boolean;
  status: "resolved" | "partial" | "not_found" | "review"; decision: string | null; reason_el: string;
  rescued: number | null; remaining: number | null; conflicts: string[]; open_items: string[]; quotes: string[];
}
export type UnitStatus = "en_route" | "on_scene" | "returning" | "available" | "out_of_service";

export interface MissionPlan {
  kind: "fire_engine" | "ambulance"; why: string; unit: string | null; base: string | null;
  depart: string | null; arrive: string | null; eta_min: number | null; distance_km: number; roads: string[];
  warnings: string[]; front_min: number | null; veto: string | null; alternatives: ("sea" | "review")[];
  other_kind: boolean; route: [number, number][];
  /** Every unit that can get there, the proposal first (rank 1). */
  options: { unit: string; kind: string; base: string; arrive: string; eta_min: number; rank: number }[];
}
export interface Mission {
  id: string; case: string; unit: string; kind: "fire_engine" | "ambulance";
  /** From the crew's reports only; the times are estimates. */
  phase: "planned" | "assigned" | "en_route" | "on_scene" | "returning" | "available" | "out_of_service" | "unknown" | "done" | "recalled";
  depart: string; arrive: string; leave_scene: string; back: string; eta_min: number; roads: string[];
  warnings: string[]; brief: string; brief_source: "agent" | "revised" | "template" | ""; brief_problems: string[];
  operator: string; recalled_at: string | null; position: [number, number] | null; route: [number, number][];
  reports: FieldReport[]; override_reason: string | null;
}
export interface CaseMissions { case: string; decision: string | null; open: boolean; can_send: boolean; plan: MissionPlan; missions: Mission[];
  /** What the operator confirms before a unit leaves: id -> text. */
  checklist: Record<string, string> }
export interface MissionsState {
  available: boolean; now: string;
  units: { id: string; kind: string; base: string; state: string; label_el: string; since: string | null; mission: string | null;
           case: string | null; position: [number, number]; estimated: boolean; last_report: FieldReport | null }[];
  missions: Mission[]; note: Localized | null;
}

export interface SupervisorAlert {
  alert_id: string; created_at: string; finding_id: string; kind: string; why_el: string; fact_el: string;
  action: string; action_el: string; allowed: string[]; owner_el: string; due_at: string;
  case_ids: string[]; unit_ids: string[]; landing_ids: string[]; severity: number;
  status: "open" | "accepted" | "dismissed" | "closed"; outcome: "still_open" | "resolved" | null;
  escalated: boolean; closed_at?: string;
  rechecks: { outcome: string; text_el: string; at: string; escalated?: boolean }[];
  decision: { decision: "accept" | "dismiss"; by: string; at: string; result: Record<string, unknown> | null } | null;
}
export interface SupervisorState {
  available: boolean; now: string; interval_min: number; last_tick: string | null; last_check: string | null;
  last_rejected: string[]; unalerted: number; findings_now: number; alerts: SupervisorAlert[];
  counts: { open: number; accepted: number; dismissed: number; closed: number; escalated: number };
}

export interface SitrepSection { key: string; title_el: string; count: number; truncated: boolean;
  items: { id: string; text_el: string; case_ids: string[] }[] }
export interface SitrepSummary { status: "agent" | "skeleton"; sentences: { text: string; cites: string[] }[];
  rejected: string[]; label_el: string }
export interface Handover { id: string; now: string; by: string; note: string; text: string; summary: SitrepSummary }
export interface HandoverState { now: string; sections: SitrepSection[]; counts: Record<string, number>;
  note_el: string; text: string; history: Handover[] }

type Quoted = { quote: string };
export interface HazardReview {
  facts: { id: string; text_el: string; severe: boolean }[];
  cautions: { fact_id: string; text_el: string }[];
  dropped: string[];
  suggested: string | null;
  changed: string | null;
  refused: string | null;
  error: string | null;
}

export interface Credibility {
  signals: { id: string; strength: string; quote: string; text_el: string }[];
  assessment: "credible" | "doubtful" | "likely_false";
  reason_el: string;
  capped: boolean;
  error: string | null;
}

export interface Contradiction {
  id: string; kind: string; strength: "medium" | "strong";
  a: { source: string; ref: string; quote: string }; b: { source: string; ref: string; quote: string };
  text_el: string; question_el: string | null;
  question_fixed_el?: string | null; question_source?: "agent" | "fixed" | "pending";
  decision: { verdict: "first" | "second" | "not_a_contradiction"; by: string; at: string | null; note: string | null } | null;
}
export interface CaseDetail extends CaseSummary {
  contradictions: Contradiction[];
  processed: {
    arbiter: { rule: string; why_el: string; changed: boolean; before: string; after: string } | null;
    credibility: Credibility | null;
    location_review: LocationReview | null;
    hazard_review: HazardReview | null;
    intake: {
      output: {
        people: (Quoted & { count: number | null }) | null;
        vulnerable: (Quoted & { kind: string })[];
        situation: (Quoted & { kind: string }) | null;
        places: (Quoted & { kind: string; role: string; name: string })[];
        hazards: (Quoted & { kind: string })[];
        language: string;
        reporter: string;
        kind: string;
      };
      rejected: string[];
      injection_suspected: boolean;
      call: { provider: string; model: string; status: string; seconds: number } | null;
      review: {
        triggers: string[];
        objections: { field: string; reason: string; quote: string; note: string }[];
        dropped: string[];
        revised: boolean;
        changes: string[];
        error: string | null;
      } | null;
    } | null;
  } | null;
  events: { seq: number; type: string; actor: { kind: string; id: string }; sim_time: string | null; data: Record<string, unknown> }[];
}

export interface LocationReview {
  trigger: string;
  steps: { tool: string; args: Record<string, string>; why_quote: string; results: number; error: string | null }[];
  answer: { candidate_id: string | null; evidence: string[]; reason_el: string } | null;
  accepted: boolean;
  outcome: "found" | "confirmed" | "refined" | "disputed" | "guess" | "no_answer" | "rejected" | "failed" | "";
  reason: string;
  proposed: string | null;
  left_from: string | null;
  error: string | null;
}

export interface FlowEvent {
  id: string; case: string | null; ts: string; src: string; dst: string; kind: string; payload: string;
  status: "ok" | "failed" | "rules"; seconds: number | null; cached: boolean; model: string | null; human_gate: boolean;
}
export interface FlowNode {
  id: string; el: string; events: number; calls: number; failed: number; rules: number; last: string | null;
  avg_s: number | null; max_s: number | null;
}
export interface RoomRun {
  id: string; agent: string; ts: string; given: string; wrote: string; state: "model" | "rejected" | "code" | "failed";
  state_el: string; seconds: number | null; cached: boolean; model: string | null; human: string | null;
}
export interface RoomAgent {
  id: string; el: string; does_el: string; runs: number; counts: Record<RoomRun["state"], number>;
  last: RoomRun | null; avg_s: number | null;
}
export interface RoomFlowData { now: string; agents: RoomAgent[]; runs: RoomRun[]; count: number;
  states_el: Record<string, string> }
export interface FlowData { now: string; case: string | null; events: FlowEvent[]; count: number; nodes: FlowNode[];
  model_seconds: number | null }

/** The language the server writes code's sentences in (X-Lang): messages and names stay as written. */
let LANG = "el";
export function setApiLang(l: string) { LANG = l; }

async function get<T>(path: string): Promise<T> {
  const r = await fetch(path, { headers: { "X-Lang": LANG } });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}

async function post<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Lang": LANG },
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const detail = await r.json().catch(() => ({}));
    throw new Error(detail.detail ?? `${r.status}`);
  }
  return r.json() as Promise<T>;
}

export interface CoastGuardGroup {
  id: string; label: string; lon: number; lat: number; case_ids: string[]; first: string; front_min: number | null; rank: number;
  request: { text: string; cites: string[]; why: string; source: "agent" | "template" };
  sent: { group: string; label: string; case_ids: string[]; text: string; at: string; by: string } | null;
  new_since_sent: string[];
}
export interface CoastGuardView {
  available: boolean; now: string; groups: CoastGuardGroup[]; pending: boolean; waiting_since: string | null;
  agent: { status: string | null; order_by: string | null; steps: { tool: string; group: string; error: string | null }[];
           rejected: { group: string; problems: string[]; text: string }[]; error: string | null } | null;
}

export interface LiveInfo { open: boolean; now: string; name: { el: string; en: string }; timezone: string }
export interface LiveStep { agent: string; el: string; en: string; status: "ok" | "failed" | "rules"; seconds: number | null; tools: number; note: string | null }
export interface LiveStatus {
  id: string; status: string; received: string; now: string; read: boolean; steps: LiveStep[];
  decision: string | null; decision_el: string | null; decision_en: string | null; where: string | null;
  decided_by_person: boolean; flagged: boolean;
  questions: string[]; answers: { text: string; at: string }[]; can_answer: boolean;
  from_centre: { text: string; at: string }[]; advice_el: string | null; advice_en: string | null;
  help?: { unit: string; kind: string; arrive: string; phase: string } | null;
  second?: { choice_el: string | null; confidence: number; verdict: string; top: { choice: string; el: string; p: number }[] } | null;
}

export const api = {
  coastguard: () => get<CoastGuardView>("/api/coastguard"),
  coastguardSend: (group: string, text: string, operator: string) =>
    post<CoastGuardView>("/api/coastguard/send", { group, text, operator }),
  liveInfo: () => get<LiveInfo>("/api/live"),
  liveSend: (text: string) => post<{ id: string; time: string; token: string }>("/api/live/send", { text }),
  liveAnswer: (id: string, text: string, token: string) =>
    post<LiveStatus>(`/api/live/${encodeURIComponent(id)}/answer`, { text, token }),
  liveStatus: (id: string) => get<LiveStatus>(`/api/live/${encodeURIComponent(id)}`),
  scenario: () => get<ScenarioInfo>("/api/scenario"),
  front: (t: string) => get<FrontFeature>(`/api/fire/front?t=${encodeURIComponent(t)}`),
  flow: (caseId?: string, limit = 300) =>
    get<FlowData>(`/api/flow?limit=${limit}${caseId ? `&case_id=${encodeURIComponent(caseId)}` : ""}`),
  flowRoom: () => get<RoomFlowData>("/api/flow/room"),
  isochrones: () => get<GeoJSON.FeatureCollection>("/api/fire/isochrones?step_min=15"),
  anchors: () => get<GeoJSON.FeatureCollection>("/api/anchors"),
  roads: () => get<GeoJSON.FeatureCollection & { available: boolean }>("/api/roads"),
  zones: () => get<GeoJSON.FeatureCollection>("/api/zones"),
  coast: () => get<GeoJSON.FeatureCollection & { counts: Record<string, number>; elevation: boolean }>("/api/coast"),
  buildings: () => get<GeoJSON.FeatureCollection & { available: boolean }>("/api/buildings"),
  perimeter: () => get<GeoJSON.FeatureCollection & { available: boolean }>("/api/perimeter"),
  arrival: (lon: number, lat: number, t: string) =>
    get<Arrival>(`/api/fire/arrival?lon=${lon}&lat=${lat}&t=${encodeURIComponent(t)}`),
  clock: (body: { action: "play" | "pause" | "seek" | "speed"; time?: string; speed?: number }) =>
    post<ClockState>("/api/clock", body),
  cases: () => get<CaseList>("/api/cases"),
  alerts: () => get<AlertList>("/api/alerts"),
  handover: () => get<HandoverState>("/api/handover"),
  handoverCreate: (summary: boolean, note: string, operator: string) =>
    post<HandoverState & { created: Handover }>("/api/handover", { summary, note, operator }),
  supervisor: () => get<SupervisorState>("/api/supervisor"),
  supervisorTick: () => post<SupervisorState>("/api/supervisor/tick", {}),
  supervisorDecide: (id: string, decision: "accept" | "dismiss", operator: string, action?: string) =>
    post<SupervisorState & { decided: { result: Record<string, unknown> | null } }>(
      `/api/supervisor/${encodeURIComponent(id)}`, { decision, operator, action }),
  missions: () => get<MissionsState>("/api/missions"),
  missionPlan: (caseId: string) => get<CaseMissions>(`/api/missions/plan/${encodeURIComponent(caseId)}`),
  missionDispatch: (caseId: string, operator: string, checklist: string[], unit?: string, reason?: string, caseVersion?: number) =>
    post<CaseMissions>("/api/missions/dispatch", { case: caseId, operator, unit, reason, case_version: caseVersion, checklist }),
  caseSubmit: (body: { text: string; channel: string; operator: string; received_at?: string; lon?: number; lat?: number }) =>
    post<{ id: string; time: string }>("/api/cases/submit", body),
  unitReport: (unit: string, body: { text?: string; status?: UnitStatus; operator: string }) =>
    post<{ report: FieldReport; state: { state: string; label_el: string } }>(`/api/units/${encodeURIComponent(unit)}/report`, body),
  missionRecall: (id: string, operator: string) => post<CaseMissions>(`/api/missions/${encodeURIComponent(id)}/recall`, { operator }),
  alertRequest: (lon: number, lat: number, operator: string) =>
    post<AlertItem>("/api/alerts/request", { lon, lat, operator }),
  alertAction: (id: string, body: { action: "send" | "dismiss" | "edit"; el?: string; en?: string; operator?: string }) =>
    post<AlertItem>(`/api/alerts/${encodeURIComponent(id)}/action`, body),
  caseDetail: (id: string) => get<CaseDetail>(`/api/cases/${encodeURIComponent(id)}`),
  caseAction: (id: string, body: { action: CaseActionName; decision?: string; note?: string; operator?: string; target?: string;
                                   expected_version?: number }) =>
    post<CaseSummary>(`/api/cases/${encodeURIComponent(id)}/action`, body),
  caseContradiction: (id: string, item: string, verdict: string, operator: string, note?: string) =>
    post<{ contradictions: Contradiction[] }>(`/api/cases/${encodeURIComponent(id)}/contradictions`, { item, verdict, operator, note }),
  toSender: (id: string, text: string, operator: string) =>
    post<{ id: string; to_sender: { text: string; by: string; at: string }[] }>(`/api/cases/${encodeURIComponent(id)}/to_sender`, { text, operator }),
  caseAnswer: (id: string, text: string, operator: string) =>
    post<CaseSummary>(`/api/cases/${encodeURIComponent(id)}/answer`, { text, operator }),
  resetSession: () => post<ClockState>("/api/session/reset", {}),
  evacuate: (lon: number, lat: number, depart: string) =>
    post<RouteResult>("/api/route/evacuate", { lon, lat, depart }),
};
