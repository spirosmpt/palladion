import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, type SupervisorAlert, type SupervisorState } from "../api";
import { useUi } from "../store";
import { Sitrep } from "./Sitrep";
import { hhmm } from "../time";

export function useSupervisor() {
  return useQuery({ queryKey: ["supervisor"], queryFn: api.supervisor, refetchInterval: 3000, staleTime: 0 });
}

const KIND: Record<string, [string, string]> = {
  front_no_decision: ["Μέτωπο κοντά, χωρίς απόφαση", "Front close, no decision"],
  approved_not_sent: ["Εγκρίθηκε, δεν στάλθηκε μονάδα", "Approved, no unit sent"],
  idle_units: ["Διαθέσιμες μονάδες, περιστατικά σε αναμονή", "Units available, cases waiting"],
  unit_silent: ["Μονάδα χωρίς αναφορά άφιξης", "Unit has not reported arriving"],
  unit_idle: ["Μονάδα επί τόπου χωρίς αποτέλεσμα", "Unit on scene, no result"],
  unit_down: ["Βλάβη μονάδας", "Unit breakdown"],
  callback_aging: ["Επανακλήσεις χωρίς απάντηση", "Call-backs with no answer"],
  no_news: ["Χωρίς νέα: «Είστε ακόμα εκεί;»", "No news: «Are you still there?»"],
  cluster: ["Συγκέντρωση περιστατικών", "Cluster of cases"],
  coast_guard: ["Θάλασσα χωρίς αίτημα στο Λιμενικό", "Sea with no Coast Guard request"],
};
const ACTION: Record<string, [string, string]> = {
  open_case: ["Άνοιγμα περιστατικού", "Open the case"], acknowledge: ["Ελήφθη", "Seen"],
  request_alert: ["Πρόταση 112 για την περιοχή", "Propose a 112 warning"],
  open_coastguard: ["Άνοιγμα αιτημάτων Λιμενικού", "Open the Coast Guard requests"],
};
/** The supervisor: what is slipping across the desk, who should act, and whether it got better. */
export function SupervisorDesk({ tz }: { tz: string }) {
  const el = useUi((s) => s.lang) === "el";
  const qc = useQueryClient();
  const q = useSupervisor();
  const setCaseId = useUi((s) => s.setCaseId);
  const setTab = useUi((s) => s.setTab);
  const set = (d: SupervisorState) => qc.setQueryData(["supervisor"], d);
  const tick = useMutation({ mutationFn: api.supervisorTick, onSuccess: set });
  const decide = useMutation({
    mutationFn: (v: { id: string; decision: "accept" | "dismiss"; action?: string }) =>
      api.supervisorDecide(v.id, v.decision, el ? "χειριστής" : "operator", v.action),
    onSuccess: (d) => {
      set(d);
      const r = d.decided.result as { open_case?: string; open_tab?: "alerts" } | null;
      for (const k of ["cases", "case", "alerts"]) void qc.invalidateQueries({ queryKey: [k] });
      if (r?.open_case) setCaseId(r.open_case);
      else if (r?.open_tab) setTab(r.open_tab);
    },
  });
  const d = q.data;
  if (!d) return <section className="block"><p className="muted">…</p></section>;
  if (!d.available) return <section className="block"><p className="muted">{el ? "Ο επιβλέπων δεν είναι διαθέσιμος." : "No supervisor."}</p></section>;
  const live = d.alerts.filter((a) => a.status === "open" || (a.status === "accepted" && a.outcome !== "resolved"));
  const done = d.alerts.filter((a) => !live.includes(a));
  return (
    <section className="block sup">
      <div className="sup-head">
        <h2 className="desk-title sup-title">{el ? "Επόπτης" : "Supervisor"}
          <span className="muted">{d.last_tick ? ` · ${el ? "έλεγχος" : "checked"} ${hhmm(d.last_tick, tz)}` : ""}</span></h2>
        <button className="secondary" onClick={() => tick.mutate()} disabled={tick.isPending}>{el ? "Έλεγχος τώρα" : "Check now"}</button>
      </div>
      {live.length === 0 && <p className="sup-ok">✓ {el ? "Τίποτα δεν ξεφεύγει αυτή τη στιγμή." : "Nothing is slipping right now."}</p>}
      {live.map((a) => <AlertCard key={a.alert_id} a={a} tz={tz} el={el} busy={decide.isPending}
        onCase={setCaseId} onDecide={(decision, action) => decide.mutate({ id: a.alert_id, decision, action })} />)}
      {decide.error && <p className="notice">{(decide.error as Error).message}</p>}
      {done.length > 0 && (
        <details className="sup-done">
          <summary>{el ? `Κλειστές και απορριφθείσες (${done.length})` : `Closed and dismissed (${done.length})`}</summary>
          {done.map((a) => <AlertCard key={a.alert_id} a={a} tz={tz} el={el} busy onCase={setCaseId} onDecide={() => undefined} />)}
        </details>
      )}
      <Sitrep tz={tz} />
    </section>
  );
}

function AlertCard({ a, tz, el, busy, onCase, onDecide }: {
  a: SupervisorAlert; tz: string; el: boolean; busy: boolean; onCase: (id: string) => void;
  onDecide: (d: "accept" | "dismiss", action?: string) => void;
}) {
  const last = a.rechecks[a.rechecks.length - 1];
  return (
    <article className={`sup-alert s-${a.status} ${a.escalated ? "sup-esc" : ""}`}>
      <p className="sup-kind">
        {KIND[a.kind]?.[el ? 0 : 1] ?? a.kind}
        {a.escalated && <span className="sup-badge">{el ? "κλιμάκωση" : "escalated"}</span>}
        <span className="muted small"> · {hhmm(a.created_at, tz)}</span>
      </p>
      <p className="sup-why">{a.why_el}</p>
      {a.why_el !== a.fact_el && (
        <details className="cc-more">
          <summary>{el ? "Τα στοιχεία" : "The facts"}</summary>
          <p className="muted small">{a.fact_el}</p>
        </details>
      )}
      {a.case_ids.length > 0 && (
        <p className="sup-cases">{a.case_ids.map((c) => <button key={c} className="chip-link" onClick={() => onCase(c)}>{c}</button>)}</p>
      )}
      <p className="muted small" title={last ? `${hhmm(last.at, tz)}: ${last.text_el}` : undefined}>
        {a.owner_el} · {el ? "έως" : "due"} {hhmm(a.due_at, tz)}
      </p>
      {a.status === "open" ? (
        <div className="sup-actions">
          <button className="primary" disabled={busy} onClick={() => onDecide("accept")}>{ACTION[a.action]?.[el ? 0 : 1] ?? a.action}</button>
          {a.allowed.filter((x) => x !== a.action).map((x) => (
            <button key={x} className="secondary" disabled={busy} onClick={() => onDecide("accept", x)}>{ACTION[x]?.[el ? 0 : 1] ?? x}</button>
          ))}
          <button className="link" disabled={busy} onClick={() => onDecide("dismiss")}>{el ? "Απόρριψη" : "Dismiss"}</button>
        </div>
      ) : (
        <p className="small">
          {a.status === "closed" ? (el ? "Έκλεισε: η συνθήκη έπαψε" : "Closed: the condition is gone")
            : a.status === "dismissed" ? (el ? "Απορρίφθηκε" : "Dismissed")
            : (el ? "Έγινε αποδεκτή · αναμονή να λυθεί" : "Accepted · waiting for it to resolve")}
          {a.decision && <> · {a.decision.by} {hhmm(a.decision.at, tz)}</>}
        </p>
      )}
    </article>
  );
}
