import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { api, type Mission, type MissionPlan } from "../api";
import { Checklist, allChecked } from "../common/Checklist";
import { CopyButton } from "../common/CopyButton";
import { UndoBar } from "../common/UndoBar";
import { useDelayedSend } from "../common/useDelayedSend";
import { ReportForm, ReportList } from "../field/UnitReports";
import { useUi } from "../store";
import { hhmm } from "../time";

const KIND: Record<string, [string, string]> = {
  fire_engine: ["πυροσβεστικό", "fire engine"],
  ambulance: ["ασθενοφόρο", "ambulance"],
};
/** The state as the crew last reported it. Nothing here comes from the clock. */
const PHASE: Record<Mission["phase"], [string, string]> = {
  planned: ["σχεδιασμένη", "planned"],
  assigned: ["ανατέθηκε · αναμονή αναφοράς", "assigned · waiting for the crew"],
  en_route: ["καθ' οδόν", "on the way"],
  on_scene: ["επί τόπου", "on scene"],
  returning: ["επιστρέφει", "returning"],
  available: ["διαθέσιμη", "available"],
  out_of_service: ["βλάβη · εκτός υπηρεσίας", "breakdown · out of service"],
  unknown: ["χωρίς αναφορά", "no report"],
  done: ["ολοκληρώθηκε", "done"],
  recalled: ["ανακλήθηκε", "recalled"],
};
const CHECK_EN: Record<string, string> = {
  place: "the location is right (address and precision on the map)",
  route: "I saw the route and when the front reaches it",
  warnings: "I read the route warnings",
  crew: "I spoke to the crew and they are ready",
};

const CHECK_SHORT: Record<string, [string, string]> = {
  place: ["Η θέση είναι σωστή", "The place is right"], route: ["Είδα τη διαδρομή", "I saw the route"],
  warnings: ["Διάβασα τις προειδοποιήσεις", "I read the warnings"], crew: ["Το πλήρωμα είναι έτοιμο", "The crew is ready"],
};

/** Text for the radio log or a message to the crew: what was sent where, and the brief. */
function card(m: Mission, caseId: string, tz: string, el: boolean): string {
  const i = el ? 0 : 1;
  return [
    `${el ? "ΑΠΟΣΤΟΛΗ" : "MISSION"} ${m.id} · ${m.unit} (${KIND[m.kind][i]}) → ${caseId}`,
    `${el ? "Αναχώρηση" : "Depart"} ${hhmm(m.depart, tz)} · ${el ? "εκτίμηση άφιξης" : "estimated arrival"} ${hhmm(m.arrive, tz)}`,
    m.roads.length ? `${el ? "Μέσω" : "Via"} ${m.roads.join(", ")}` : "",
    m.brief,
    ...m.warnings.map((w) => `! ${w}`),
    `${el ? "Εντολή" : "Ordered by"}: ${m.operator}`,
  ].filter(Boolean).join("\n");
}

export function useMissionPlan(caseId: string, enabled = true) {
  return useQuery({ queryKey: ["mission", caseId], queryFn: () => api.missionPlan(caseId), refetchInterval: 2000, enabled });
}

/** What is already on its way, and what code proposes to send. Information only: the send is in the action bar. */
export function MissionInfo({ caseId, tz, el }: { caseId: string; tz: string; el: boolean }) {
  const i = el ? 0 : 1;
  const qc = useQueryClient();
  const setPlanRoute = useUi((s) => s.setPlanRoute);
  const q = useMissionPlan(caseId);
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["mission", caseId] });
    void qc.invalidateQueries({ queryKey: ["missions"] });
  };
  const recall = useMutation({ mutationFn: (id: string) => api.missionRecall(id, el ? "χειριστής" : "operator"), onSuccess: refresh });
  const d = q.data;
  const plan = d?.plan;
  useEffect(() => { setPlanRoute(d?.can_send && plan ? plan.route : null); }, [d?.can_send, plan?.unit, plan?.route.length]);
  useEffect(() => () => setPlanRoute(null), [caseId]);
  if (!d || !plan) return null;
  if (d.decision !== "DISPATCH_UNIT_CANDIDATE" && d.missions.length === 0) return null;
  const active = d.missions.filter((m) => !["done", "recalled", "planned"].includes(m.phase));
  return (
    <div className="cc-mission">
      {d.missions.map((m) => (
        <div key={m.id} className={`cc-unit ph-${m.phase}`}>
          <p className="cc-unit-head">
            <b>{m.unit}</b> <span className="muted">{KIND[m.kind][i]}</span>
            <span className={`ph ph-${m.phase}`}>{PHASE[m.phase]?.[i] ?? m.phase}</span>
            {["assigned", "en_route"].includes(m.phase) && <span className="muted">{el ? "άφιξη " : "arrives "}{hhmm(m.arrive, tz)}</span>}
          </p>
          {m.brief && (
            <details className="cc-more">
              <summary>{el ? "Κάρτα πληρώματος" : "Crew brief"}</summary>
              <p className="small">{m.brief}</p>
              <CopyButton el={el} label={el ? "Αντιγραφή κάρτας" : "Copy the card"} text={card(m, caseId, tz, el)} />
            </details>
          )}
          <ReportList reports={m.reports} tz={tz} el={el} />
          {active.includes(m) && (
            <div className="cc-unit-acts">
              <ReportForm unit={m.unit} el={el} onDone={refresh} />
              <button className="link" onClick={() => recall.mutate(m.id)} disabled={recall.isPending}>
                {el ? "Ανάκληση μονάδας" : "Recall the unit"}</button>
            </div>
          )}
        </div>
      ))}
      {d.open && d.decision === "DISPATCH_UNIT_CANDIDATE" && <PlanView p={plan} tz={tz} el={el} first={d.missions.length === 0} />}
      {recall.error && <p className="notice">{(recall.error as Error).message}</p>}
    </div>
  );
}

