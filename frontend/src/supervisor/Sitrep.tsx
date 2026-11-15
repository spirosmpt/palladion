import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Handover, type HandoverState } from "../api";
import { useUi } from "../store";
import { hhmm } from "../time";

async function copy(text: string) {
  try { await navigator.clipboard.writeText(text); } catch { /* a page without clipboard access: the text is on screen */ }
}

/** Situation report and shift handover: code's lines, live; saved with an optional checked summary. */
export function Sitrep({ tz }: { tz: string }) {
  const el = useUi((s) => s.lang) === "el";
  const setCaseId = useUi((s) => s.setCaseId);
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["handover"], queryFn: api.handover, refetchInterval: 5000 });
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: (summary: boolean) => api.handoverCreate(summary, note, el ? "χειριστής" : "operator"),
    onSuccess: (d) => { qc.setQueryData(["handover"], d as HandoverState); setNote(""); },
  });
  const d = q.data;
  if (!d) return null;
  const shown = d.sections.filter((s) => s.items.length > 0);
  return (
    <section className="sitrep">
      <h2 className="desk-title sup-title">{el ? "Παράδοση βάρδιας" : "Shift handover"} <span className="muted">{hhmm(d.now, tz)}</span></h2>
      <p className="sitrep-counts">{shown.filter((s) => s.key !== "totals").map((s) => <span key={s.key}>{s.title_el.split(" (")[0]} <b>{s.count}</b></span>)}</p>
      {shown.map((s) => (
        <details key={s.key}>
          <summary>{s.title_el} ({s.count})</summary>
          <ul>
            {s.items.map((i) => (
              <li key={i.id}>
                {i.text_el}{" "}
                {i.case_ids.slice(0, 1).map((c) => <button key={c} className="chip-link" onClick={() => setCaseId(c)}>{c}</button>)}
              </li>
            ))}
            {s.truncated && <li className="muted">… {el ? "και" : "and"} {s.count - s.items.length} {el ? "ακόμα" : "more"}</li>}
          </ul>
        </details>
      ))}
      <textarea value={note} onChange={(e) => setNote(e.target.value)} rows={2} maxLength={1000}
        placeholder={el ? "Σημείωση για την επόμενη βάρδια (προαιρετική)" : "Note for the next shift (optional)"} />
      <div className="sup-actions">
        <button className="primary" disabled={save.isPending} onClick={() => save.mutate(true)}>
          {save.isPending ? (el ? "Γράφεται…" : "Writing…") : el ? "Παράδοση βάρδιας με σύνοψη" : "Hand over with summary"}
        </button>
        <button className="secondary" disabled={save.isPending} onClick={() => save.mutate(false)}>{el ? "Χωρίς σύνοψη" : "Without summary"}</button>
        <button className="link" onClick={() => void copy(d.text)}>{el ? "Αντιγραφή κειμένου" : "Copy text"}</button>
      </div>
      {save.error && <p className="notice">{(save.error as Error).message}</p>}
      {d.history.length > 0 && (
        <div className="sitrep-history">
          <h4>{el ? "Παραδόσεις βάρδιας" : "Handovers"}</h4>
          {[...d.history].reverse().map((h) => <Saved key={h.id} h={h} tz={tz} el={el} />)}
        </div>
      )}
    </section>
  );
}

function Saved({ h, tz, el }: { h: Handover; tz: string; el: boolean }) {
  return (
    <details className="sitrep-saved">
      <summary>{hhmm(h.now, tz)} · {h.by}{h.summary.status === "agent" ? (el ? " · με σύνοψη" : " · with summary") : ""}</summary>
      {h.summary.sentences.length > 0 && <ol>{h.summary.sentences.map((x, i) => <li key={i}>{x.text}</li>)}</ol>}
      <p className="muted small">{h.summary.label_el}{h.summary.rejected.length > 0 ? ` · ${el ? "απορρίφθηκαν" : "rejected"}: ${h.summary.rejected.length}` : ""}</p>
      {h.note && <p className="small">{el ? "Σημείωση: " : "Note: "}{h.note}</p>}
      <button className="link" onClick={() => void copy(h.text)}>{el ? "Αντιγραφή κειμένου" : "Copy text"}</button>
    </details>
  );
}
