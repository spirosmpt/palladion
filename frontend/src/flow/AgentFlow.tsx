import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";

import { api, type FlowEvent } from "../api";
import { useUi } from "../store";
import { AgentTabs } from "./AgentTabs";
import { hhmm, minutesBetween } from "../time";

// One message's trip through the agents, left to right. Drawn from what /api/flow recorded; decides nothing.
const STATIONS: { id: string; el: string; en: string }[] = [
  { id: "citizen", el: "Μήνυμα", en: "Message" },
  { id: "intake", el: "Ανάγνωση", en: "Reading" },
  { id: "critic", el: "Κριτής", en: "Critic" },
  { id: "location", el: "Θέση", en: "Location" },
  { id: "hazard", el: "Κίνδυνος", en: "Hazard" },
  { id: "credibility", el: "Αξιοπιστία", en: "Credibility" },
  { id: "needs", el: "Ανάγκες", en: "Needs" },
  { id: "decide", el: "Πρόταση", en: "Proposal" },
  { id: "operator", el: "Χειριστής", en: "Operator" },
];
const EXTRA_EL: Record<string, string> = { duplicates: "Διπλά", callback: "Επανάκληση", crew: "Πλήρωμα", alerts: "112",
  contra: "Ερώτηση ασυμφωνίας",
  supervisor: "Επόπτης", coastguard: "Λιμενικό" };
const EXTRA_EN: Record<string, string> = { duplicates: "Duplicates", callback: "Call-back", crew: "Crew", alerts: "112",
  contra: "Contradiction question", supervisor: "Supervisor", coastguard: "Coast Guard" };

type State = "model" | "code" | "failed" | "skip" | "human" | "wait";
interface Stop { id: string; name: string; state: State; seconds: number | null; cached: boolean; text: string; note?: string;
  big?: string }
interface Trip { id: string; ts: string; text: string; proposal: string; stops: Stop[]; after: FlowEvent[];
  total: number | null; waiting: boolean; acted: string | null }

const secs = (s: number | null, el: boolean) =>
  s === null ? "" : `${s < 10 ? s.toFixed(1) : Math.round(s)} s`.replace(".", el ? "," : ".");

function trips(events: FlowEvent[], el: boolean, tz: string): Trip[] {
  const by = new Map<string, FlowEvent[]>();
  for (const e of events) if (e.case) by.set(e.case, [...(by.get(e.case) ?? []), e]);
  const out: Trip[] = [];
  for (const [id, evs] of by) {
    const start = evs.find((e) => e.src === "citizen" && e.kind === "message");
    if (!start) continue;
    const pipe = evs.filter((e) => !e.id.startsWith("r"));
    const human = evs.filter((e) => e.id.startsWith("r"));
    const acts = human.filter((e) => e.src === "operator");
    const decide = pipe.find((e) => e.src === "decide");
    const stops: Stop[] = STATIONS.map((s) => {
      const name = el ? s.el : s.en;
      if (s.id === "citizen") {
        return { id: s.id, name, state: "human", seconds: null, cached: false, text: el ? "έφτασε" : "arrived",
          big: hhmm(start.ts, tz) };
      }
      if (s.id === "operator") {
        const a = acts[0];
        if (!a) {
          return { id: s.id, name, state: "wait", seconds: null, cached: false, big: el ? "περιμένει" : "waiting",
            text: el ? "καμία ενέργεια χωρίς έγκριση" : "nothing moves without approval" };
        }
        const m = Math.max(0, Math.round(minutesBetween(start.ts, a.ts)));
        return { id: s.id, name, state: "human", seconds: null, cached: false, text: a.payload, note: hhmm(a.ts, tz),
          big: m < 1 ? (el ? "σε <1′" : "in <1′") : `${el ? "σε" : "in"} ${m}′` };
      }
      const mine = pipe.filter((e) => e.src === s.id);
      const arb = s.id === "decide" ? pipe.find((e) => e.src === "arbiter") : undefined;
      const call = [...mine].reverse().find((e) => e.kind === "call") ?? mine[mine.length - 1];
      if (!call) return { id: s.id, name, state: "skip", seconds: null, cached: false, text: el ? "δεν χρειάστηκε" : "not needed" };
      const tools = s.id === "location" ? mine.filter((e) => e.kind === "tool_call").length : 0;
      const state: State = mine.some((e) => e.status === "failed") ? "failed" : call.status === "rules" ? "code" : "model";
      const text = s.id === "decide" ? call.payload.replace(/^(πρόταση|proposal): /, "") : call.payload.replace(/^(από κώδικα|from code): /, "");
      const note = [tools ? (el ? `${tools} αναζητήσεις` : `${tools} searches`) : "", arb ? arb.payload : ""]
        .filter(Boolean).join(" · ") || undefined;
      return { id: s.id, name, state, seconds: call.kind === "call" ? call.seconds : null, cached: call.cached, text, note };
    });
    const timed = stops.filter((s) => s.seconds !== null);
    out.push({
      id, ts: start.ts, text: start.payload, proposal: decide ? decide.payload.replace(/^(πρόταση|proposal): /, "").split(" · ")[0] : "—",
      stops, after: evs.filter((e) => e.src in EXTRA_EL || (e.id.startsWith("r") && e !== acts[0] && (e.src !== "citizen" || (e.payload.startsWith("απάντηση") || e.payload.startsWith("answer"))))),
      total: timed.length ? timed.reduce((a, s) => a + (s.seconds ?? 0), 0) : null,
      waiting: !acts.length, acted: acts[0]?.payload ?? null,
    });
  }
  return out.sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : b.id.localeCompare(a.id)));
}

