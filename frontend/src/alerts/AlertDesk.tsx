import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type AlertItem, type Lang } from "../api";
import { CoastGuardDesk } from "./CoastGuard";
import { useUi } from "../store";
import { hhmm } from "../time";

export function useAlerts() {
  return useQuery({ queryKey: ["alerts"], queryFn: api.alerts, refetchInterval: 2000, staleTime: 0 });
}

const ACTION: Record<Lang, Record<string, string>> = {
  el: { evacuate: "Φύγετε με όχημα", go_to_coast: "Όχι αυτοκίνητο: με τα πόδια στην παραλία", shelter: "Όχι αυτοκίνητο: μείνετε σε κτίριο" },
  en: { evacuate: "Leave by vehicle", go_to_coast: "No car: walk to the beach", shelter: "No car: stay in a building" },
};

const band = (a: AlertItem) => (a.band[0] === 0 ? "< 15′" : `${a.band[0]}–${a.band[1]}′`);

/* The 112 desk: Coast Guard requests first (if any), then each proposed warning as one card read top to bottom:
   where and how soon · what people must do · the message as it will appear · send. */
export function AlertDesk({ tz }: { tz: string }) {
  const lang = useUi((s) => s.lang);
  const el = lang === "el";
  const q = useAlerts();
  if (!q.data) return <section className="block"><p className="muted">{el ? "Φόρτωση…" : "Loading…"}</p></section>;
  const { alerts } = q.data;
  if (!q.data.available) {
    return <section className="block"><p className="notice">{el ? "Λείπουν κτίρια και οικισμοί από το OSM (fetch)." : "Buildings and settlements from OSM are missing (fetch)."}</p></section>;
  }
  const open = alerts.filter((a) => a.status === "proposed" || a.status === "writing");
  const sent = alerts.filter((a) => a.status === "sent").sort((a, b) => (b.acted_at ?? "").localeCompare(a.acted_at ?? ""));
  const dismissed = alerts.filter((a) => a.status === "dismissed");

  return (
    <section className="desk">
      <CoastGuardDesk tz={tz} />
      <h2 className="desk-title">{el ? "Προειδοποιήσεις 112" : "112 warnings"}
        <span className="muted">{el ? ` · ${open.length} περιμένουν` : ` · ${open.length} waiting`}{sent.length > 0 && (el ? ` · ${sent.length} στάλθηκαν` : ` · ${sent.length} sent`)}</span></h2>
      {open.length === 0 && <p className="queue-empty">{el ? "Καμία περιοχή δεν απειλείται τα επόμενα 30′." : "No area is threatened in the next 30′."}</p>}
      <ol className="alert-list">
        {open.map((a) => <li key={a.id}><AlertCard a={a} /></li>)}
      </ol>
      {sent.length > 0 && (
        <details className="desk-past">
          <summary>{el ? `Στάλθηκαν (${sent.length})` : `Sent (${sent.length})`}</summary>
          <ul>{sent.map((a) => <li key={a.id}><b>{a.settlement}</b> · {a.acted_at ? hhmm(a.acted_at, tz) : ""} · {ACTION[lang][a.instruction.action]}</li>)}</ul>
        </details>
      )}
      {dismissed.length > 0 && (
        <details className="desk-past">
          <summary>{el ? `Απορρίφθηκαν (${dismissed.length})` : `Dismissed (${dismissed.length})`}</summary>
          <ul>{dismissed.map((a) => <li key={a.id}>{a.settlement} · {ACTION[lang][a.instruction.action]} · {a.operator}</li>)}</ul>
        </details>
      )}
    </section>
  );
}