function PlanView({ p, tz, el, first }: { p: MissionPlan; tz: string; el: boolean; first: boolean }) {
  const i = el ? 0 : 1;
  if (p.veto) {
    return (
      <div className="cc-attn-item is-crit">
        <b>{el ? "Καμία μονάδα δεν φτάνει με ασφάλεια" : "No unit gets there safely"}</b>: {p.veto}
        {p.alternatives.includes("sea") && (el ? " Η θέση είναι κοντά στην ακτή: εξέτασε σκάφος (καρτέλα 112 → Λιμενικό)." : " The place is by the shore: consider a boat.")}
      </div>
    );
  }
  if (!p.unit || !p.arrive) return null;
  const late = p.front_min !== null && p.front_min < 0;
  return (
    <div className="cc-plan">
      <p className="cc-plan-unit" title={p.why}>
        {first ? (el ? "Προτεινόμενη μονάδα" : "Proposed unit") : (el ? "Επόμενη διαθέσιμη" : "Next available")}:{" "}
        <b>{p.unit}</b> <span className="muted">{KIND[p.kind][i]} · {p.base}</span>
      </p>
      <p className="cc-plan-eta">
        <b>{el ? "άφιξη" : "arrives"} {hhmm(p.arrive, tz)}</b>
        {p.eta_min !== null && <span className="muted"> ({Math.round(p.eta_min)}′ · {p.distance_km.toLocaleString(el ? "el" : "en")} km)</span>}
        {p.front_min !== null && (
          <span className={late ? "cc-bad" : "cc-good"}>
            {" · "}{late ? (el ? `το μέτωπο φτάνει ${Math.round(-p.front_min)}′ νωρίτερα` : `the front gets there ${Math.round(-p.front_min)}′ earlier`)
              : (el ? `${Math.round(p.front_min)}′ πριν από το μέτωπο` : `${Math.round(p.front_min)}′ before the front`)}
          </span>
        )}
      </p>
      {p.roads.length > 0 && <p className="muted small">{el ? "Μέσω " : "Via "}{p.roads.join(", ")}</p>}
      {p.warnings.map((w) => <p key={w} className="cc-attn-item is-warn small">{w}</p>)}
    </div>
  );
}

/** The send, for the action bar: the checklist, the proposed unit or another one with a reason, and 10″ to undo.
 *  `onSent`: also approve the case, so one press does what the operator means. */
export function MissionSend({ caseId, el, caseVersion, onSent }: {
  caseId: string; el: boolean; caseVersion: number | null; onSent: () => void;
}) {
  const i = el ? 0 : 1;
  const qc = useQueryClient();
  const q = useMissionPlan(caseId);
  const [other, setOther] = useState("");
  const [why, setWhy] = useState("");
  const [checked, setChecked] = useState<string[]>([]);
  const delayed = useDelayedSend();
  const send = useMutation({
    mutationFn: (v: { unit?: string; reason?: string }) =>
      api.missionDispatch(caseId, el ? "χειριστής" : "operator", checked, v.unit, v.reason, caseVersion ?? undefined),
    onSuccess: () => {
      setOther(""); setWhy(""); setChecked([]);
      void qc.invalidateQueries({ queryKey: ["mission", caseId] });
      void qc.invalidateQueries({ queryKey: ["missions"] });
      onSent();
    },
  });
  const d = q.data;
  const plan = d?.plan;
  if (!d || !plan || !d.can_send) return null;
  const ready = allChecked(d.checklist, checked);
  const unit = other || plan.unit;
  return (
    <div className="cc-send">
      <Checklist items={d.checklist ?? {}} en={CHECK_EN} checked={checked} onChange={setChecked} el={el} short={CHECK_SHORT} />
      {plan.options.length > 1 && (
        <label className="small cc-other">{el ? "Μονάδα" : "Unit"}{" "}
          <select value={other} onChange={(e) => setOther(e.target.value)}>
            <option value="">{plan.unit} ({el ? "πρόταση" : "proposal"})</option>
            {plan.options.filter((o) => o.rank > 1).map((o) => (
              <option key={o.unit} value={o.unit}>{o.unit} · {KIND[o.kind]?.[i] ?? o.kind} · {Math.round(o.eta_min)}′</option>
            ))}
          </select>
        </label>
      )}
      {other && (
        <input value={why} maxLength={500} onChange={(e) => setWhy(e.target.value)}
          placeholder={el ? `Γιατί όχι η ${plan.unit}; (υποχρεωτικό)` : `Why not ${plan.unit}? (required)`} />
      )}
      <button className="primary cc-primary" disabled={send.isPending || !ready || !!delayed.pending || (!!other && why.trim().length < 5)}
        onClick={() => delayed.start(el ? `Η ${unit}` : `${unit}`, () => send.mutate(other ? { unit: other, reason: why } : { unit: plan.unit ?? undefined }))}>
        {send.isPending ? (el ? "Γράφεται η κάρτα πληρώματος…" : "Writing the crew brief…")
          : !ready ? (el ? "Τσέκαρε τα παραπάνω για αποστολή" : "Tick the boxes to send")
          : `${el ? "Έγκριση και αποστολή" : "Approve and send"} ${unit}`}
      </button>
      <UndoBar pending={delayed.pending} undo={delayed.undo} el={el} />
      {send.error && <p className="notice">{(send.error as Error).message}</p>}
    </div>
  );
}
