import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { api, type RoomRun } from "../api";
import { useUi } from "../store";
import { AgentTabs } from "./AgentTabs";
import { hhmm } from "../time";

// The operations room's agents: one card each, and their runs, newest first. Drawn from /api/flow/room; decides nothing.
const STATE_SHORT: Record<RoomRun["state"], [string, string]> = {
  model: ["κράτησε", "kept"], rejected: ["πρότυπο", "template"], code: ["κώδικας", "code"], failed: ["απέτυχε", "failed"],
};
const EN: Record<string, [string, string]> = {
  supervisor: ["Supervisor", "watches the whole queue and flags what is forgotten or late"],
  alert_writer: ["112 writer", "writes the warning text, Greek and English"],
  crew_brief: ["Crew card", "writes the crew's instructions when a unit is sent"],
  field_report: ["Crew report", "reads what a crew said from the field"],
  sitrep: ["Shift handover", "writes the summary for the next shift"],
  coastguard: ["Coast Guard liaison", "groups the sea by shore, writes and ranks the requests"],
};
const secs = (s: number | null, el: boolean) =>
  s === null ? "" : `${s < 10 ? s.toFixed(1) : Math.round(s)} s`.replace(".", el ? "," : ".");

export function RoomAgents({ tz }: { tz: string }) {
  const el = useUi((s) => s.lang) === "el";
  const setMode = useUi((s) => s.setFlowOpen);
  const q = useQuery({ queryKey: ["flow", "room"], queryFn: api.flowRoom, refetchInterval: 2500 });
  const [only, setOnly] = useState<string | null>(null);
  const d = q.data;
  const names = new Map((d?.agents ?? []).map((a) => [a.id, el ? a.el : EN[a.id]?.[0] ?? a.el]));
  const runs = (d?.runs ?? []).filter((r) => !only || r.agent === only);
  const total = d?.agents.reduce((n, a) => n + a.runs, 0) ?? 0;
  const anyCached = runs.some((r) => r.cached && r.seconds !== null);

  return (
    <section className="ag-drawer" aria-label={el ? "Πράκτορες αίθουσας" : "Room agents"}>
      <header className="ag-head">
        <span className={`ag-live ${q.isError ? "is-off" : ""}`} aria-hidden="true" />
        <AgentTabs />
        <span className="ag-sum">
          {el ? `${total} εκτελέσεις` : `${total} runs`}
        </span>
        <button className="ag-x" onClick={() => setMode(false)} aria-label={el ? "Κλείσιμο" : "Close"}>✕</button>
      </header>

      <div className="rm-body">
        <ul className="rm-cards" aria-label={el ? "Οι πράκτορες· πάτα για φίλτρο" : "The agents; press to filter"}>
          {(d?.agents ?? []).map((a) => {
            const last = a.last;
            return (
              <li key={a.id}>
                <button className={`rm-card ${only === a.id ? "is-sel" : ""} ${a.runs ? "" : "is-idle"}`}
                  aria-pressed={only === a.id} onClick={() => setOnly(only === a.id ? null : a.id)}>
                  <span className="rm-name">{names.get(a.id)}</span>
                  <span className="rm-does">{el ? a.does_el : EN[a.id]?.[1]}</span>
                  <span className="rm-big">
                    {a.avg_s !== null ? secs(a.avg_s, el) : last ? STATE_SHORT[last.state][el ? 0 : 1] : "—"}
                    {a.avg_s !== null && <small>{el ? " μέσος" : " avg"}</small>}
                  </span>
                  <span className="rm-meta">
                    {a.runs
                      ? <>{el ? `${a.runs} φορές` : `${a.runs} runs`}{last && <> · {el ? "τελευταία" : "last"} {hhmm(last.ts, tz)}</>}</>
                      : (el ? "δεν έχει τρέξει ακόμη" : "has not run yet")}
                  </span>
                  {a.runs > 0 && (
                    <span className="rm-bar" aria-label={(Object.keys(STATE_SHORT) as RoomRun["state"][])
                      .map((k) => `${STATE_SHORT[k][el ? 0 : 1]} ${a.counts[k]}`).join(", ")}>
                      {(Object.keys(STATE_SHORT) as RoomRun["state"][]).map((k) => a.counts[k] > 0 && (
                        <i key={k} className={`st-${k}`} style={{ flexGrow: a.counts[k] }} />
                      ))}
                    </span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>

        <ol className="rm-runs" aria-live="polite">
          {runs.length === 0 && (
            <li className="ag-empty">
              {el ? "Τίποτα ακόμη. Οι πράκτορες της αίθουσας τρέχουν όταν ο επόπτης βρει κάτι, όταν προταθεί 112, όταν σταλεί μονάδα, όταν αναφέρει πλήρωμα ή όταν γραφτεί παράδοση βάρδιας."
                : "Nothing yet. These agents run when the supervisor finds something, a 112 is proposed, a unit is sent, a crew reports or a handover is written."}
            </li>
          )}
          {runs.slice(0, 60).map((r) => (
            <li key={r.id} className={`rm-run st-${r.state}`}>
              <span className="ag-t">{hhmm(r.ts, tz)}</span>
              <span className="rm-who">{names.get(r.agent) ?? r.agent}</span>
              <span className="rm-text">
                <span className="rm-given">{r.given}</span>
                <span className="rm-wrote" title={r.wrote}>{r.wrote}</span>
              </span>
              <span className="rm-state" title={r.state_el}>{STATE_SHORT[r.state][el ? 0 : 1]}</span>
              <span className="rm-sec">{secs(r.seconds, el)}{r.cached && r.seconds !== null && <sup>*</sup>}</span>
              <span className="rm-human">{r.human ? <>◆ {r.human}</> : ""}</span>
            </li>
          ))}
        </ol>
        <p className="ag-legend rm-legend">
          <span><i className="rm-sw st-model" />{el ? "κράτησε: το κείμενο του πράκτορα πέρασε τον έλεγχο του κώδικα" : "kept: passed code's checks"}</span>
          <span><i className="rm-sw st-rejected" />{el ? "πρότυπο: ο κώδικας το απέρριψε" : "template: code rejected it"}</span>
          <span><i className="rm-sw st-code" />{el ? "κώδικας: χωρίς μοντέλο" : "code: no model"}</span>
          <span><i className="rm-sw st-failed" />{el ? "απέτυχε η κλήση" : "call failed"}</span>
          {anyCached && <span>* {el ? "από αποθήκευση: ο χρόνος της αρχικής κλήσης" : "cached: time of the original call"}</span>}
        </p>
      </div>
    </section>
  );
}
