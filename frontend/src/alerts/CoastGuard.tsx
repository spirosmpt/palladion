import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type CoastGuardGroup, type CoastGuardView } from "../api";
import { CopyButton } from "../common/CopyButton";
import { UndoBar } from "../common/UndoBar";
import { useDelayedSend } from "../common/useDelayedSend";
import { useUi } from "../store";
import { hhmm } from "../time";

export function useCoastGuard() {
  return useQuery({ queryKey: ["coastguard"], queryFn: api.coastguard, refetchInterval: 3000, staleTime: 0 });
}

const STATUS: Record<string, [string, string]> = {
  agent_checked: ["ο πράκτορας έγραψε και ιεράρχησε· ο κώδικας έλεγξε κάθε αριθμό", "the agent wrote and ranked; code checked every number"],
  agent_partial: ["ο πράκτορας έγραψε μερικά· για τα υπόλοιπα μπήκε το πρότυπο του κώδικα", "the agent wrote some; the rest are code's template"],
  agent_rejected: ["ο κώδικας απέρριψε τα κείμενα του πράκτορα· μπήκε το πρότυπο", "code refused the agent's texts; template used"],
  agent_failed: ["ο πράκτορας δεν απάντησε· κείμενα από πρότυπο, σειρά από κώδικα", "the agent did not answer; template texts, code's order"],
};
const TOOL_EL: Record<string, string> = { group_cases: "μηνύματα", shore: "ακτή", fire: "μέτωπο", warnings_sent: "112 που στάλθηκαν" };

/** Coast Guard: open sea and shore messages grouped by stretch of shore, a drafted request for each, ranked. */
export function CoastGuardDesk({ tz }: { tz: string }) {
  const el = useUi((s) => s.lang) === "el";
  const q = useCoastGuard();
  const d = q.data;
  if (!d || !d.available || d.groups.length === 0) return null;
  return (
    <section className="block cg">
      <div className="room-head">
        <h2>{el ? "Λιμενικό" : "Coast Guard"}</h2>
        {d.groups.length > 0 && <span className="small muted">{el ? `${d.groups.length} ακτές` : `${d.groups.length} shores`}</span>}
      </div>
      {d.groups.length === 0 ? (
        <p className="room-none">{el ? "Κανένα ανοιχτό μήνυμα από θάλασσα ή ακτή." : "No open message from the sea or the shore."}</p>
      ) : (
        <>
          <p className="muted small">
            {d.pending ? (el ? "Ο σύνδεσμος Λιμενικού διαβάζει τα μηνύματα και ψάχνει ακτή, μέτωπο και 112…" : "The Coast Guard liaison is reading and looking up shore, front and 112…")
              : d.agent ? STATUS[d.agent.status ?? ""]?.[el ? 0 : 1] ?? "" : ""}
            {d.agent && d.agent.steps.length > 0 && (
              <> · {el ? "εργαλεία: " : "tools: "}{d.agent.steps.map((s) => `${el ? TOOL_EL[s.tool] ?? s.tool : s.tool}(${s.group})`).join(", ")}</>
            )}
          </p>
          <ol className="cg-list">
            {d.groups.map((g) => <li key={g.id}><GroupCard g={g} tz={tz} el={el} orderBy={d.agent?.order_by ?? "code"} /></li>)}
          </ol>
        </>
      )}
    </section>
  );
}

function GroupCard({ g, tz, el, orderBy }: { g: CoastGuardGroup; tz: string; el: boolean; orderBy: string }) {
  const qc = useQueryClient();
  const setCaseId = useUi((s) => s.setCaseId);
  const [text, setText] = useState(g.request.text);
  const [edited, setEdited] = useState(false);
  useEffect(() => { if (!edited) setText(g.request.text); }, [g.request.text, edited]);
  const delayed = useDelayedSend();
  const send = useMutation({
    mutationFn: () => api.coastguardSend(g.id, text, el ? "χειριστής" : "operator"),
    onSuccess: (v: CoastGuardView) => { qc.setQueryData(["coastguard"], v); void qc.invalidateQueries({ queryKey: ["supervisor"] }); setEdited(false); },
  });
  const front = g.front_min === null ? "—" : g.front_min <= 0 ? (el ? "πέρασε" : "passed") : `${g.front_min}′`;
  return (
    <article className={`cg-card ${g.sent && g.new_since_sent.length === 0 ? "is-sent" : ""}`}>
      <header className="cg-top">
        <span className="cg-rank" title={orderBy === "agent" ? (el ? "Σειρά από τον πράκτορα" : "Order by the agent") : (el ? "Σειρά από τον κώδικα" : "Order by code")}>{g.rank}</span>
        <b>{g.label}</b>
        <span className={`cg-front ${g.front_min !== null && g.front_min <= 10 ? "is-near" : ""}`}>{el ? "μέτωπο " : "front "}{front}</span>
      </header>
      <p className="small">
        {g.case_ids.map((c, i) => <span key={c}>{i > 0 && ", "}<button className="link" onClick={() => setCaseId(c)}>{c}</button></span>)}
        {" · "}{el ? "πρώτο " : "first "}{hhmm(g.first, tz)}
      </p>
      {g.request.why && <p className="small muted">{el ? "Προτεραιότητα: " : "Priority: "}{g.request.why}</p>}
      <textarea className="cg-text" value={text} rows={4} maxLength={1500}
        onChange={(e) => { setText(e.target.value); setEdited(true); }} />
      <p className="small muted">{g.request.source === "agent" ? (el ? "Κείμενο πράκτορα, ελεγμένο" : "Agent's text, checked") : (el ? "Πρότυπο κώδικα" : "Code template")}
        {edited && (el ? " · το άλλαξες" : " · edited")}</p>
      {g.sent && (
        <p className="small cg-sent">
          {el ? "Στάλθηκε " : "Sent "}{hhmm(g.sent.at, tz)} ({g.sent.by})
          {g.new_since_sent.length > 0 && <b> · {el ? "νέα μηνύματα από τότε: " : "new since: "}{g.new_since_sent.join(", ")}</b>}
        </p>
      )}
      <div className="cg-actions">
        <button className="primary" disabled={send.isPending || !!delayed.pending || text.trim().length < 10 || (!!g.sent && g.new_since_sent.length === 0 && !edited)}
          onClick={() => delayed.start(el ? "Το αίτημα στο Λιμενικό" : "The Coast Guard request", () => send.mutate())}>
          {g.sent ? (el ? "Ενημέρωση στο Λιμενικό" : "Update the Coast Guard") : (el ? "Αποστολή στο Λιμενικό" : "Send to the Coast Guard")}
        </button>
        <CopyButton text={text} label={el ? "Αντιγραφή" : "Copy"} el={el} />
      </div>
      <UndoBar pending={delayed.pending} undo={delayed.undo} el={el} />
      {send.error && <p className="notice">{(send.error as Error).message}</p>}
    </article>
  );
}
