import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type FieldReport, type UnitStatus } from "../api";
import { hhmm } from "../time";

const KIND: Record<string, [string, string]> = {
  rescued_n: ["σώθηκαν", "rescued"], remaining_n: ["μένουν", "still there"], all_out: ["βγήκαν όλοι", "all out"],
  not_found: ["δεν βρέθηκε κανείς", "nobody found"], left_by_other_means: ["έφυγαν μόνοι", "left on their own"],
  needs_more: ["ζητά κι άλλη μονάδα", "asks for another unit"], new_hazard: ["νέος κίνδυνος", "new hazard"],
  departed: ["έφυγε", "left"], arrived: ["έφτασε", "arrived"], returning: ["επιστρέφει", "returning"],
  available: ["διαθέσιμη", "available"], failed: ["βλάβη", "breakdown"],
};
const BUTTONS: [UnitStatus, string, string][] = [
  ["en_route", "Έφυγε", "Left"], ["on_scene", "Επί τόπου", "On scene"], ["returning", "Επιστρέφει", "Returning"],
  ["available", "Διαθέσιμη", "Available"], ["out_of_service", "Βλάβη", "Breakdown"],
];

/** The crew's reports for one mission or trip, newest last, with what code read in each. */
export function ReportList({ reports, tz, el }: { reports: FieldReport[]; tz: string; el: boolean }) {
  if (reports.length === 0) return <p className="muted small">{el ? "Καμία αναφορά από το πλήρωμα ακόμα." : "No word from the crew yet."}</p>;
  return (
    <ol className="reports">
      {reports.map((r) => (
        <li key={r.id} className={r.applied ? "" : "not-applied"}>
          <span className="muted">{hhmm(r.at, tz)}</span>{" "}
          {r.simulated && <span className="sim">{el ? "ΠΡΟΣΟΜΟΙΩΣΗ" : "SIMULATED"}</span>}{" "}
          {r.source === "operator" && <span className="muted">{r.actor}: </span>}
          {r.text ? <q>{r.text}</q> : <span>{el ? "κουμπί" : "button"}: {r.button}</span>}
          {r.findings.length > 0 && (
            <span className="findings">
              {r.findings.map((f, i) => (
                <span key={i} className={`finding f-${f.source}`} title={`«${f.quote}»${f.source === "agent" ? (el ? " · από τον πράκτορα" : " · by the agent") : ""}`}>
                  {KIND[f.kind]?.[el ? 0 : 1] ?? f.kind}{typeof f.value === "number" ? ` ${f.value}` : ""}
                </span>
              ))}
            </span>
          )}
          {r.rejected.length > 0 && <span className="muted small"> · {el ? "απορρίφθηκαν από τον κώδικα" : "rejected by code"}: {r.rejected.length}</span>}
          {r.note && <span className="muted small"> · {r.note}</span>}
        </li>
      ))}
    </ol>
  );
}

/** What a crew said by radio or phone, typed by the operator; or a status button. */
export function ReportForm({ unit, el, onDone }: { unit: string; el: boolean; onDone?: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const send = useMutation({
    mutationFn: (body: { text?: string; status?: UnitStatus }) => api.unitReport(unit, { ...body, operator: el ? "χειριστής" : "operator" }),
    onSuccess: () => {
      setText("");
      for (const k of ["missions", "mission", "cases", "case"]) void qc.invalidateQueries({ queryKey: [k] });
      onDone?.();
    },
  });
  return (
    <details className="report-form">
      <summary>{el ? `Αναφορά από το ${unit}` : `Report from ${unit}`}</summary>
      <textarea value={text} onChange={(e) => setText(e.target.value)} maxLength={2000} rows={2}
        placeholder={el ? "Τι είπε το πλήρωμα, π.χ. «Πήραμε 2 άτομα, μένει ακόμα 1»" : "What the crew said"} />
      <div className="report-buttons">
        <button className="primary" disabled={!text.trim() || send.isPending} onClick={() => send.mutate({ text })}>
          {el ? "Καταχώριση" : "Record"}
        </button>
        {BUTTONS.map(([s, gr, en]) => (
          <button key={s} className="secondary" disabled={send.isPending} onClick={() => send.mutate({ status: s })}>{el ? gr : en}</button>
        ))}
      </div>
      {send.error && <p className="notice">{(send.error as Error).message}</p>}
    </details>
  );
}
