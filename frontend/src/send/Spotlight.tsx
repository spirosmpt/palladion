import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";

import { api, type LiveStatus } from "../api";
import { useCases } from "../cases/CaseQueue";
import { useMissionPlan } from "../missions/MissionBox";
import { useUi } from "../store";
import { hhmm } from "../time";

const NOTE: Record<string, [string, string]> = {
  found: ["βρήκε πού είναι", "found where they are"], confirmed: ["επιβεβαίωσε τη θέση", "confirmed the location"],
  refined: ["βρήκε τον δρόμο", "found the street"], disputed: ["διαφωνεί με τον κώδικα", "disagrees with code"],
  no_answer: ["δεν ξεχώρισε", "could not tell"], guess: ["ομώνυμοι δρόμοι", "streets with the same name"],
};
const KIND: Record<string, [string, string]> = { fire_engine: ["πυροσβεστικό", "fire engine"], ambulance: ["ασθενοφόρο", "ambulance"] };

/** A message from a phone in the room takes the big screen: the text, the agents as they finish, the proposal.
 *  It only shows. The decision is still made in the case card, by a person. */
export function Spotlight({ tz }: { tz: string }) {
  const cases = useCases().data?.cases;
  const now = useUi((s) => s.clock?.sim_time);
  const seen = useRef<Set<string> | null>(null);
  const [id, setId] = useState<string | null>(null);
  const setCaseId = useUi((s) => s.setCaseId);
  const setPanelOpen = useUi((s) => s.setPanelOpen);

  // A message from the public page that just arrived: known by its id and its time, since ids start again at w01
  // after a reset or a rewind. One that only comes back into view after a jump in time does not count.
  useEffect(() => {
    if (!cases) return;
    const live = cases.filter((c) => c.channel === "live_web");
    const key = (c: { id: string; time: string }) => `${c.id}@${c.time}`;
    if (seen.current === null) { seen.current = new Set(live.map(key)); return; }
    const t = now ? Date.parse(now) : NaN;
    const fresh = live.find((c) => !seen.current!.has(key(c))
      && (Number.isNaN(t) || Math.abs(t - Date.parse(c.time)) <= 3 * 60_000));
    seen.current = new Set(live.map(key));      // only what is in view now: after a reset or a rewind, w01 is new again
    if (fresh && fresh.id !== id) setId(fresh.id);
  }, [cases, now, id]);

  useEffect(() => {
    if (!id) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setId(null); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [id]);

  if (!id) return null;
  const c = cases?.find((x) => x.id === id);
  return <Card key={id} id={id} text={c?.text ?? ""} tz={tz} onClose={() => setId(null)}
    onShown={() => { setPanelOpen(true); setCaseId(id); }} />;
}

function Card({ id, text, tz, onClose, onShown }: {
  id: string; text: string; tz: string; onClose: () => void; onShown: () => void;
}) {
  const el = useUi((s) => s.lang) === "el";
  const i = el ? 0 : 1;
  const q = useQuery({ queryKey: ["live", id], queryFn: () => api.liveStatus(id), refetchInterval: 1000 });
  const d: LiveStatus | undefined = q.data;
  const plan = useMissionPlan(id, !!d?.read && d.decision === "DISPATCH_UNIT_CANDIDATE").data;
  const [shown, setShown] = useState(0);
  const steps = d?.steps ?? [];
  // the agents come in one by one, even when they finished before the screen looked
  useEffect(() => {
    if (shown >= steps.length) return;
    const t = setTimeout(() => setShown((n) => n + 1), 650);
    return () => clearTimeout(t);
  }, [shown, steps.length]);
  const done = !!d?.read && shown >= steps.length;
  useEffect(() => { if (done) onShown(); }, [done]);       // the map goes to the place with the proposal
  const p = plan?.plan;
  const secs = (s: number | null) => (s === null ? "" : `${s < 10 ? s.toFixed(1).replace(".", el ? "," : ".") : Math.round(s)} s`);

  return (
    <aside className="spot" role="dialog" aria-label={el ? "Νέο μήνυμα από το κοινό" : "New message from the public"} aria-live="polite">
      <header className="spot-head">
        <span className="spot-live">{el ? "Από τη σελίδα πολιτών" : "From the public page"}</span>
        <span className="spot-id">{id}{d ? ` · ${hhmm(d.received, tz)}` : ""}</span>
        <button className="spot-x" onClick={onClose} aria-label={el ? "Κλείσιμο" : "Close"}>×</button>
      </header>
      <blockquote className="spot-text">{text || "…"}</blockquote>

      <ol className="spot-steps">
        {steps.slice(0, shown).map((s, i) => (
          <li key={i} className={s.status === "failed" ? "is-failed" : ""}>
            <b>{el ? s.el : s.en}</b>
            <span>
              {s.status === "rules" ? (el ? "κώδικας, χωρίς μοντέλο" : "code, no model")
                : s.status === "failed" ? (el ? "δεν απάντησε · συνεχίζει ο κώδικας" : "no answer · code goes on") : secs(s.seconds)}
              {s.tools > 0 && (el ? ` · ${s.tools} αναζητήσεις στον χάρτη` : ` · ${s.tools} map searches`)}
              {s.note && NOTE[s.note] && ` · ${NOTE[s.note][i]}`}
            </span>
          </li>
        ))}
        {!done && <li className="is-wait"><b>{el ? "Οι πράκτορες διαβάζουν…" : "The agents are reading…"}</b></li>}
      </ol>

      {done && d && (
        <section className="spot-result">
          {d.flagged && <p className="spot-flag">{el ? "Το μήνυμα έδινε εντολές στο σύστημα. Δεν τις ακολούθησε." : "The message gave the system orders. It did not follow them."}</p>}
          <p className="spot-decision">{el ? d.decision_el : d.decision_en}{d.where && <span> · {d.where}</span>}</p>
          {p?.unit && p.arrive && plan?.can_send && (
            <p className="spot-unit">
              {p.unit} ({KIND[p.kind]?.[i] ?? p.kind}) · {el ? "άφιξη" : "arrives"} <b>{hhmm(p.arrive, tz)}</b>
              {p.front_min !== null && (p.front_min >= 0
                ? (el ? ` · ${Math.round(p.front_min)}′ πριν από το μέτωπο` : ` · ${Math.round(p.front_min)}′ before the front`)
                : (el ? ` · το μέτωπο φτάνει ${Math.round(-p.front_min)}′ νωρίτερα` : ` · the front gets there ${Math.round(-p.front_min)}′ earlier`))}
            </p>
          )}
          {d.second && (
            <p className={`spot-second is-${d.second.verdict}`}>{el ? "Δεύτερη γνώμη" : "Second opinion"}, Oumi Iris: {d.second.choice_el} {Math.round((d.second.top[0]?.p ?? d.second.confidence) * 100)}%
              {d.second.verdict === "agrees" ? (el ? " · συμφωνεί" : " · agrees") : d.second.verdict === "disagrees" ? (el ? " · διαφωνεί" : " · disagrees") : (el ? " · αβέβαιη" : " · unsure")}</p>
          )}
          {d.questions.length > 0 && <p className="spot-q">{el ? "Ερώτηση στο κινητό" : "Question to the phone"}: «{d.questions[0]}»</p>}
          <p className="spot-who">{el ? "Οι πράκτορες προτείνουν. Αποφασίζει άνθρωπος." : "The agents propose. A person decides."}</p>
          <button className="spot-open" onClick={onClose}>{el ? "Στο περιστατικό δεξιά →" : "Decide in the case on the right →"}</button>
        </section>
      )}
    </aside>
  );
}