export function AgentFlow({ tz }: { tz: string }) {
  const el = useUi((s) => s.lang) === "el";
  const setMode = useUi((s) => s.setFlowOpen);
  const setCaseId = useUi((s) => s.setCaseId);
  const q = useQuery({ queryKey: ["flow", "all"], queryFn: () => api.flow(undefined, 2000), refetchInterval: 2000 });
  const list = useMemo(() => trips(q.data?.events ?? [], el, tz), [q.data, el, tz]);
  const [pinned, setPinned] = useState<string | null>(null);
  const shown = list.find((t) => t.id === pinned) ?? list[0];
  // A new message arriving replays the trip once (only when following the latest).
  const [run, setRun] = useState(0);
  const head = list[0]?.id;
  useEffect(() => { if (head && !pinned) setRun((r) => r + 1); }, [head, pinned]);

  const waiting = list.filter((t) => t.waiting).length;
  const totals = list.map((t) => t.total).filter((x): x is number => x !== null);
  const avg = totals.length ? totals.reduce((a, b) => a + b, 0) / totals.length : null;
  const anyCached = shown?.stops.some((s) => s.cached && s.seconds !== null);

  return (
    <section className="ag-drawer" aria-label={el ? "Πράκτορες" : "Agents"}>
      <header className="ag-head">
        <span className={`ag-live ${q.isError ? "is-off" : ""}`} aria-hidden="true" />
        <AgentTabs />
        <span className="ag-sum">
          {el ? `${list.length} μηνύματα` : `${list.length} messages`}
          {avg !== null && <> · {el ? "μέσος χρόνος ανά μήνυμα" : "average per message"} <b>{secs(avg, el)}</b></>}
          {waiting > 0 && <> · <b className="ag-wait">{el ? `${waiting} περιμένουν χειριστή` : `${waiting} wait for the operator`}</b></>}
        </span>
        <button className="ag-x" onClick={() => setMode(false)} aria-label={el ? "Κλείσιμο" : "Close"}>✕</button>
      </header>

      <div className="ag-body">
        <ol className="ag-list" aria-label={el ? "Μηνύματα, το νεότερο πρώτο" : "Messages, newest first"}>
          {list.length === 0 && <li className="ag-empty">{el ? "Κανένα μήνυμα ακόμη. Πάτα ▶ στο ρολόι." : "No messages yet. Press ▶ on the clock."}</li>}
          {list.map((t) => (
            <li key={t.id}>
              <button className={`ag-row ${t.id === shown?.id ? "is-sel" : ""}`} aria-pressed={t.id === shown?.id}
                onClick={() => { setPinned(t.id); setCaseId(t.id); setRun((r) => r + 1); }}>
                <span className="ag-t">{hhmm(t.ts, tz)}</span>
                <span className="ag-id">{t.id}</span>
                <span className="ag-msg">{t.text}</span>
                <span className="ag-tot">{secs(t.total, el)}</span>
                <span className={`ag-chip ${t.waiting ? "is-wait" : "is-done"}`}>
                  {t.waiting ? (el ? "περιμένει" : "waiting") : (el ? "αποφασίστηκε" : "decided")}
                </span>
              </button>
            </li>
          ))}
        </ol>

        {shown && (
          <div className="ag-trip">
            <div className="ag-trip-head">
              <h3>{shown.id} · {hhmm(shown.ts, tz)}</h3>
              <span className="ag-quote">«{shown.text}»</span>
              {pinned
                ? <button className="link small" onClick={() => setPinned(null)}>{el ? "ακολούθησε το νεότερο" : "follow the newest"}</button>
                : <span className="small muted">{el ? "ακολουθεί το νεότερο" : "following the newest"}</span>}
            </div>
            <ol key={`${shown.id}:${run}`} className="ag-track">
              {shown.stops.map((s, i) => (
                <li key={s.id} className={`ag-stop s-${s.state}`} style={{ animationDelay: `${i * 110}ms` }}
                  title={`${s.name}: ${s.text}${s.note ? ` (${s.note})` : ""}`}>
                  <span className="ag-name">{s.name}</span>
                  <span className="ag-dot" aria-hidden="true" />
                  <span className="ag-sec">
                    {s.seconds !== null ? <>{secs(s.seconds, el)}{s.cached && <sup>*</sup>}</>
                      : s.big ? s.big
                      : s.state === "code" ? (el ? "κώδικας" : "code")
                      : s.state === "model" ? (el ? "μοντέλο" : "model")
                      : s.state === "failed" ? (el ? "απέτυχε" : "failed") : ""}
                  </span>
                  <span className="ag-out">{s.text}</span>
                  {s.note && <span className="ag-note">{s.note}</span>}
                </li>
              ))}
            </ol>
            {shown.after.length > 0 && (
              <ul className="ag-after" aria-label={el ? "Και μετά" : "Afterwards"}>
                <li className="ag-after-h">{el ? "Και μετά" : "Afterwards"}</li>
                {shown.after.map((e) => (
                  <li key={e.id}>
                    <b>{(el ? EXTRA_EL[e.src] : EXTRA_EN[e.src]) ?? (e.src === "operator" ? (el ? "Χειριστής" : "Operator") : e.src)}</b> {e.payload}
                    <span className="muted"> · {hhmm(e.ts, tz)}{e.seconds !== null ? ` · ${secs(e.seconds, el)}` : ""}</span>
                  </li>
                ))}
              </ul>
            )}
            <p className="ag-legend">
              <span><i className="ag-dot s-model" />{el ? "μοντέλο (χρόνος κλήσης)" : "model (call time)"}</span>
              <span><i className="ag-dot s-code" />{el ? "κανόνες κώδικα, χωρίς μοντέλο" : "code rules, no model"}</span>
              <span><i className="ag-dot s-wait" />{el ? "άνθρωπος (χρόνος ως την απόφαση)" : "a person (time to decide)"}</span>
              {anyCached && <span>* {el ? "από αποθήκευση: ο χρόνος της αρχικής κλήσης" : "cached: time of the original call"}</span>}
            </p>
          </div>
        )}
      </div>
    </section>
  );
}