function AlertCard({ a }: { a: AlertItem }) {
  const lang = useUi((s) => s.lang);
  const el = lang === "el";
  const alertId = useUi((s) => s.alertId);
  const setAlertId = useUi((s) => s.setAlertId);
  const qc = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState({ el: "", en: "" });
  const act = useMutation({
    mutationFn: (body: Parameters<typeof api.alertAction>[1]) => api.alertAction(a.id, { ...body, operator: el ? "χειριστής" : "operator" }),
    onSuccess: () => { setEditing(false); void qc.invalidateQueries({ queryKey: ["alerts"] }); },
  });
  const ins = a.instruction;
  const urgent = a.band[0] === 0;
  const writing = a.status === "writing";
  const where = ins.action === "go_to_coast"
    ? (ins.coast_name ? (el ? `παραλία ${ins.coast_name}` : `${ins.coast_name} beach`) : (el ? `παραλία στο τέλος της ${ins.coast_road ?? ""}` : `beach at the end of ${ins.coast_road ?? ""}`)) + (ins.coast_m ? ` · ~${Math.round(ins.coast_m)} m` : "")
    : ins.action === "evacuate" ? `${el ? "προς " : "towards "}${ins.destination ?? ""}${ins.roads.length ? (el ? " μέσω " : " via ") + ins.roads.join(", ") : ""}` : "";

  return (
    <article className={`alert ${urgent ? "alert-urgent" : ""} ${alertId === a.id ? "alert-selected" : ""}`}>
      <button className="alert-head" onClick={() => setAlertId(alertId === a.id ? null : a.id)} aria-pressed={alertId === a.id}
        title={el ? "Δείξε την περιοχή στον χάρτη" : "Show the area on the map"}>
        <span className="alert-place">{a.settlement}</span>
        <span className={`alert-band ${urgent ? "b-now" : ""}`}>{el ? "μέτωπο σε " : "front in "}{band(a)}</span>
        <span className="alert-meta">{a.buildings} {el ? "κτίρια" : "buildings"}{a.origin === "operator" ? (el ? " · ζήτησε χειριστής" : " · requested") : ""}</span>
      </button>

      <div className={`alert-instr i-${ins.action}`}>
        <strong>{ACTION[lang][ins.action]}</strong>
        {where && <span>{where}</span>}
        {ins.reasons.length > 0 && (
          <details className="cc-more"><summary>{el ? "Γιατί αυτή η οδηγία" : "Why this instruction"}</summary>
            <ul className="small">{ins.reasons.map((r) => <li key={r}>{r}</li>)}</ul></details>
        )}
      </div>
      {a.replaces.length > 0 && <p className="cc-attn-item is-warn small">{el ? "Η οδηγία άλλαξε από την προηγούμενη προειδοποίηση." : "The instruction changed since the last warning."}</p>}

      {writing ? (
        <p className="muted">{el ? "Ο πράκτορας γράφει το μήνυμα…" : "The agent is writing…"}</p>
      ) : editing ? (
        <div className="alert-edit">
          <label>{el ? "Ελληνικά" : "Greek"}<textarea value={draft.el} onChange={(e) => setDraft({ ...draft, el: e.target.value })} rows={4} /></label>
          <label>{el ? "Αγγλικά" : "English"}<textarea value={draft.en} onChange={(e) => setDraft({ ...draft, en: e.target.value })} rows={3} /></label>
          <p className="muted small">{draft.el.length} / {draft.en.length} {el ? "χαρακτήρες (έως 300)" : "characters (max 300)"}</p>
          <div className="cc-row">
            <button className="primary" disabled={act.isPending} onClick={() => act.mutate({ action: "edit", ...draft })}>{el ? "Έλεγχος και αποθήκευση" : "Check and save"}</button>
            <button className="link" onClick={() => setEditing(false)}>{el ? "Άκυρο" : "Cancel"}</button>
          </div>
        </div>
      ) : a.text && (
        <>
          <div className="phone" aria-label={el ? "Το μήνυμα όπως θα εμφανιστεί" : "The message as it will appear"}>
            <p lang={el ? "el" : "en"}>{el ? a.text.el : a.text.en}</p>
          </div>
          <div className="alert-source small">
            <span className={`cc-by ${a.source === "agent" ? "by-agent" : "by-code"}`}
              title={a.source === "template" && a.problems.length ? a.problems.join(" · ") : undefined}>
              {a.source === "agent" ? (el ? "πράκτορας ✓" : "agent ✓") : a.source === "operator" ? (el ? "χειριστής" : "operator") : (el ? "πρότυπο κώδικα" : "code template")}
            </span>
            <details className="cc-more"><summary>{el ? "English" : "Ελληνικά"}</summary><p className="small">{el ? a.text.en : a.text.el}</p></details>
          </div>
        </>
      )}

      {!writing && !editing && (
        <div className="alert-acts">
          <button className="primary send cc-primary" disabled={act.isPending} onClick={() => act.mutate({ action: "send" })}>{el ? `Αποστολή 112: ${a.settlement}` : `Send 112: ${a.settlement}`}</button>
          <div className="cc-row">
            <button className="secondary" onClick={() => { setDraft(a.text ?? { el: "", en: "" }); setEditing(true); }}>{el ? "Αλλαγή κειμένου" : "Edit text"}</button>
            <button className="link" disabled={act.isPending} onClick={() => act.mutate({ action: "dismiss" })}>{el ? "Απόρριψη" : "Dismiss"}</button>
          </div>
        </div>
      )}
      {act.error && <p className="notice">{(act.error as Error).message}</p>}
    </article>
  );
}
